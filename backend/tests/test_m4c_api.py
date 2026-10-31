"""M4c API integration: case-owned clip, no mutation and no fake tracklet."""
import numpy as np
import pytest
from uuid import uuid4

from app import m4_video, m4_detection
from app.config import settings
from app.models import RecordingClip, RecordingSource


@pytest.fixture()
def sample_clip(env, monkeypatch, tmp_path):
    client, Session, _ = env
    monkeypatch.setattr(settings, 'm4c_enabled', True)
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    monkeypatch.setattr(settings, 'm4b_enabled', False)
    root=tmp_path/'videos'
    (root/'day').mkdir(parents=True)
    clip_file=root/'day'/'test.mp4'
    clip_file.write_bytes(b'video-fixture-readonly')
    stat=clip_file.stat()
    monkeypatch.setattr(settings, 'recordings_root', root)
    case_response=client.post('/api/v1/cases',json={'name':'M4c 录像观测测试'})
    assert case_response.status_code==201
    case_id=case_response.json()['id']
    source_id=str(uuid4()); clip_id=str(uuid4())
    with Session() as db:
        db.add(RecordingSource(id=source_id,case_id=case_id,path='day',
                               label='day',auto_scan=False,last_status='succeeded'))
        db.flush()
        db.add(RecordingClip(
            id=clip_id,source_id=source_id,relpath='day/test.mp4',
            file_name='test.mp4',size_bytes=stat.st_size,mtime_ns=stat.st_mtime_ns,
            duration_seconds=120,start_local='2026-10-23T15:00:00',
            start_source='filename',camera_uuid=None,match_status='unscanned',
            ocr_status='disabled'))
        db.commit()
    return client, Session, case_id, clip_id, clip_file


def route(case_id,clip_id):
    return f'/api/v1/cases/{case_id}/recordings/clips/{clip_id}/keyframes/preview'


def test_enabled_case_isolation_and_authorization(sample_clip):
    client,_,case_id,clip_id,_=sample_clip
    url=route(case_id,clip_id)
    denied=client.post(url,json={'offsets_seconds':[.5],'authorized':False})
    assert denied.status_code==422
    wrong_case=client.post(route('another-case',clip_id),
                           json={'offsets_seconds':[.5],'authorized':True})
    assert wrong_case.status_code==404
    assert wrong_case.json()['error']['code']=='m4c-clip-not-found'


def test_sampling_uses_shared_extractor_and_does_not_write(sample_clip,monkeypatch):
    client,Session,case_id,clip_id,path=sample_clip
    calls=[]
    def shared(file,*,offsets):
        calls.append((file,offsets))
        return np.full((120,240,3),125,np.uint8)
    monkeypatch.setattr(m4_video.overlay_identity,'grab_evidence_frame',shared)
    data=client.post(route(case_id,clip_id),
                     json={'authorized':True,'offsets_seconds':[.5,2,8]})
    assert data.status_code==200,data.text
    body=data.json()
    assert len(body['frames'])==3
    assert [f['requested_offset_seconds'] for f in body['frames']]==[.5,2,8]
    assert len(calls)==3
    assert body['tracklet_status']=='not_established'
    assert body['absolute_frame_time_verified'] is False
    assert body['start_time_source']=='filename'
    assert body['source_file_fingerprint']['content_sha256_verified'] is False
    assert body['camera_uuid_candidate'] is None
    assert path.read_bytes()==b'video-fixture-readonly'


def test_preview_image_returns_bounded_jpeg(sample_clip,monkeypatch):
    client,_,case_id,clip_id,_=sample_clip
    monkeypatch.setattr(m4_video.overlay_identity,'grab_evidence_frame',
                        lambda path,offsets:np.full((120,240,3),127,np.uint8))
    url=f'/api/v1/cases/{case_id}/recordings/clips/{clip_id}/keyframes/image'
    response=client.get(url,params={'authorized':'true','offset_seconds':2})
    assert response.status_code==200
    assert response.headers['content-type']=='image/jpeg'
    assert response.content[:3]==b'\xff\xd8\xff'
    assert response.headers['cache-control']=='private, no-store'
    assert response.headers['x-frame-time-semantics']=='requested-seek-offset-not-verified-pts'


def test_changed_clip_requires_rescan(sample_clip):
    client,_,case_id,clip_id,path=sample_clip
    path.write_bytes(b'changed-during-retention')
    response=client.post(route(case_id,clip_id),
                         json={'authorized':True,'offsets_seconds':[.5]})
    assert response.status_code==409
    assert response.json()['error']['code']=='m4c-clip-changed-rescan-required'


def test_invalid_offsets_and_detector_gate(sample_clip):
    client,_,case_id,clip_id,_=sample_clip
    base={'authorized':True}
    for offsets in ([1,1],[-1],[121], [float('nan')], [1]*7):
        resp=client.post(route(case_id,clip_id),json={**base,'offsets_seconds':offsets})
        assert resp.status_code==422
    resp=client.post(route(case_id,clip_id),
                     json={**base,'offsets_seconds':[.5],
                           'include_detection':True})
    assert resp.status_code==503
    assert resp.json()['error']['code']=='m4c-detector-not-configured'


def test_no_frame_does_not_claim_no_person(sample_clip,monkeypatch):
    client,_,case_id,clip_id,_=sample_clip
    monkeypatch.setattr(m4_video.overlay_identity,'grab_evidence_frame',
                        lambda path,offsets:None)
    response=client.post(route(case_id,clip_id),
                         json={'authorized':True,'offsets_seconds':[.5]})
    assert response.status_code==200
    row=response.json()['frames'][0]
    assert row['status']=='frame_unavailable' and row['detections'] is None


def test_detector_failure_is_reported_not_hidden(sample_clip,monkeypatch):
    client,_,case_id,clip_id,_=sample_clip
    monkeypatch.setattr(settings,'m4b_enabled',True)
    monkeypatch.setattr(settings,'m4b_model_sha256','a'*64)
    monkeypatch.setattr(m4_video.overlay_identity,'grab_evidence_frame',
                        lambda path,offsets:np.full((120,240,3),127,np.uint8))
    def unavailable(*args,**kwargs):
        raise m4_detection.DetectionError('m4b_model_unavailable')
    monkeypatch.setattr(m4_detection,'detect_image',unavailable)
    resp=client.post(route(case_id,clip_id),
                     json={'authorized':True,'offsets_seconds':[.5],
                           'include_detection':True})
    assert resp.status_code==503
    assert resp.json()['error']['code']=='m4b-model-unavailable'


def test_feature_disabled_by_default(sample_clip,monkeypatch):
    client,_,case_id,clip_id,_=sample_clip
    monkeypatch.setattr(settings,'m4c_enabled',False)
    resp=client.post(route(case_id,clip_id),
                     json={'authorized':True,'offsets_seconds':[.5]})
    assert resp.status_code==503
    assert resp.json()['error']['code']=='m4c-disabled'
