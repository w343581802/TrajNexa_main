"""M4b preview route uses source hash and model gating; no fake detections."""
from hashlib import sha256
import cv2
import numpy as np

from app import m4_detection
from app.config import settings
from app.models import MediaAsset


def _upload(client):
    case = client.post('/api/v1/cases', json={'name': 'M4b 自动框候选验收'}).json()
    image = np.zeros((120, 200, 3), np.uint8)
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    response = client.post('/api/v1/media', files={
        'file': ('m4b.png', encoded.tobytes(), 'image/png'),
    }, data={'case_id': case['id']})
    assert response.status_code == 201
    return case, response.json()


def _post(client, media_id):
    return client.post(f'/api/v1/media/{media_id}/detections/preview',
                       json={'authorized': True, 'min_confidence': 0.4})


def test_detector_disabled_is_not_fake_empty_detection(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    monkeypatch.setattr(settings, 'm4b_enabled', False)
    _, media = _upload(client)
    result = _post(client, media['id'])
    assert result.status_code == 503
    assert result.json()['error']['code'] == 'm4b-disabled'


def test_detector_model_digest_required(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    monkeypatch.setattr(settings, 'm4b_enabled', True)
    monkeypatch.setattr(settings, 'm4b_model_sha256', '')
    _, media = _upload(client)
    assert _post(client, media['id']).json()['error']['code'] == 'm4b-model-not-installed'


def test_authorization_and_original_sha_guard(env, monkeypatch):
    client, Session, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    monkeypatch.setattr(settings, 'm4b_enabled', True)
    monkeypatch.setattr(settings, 'm4b_model_sha256', 'a'*64)
    _, media = _upload(client)
    # Demo 口径：授权不再是闸门；authorized=False 只会继续走到模型检查（502/503），不再 422
    legacy = client.post(f"/api/v1/media/{media['id']}/detections/preview",
                         json={'authorized': False})
    assert legacy.status_code != 422
    with Session() as db:
        obj = db.get(MediaAsset, media['id'])
        file = settings.storage_root.resolve() / obj.storage_relpath
    file.write_bytes(file.read_bytes() + b'tampered')
    failed = _post(client, media['id'])
    assert failed.status_code == 409
    assert failed.json()['error']['code'] == 'm4b-source-hash-mismatch'


def test_injected_candidate_only_not_written_as_observation(env, monkeypatch):
    client, Session, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    monkeypatch.setattr(settings, 'm4b_enabled', True)
    monkeypatch.setattr(settings, 'm4b_model_sha256', 'a'*64)
    _, media = _upload(client)
    def fake_detector(img, *, model_path, model_sha256, source_sha256, threshold):
        assert img.shape[:2] == (120, 200)
        assert source_sha256 == media['sha256'] and threshold == 0.4
        return {'schema_version':'1.0','status':'candidates_unverified',
                'model_family':'D-FINE','source_sha256':source_sha256,
                'candidates':[{'proposal_index':0,'object_class':'person',
                 'detector_label':'person','confidence':0.87,
                 'bbox_normalized':[0.1,0.2,0.6,0.9],
                 'status':'unverified_detection_proposal'}]}
    monkeypatch.setattr(m4_detection, 'detect_image', fake_detector)
    result = _post(client, media['id'])
    assert result.status_code == 200, result.text
    assert result.json()['case_id'] == media['case_id']
    assert result.json()['candidates'][0]['status'] == 'unverified_detection_proposal'
    assert client.get(f"/api/v1/media/{media['id']}/observations").json() == []


def test_missing_real_model_reports_failure_not_empty_candidates(env, monkeypatch, tmp_path):
    client, _, _ = env
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    monkeypatch.setattr(settings, 'm4b_enabled', True)
    monkeypatch.setattr(settings, 'm4b_model_sha256', 'a'*64)
    monkeypatch.setattr(settings, 'm4b_model_path', tmp_path / 'missing.onnx')
    _, media = _upload(client)
    response = _post(client, media['id'])
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'm4b-model-unavailable'
