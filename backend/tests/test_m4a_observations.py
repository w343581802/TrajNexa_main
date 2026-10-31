"""M4a: manual screenshot ROI, hash safety and case-isolated append-only records."""
from hashlib import sha256

import cv2
import numpy as np
import pytest

from app.config import settings
from app.m4_features import analyze_image_roi, FeatureInputError, VERSION
from app.models import MediaAsset, TargetObservation


def image_bytes():
    img = np.full((180, 240, 3), (65, 95, 145), np.uint8)
    img[30:145, 65:175] = (170, 80, 30)
    ok, encoded = cv2.imencode('.png', img)
    assert ok
    return encoded.tobytes()


def test_roi_features_are_measured_not_identity(tmp_path):
    content = image_bytes()
    path = tmp_path / 'frame.png'
    path.write_bytes(content)
    result = analyze_image_roi(path, sha256(content).hexdigest(), [0.25, 0.15, 0.75, 0.85])
    assert result['method'] == VERSION
    assert result['frame_size'] == [240, 180]
    assert result['bbox_pixels'] == [60, 27, 180, 153]
    assert result['quality']['heuristic_only'] is True
    assert result['appearance']['roi_tones_unverified']
    assert result['appearance']['semantic_attributes'] == {}
    assert result['linkage']['camera_uuid'] is None
    assert result['linkage']['identity_status'] == 'not_established'
    assert result['linkage']['geo_status'] == 'unlocated'


@pytest.mark.parametrize('box', [
    [0, 0, 0, 1], [0, 0, 1.1, 1], [-0.1, 0, 1, 1],
    [float('nan'), 0, .5, .5], [True, 0, 1, 1],
    [0, .9, 1, .1],
])
def test_invalid_bbox_rejected(tmp_path, box):
    path = tmp_path / 'sample.png'
    content = image_bytes()
    path.write_bytes(content)
    with pytest.raises(FeatureInputError, match='m4_invalid_bbox'):
        analyze_image_roi(path, sha256(content).hexdigest(), box)


def test_tiny_roi_and_hash_mismatch_rejected(tmp_path):
    path = tmp_path / 'frame.png'
    content = image_bytes()
    path.write_bytes(content)
    with pytest.raises(FeatureInputError, match='m4_roi_too_small'):
        analyze_image_roi(path, sha256(content).hexdigest(), [0, 0, .03, .03])
    path.write_bytes(content + b'tamper')
    with pytest.raises(FeatureInputError, match='m4_source_hash_mismatch'):
        analyze_image_roi(path, sha256(content).hexdigest(), [.1, .1, .9, .9])


def _case_and_media(client):
    response = client.post('/api/v1/cases', json={'name': 'M4 截图测试'})
    assert response.status_code == 201, response.text
    case = response.json()
    content = image_bytes()
    response = client.post('/api/v1/media', files={
        'file': ('sample.png', content, 'image/png'),
    }, data={'case_id': case['id']})
    assert response.status_code == 201, response.text
    return case, response.json()


def _payload():
    return {'object_class': 'person', 'bbox_normalized': [.25, .15, .75, .85],
            'review_note': '根据原始截图人工框选画面中的对象，不确认身份',
            'authorized': True}


def test_feature_requires_opt_in(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', False)
    _, media = _case_and_media(client)
    response = client.post(f"/api/v1/media/{media['id']}/observations", json=_payload())
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'm4a-disabled'


def test_create_query_case_delete_clears_derivatives_only(env, monkeypatch):
    client, Session, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    case, media = _case_and_media(client)
    url = '/api/v1/media/' + media['id'] + '/observations'
    first = client.post(url, json=_payload())
    assert first.status_code == 201, first.text
    record = first.json()
    assert record['case_id'] == case['id']
    assert record['source_inspection_id'] is None
    assert record['analysis_json']['source_context']['time_verified'] is False
    assert record['analysis_json']['linkage']['camera_uuid'] is None
    second = client.post(url, json=_payload())
    assert second.status_code == 201
    assert second.json()['id'] != record['id']
    assert len(client.get(url).json()) == 2
    assert len(client.get(f"/api/v1/cases/{case['id']}/observations").json()) == 2
    with Session() as db:
        path = settings.storage_root.resolve() / db.get(MediaAsset, media['id']).storage_relpath
    assert path.exists()
    deleted = client.delete('/api/v1/cases/' + case['id'])
    assert deleted.status_code == 200, deleted.text
    assert not path.exists()
    with Session() as db:
        assert db.query(TargetObservation).count() == 0


def test_reject_unowned_or_unapproved_or_invalid_object(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    _, media = _case_and_media(client)
    url = f"/api/v1/media/{media['id']}/observations"
    payload = _payload()
    payload['authorized'] = False
    assert client.post(url, json=payload).status_code == 422
    payload = _payload()
    payload['object_class'] = 'face_id'
    assert client.post(url, json=payload).json()['error']['code'] == 'm4a-invalid-object-class'
    payload = _payload()
    payload['bbox_normalized'] = [.2, .2, .2, .9]
    assert client.post(url, json=payload).status_code == 422
    bad = client.post('/api/v1/media', files={'file': ('orphan.png', image_bytes(), 'image/png')})
    assert bad.status_code == 201
    assert client.post(f"/api/v1/media/{bad.json()['id']}/observations",
                       json=_payload()).json()['error']['code'] == 'm4a-case-image-required'


def test_changed_file_rejected_without_record(env, monkeypatch):
    client, Session, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    _, media = _case_and_media(client)
    with Session() as db:
        item = db.get(MediaAsset, media['id'])
        file = settings.storage_root.resolve() / item.storage_relpath
    file.write_bytes(file.read_bytes() + b'altered')
    result = client.post(f"/api/v1/media/{media['id']}/observations", json=_payload())
    assert result.status_code == 409
    with Session() as db:
        assert db.query(TargetObservation).count() == 0



def test_case_delete_clears_observations_before_referenced_inspection(env, monkeypatch):
    from uuid import uuid4
    from app.models import AnalysisJob, InspectionReport
    client, Session, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    case, media = _case_and_media(client)
    with Session() as db:
        job = AnalysisJob(id=str(uuid4()), media_id=media['id'], module='test-inspector',
                          status='succeeded', attempt=1, progress=100)
        db.add(job)
        db.flush()
        report = InspectionReport(
            id=str(uuid4()), media_id=media['id'], job_id=job.id,
            schema_version='0.1.3', module_version='test', result_json={
                'identity': {'capture_time': '2026-10-23T15:00:00',
                             'time_source': 'ocr', 'camera_name': '仅候选'}
            })
        db.add(report)
        db.commit()
        report_id = report.id
    result = client.post('/api/v1/media/' + media['id'] + '/observations',
                         json=_payload())
    assert result.status_code == 201, result.text
    assert result.json()['source_inspection_id'] == report_id
    assert result.json()['analysis_json']['source_context']['time_verified'] is False
    assert result.json()['analysis_json']['linkage']['camera_uuid'] is None
    deleted = client.delete('/api/v1/cases/' + case['id'])
    assert deleted.status_code == 200, deleted.text
    with Session() as db:
        assert db.query(TargetObservation).count() == 0
        assert db.query(InspectionReport).count() == 0
