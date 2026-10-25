"""M1d camera view check: deterministic synthetic scenes; not an accuracy benchmark."""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from app.view_match import compare_static_scene, check_reference_view, ViewInputError
from app.worker import run_once
from app.config import settings
from app.models import MediaAsset

CAM_A = '11111111-1111-4111-8111-111111111111'
CAM_B = '22222222-2222-4222-8222-222222222222'


def catalog():
    p = settings.storage_root / 'camera_catalog.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({'schema_version': '1.0', 'spatial_revision': 'demo-rev', 'cameras': [
        {'camera_uuid': CAM_A, 'display_name': 'DEMO A', 'aliases': [], 'active': True},
        {'camera_uuid': CAM_B, 'display_name': 'DEMO B', 'aliases': [], 'active': True},
    ]}), encoding='utf-8')
    return p


def scene():
    rng = np.random.default_rng(510)
    arr = rng.integers(10, 246, (480, 720, 3), dtype=np.uint8)
    # Add fake synthetic building edges / scene structures.
    cv2.rectangle(arr, (110, 95), (350, 360), (80, 110, 140), 3)
    cv2.line(arr, (30, 330), (680, 380), (230, 180, 120), 5)
    cv2.putText(arr, 'TEST SCENE', (140, 210), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (250, 250, 250), 4)
    return arr


def image_bytes(arr):
    ok, buff = cv2.imencode('.png', arr)
    assert ok
    return buff.tobytes()


def upload(client, arr):
    res = client.post('/api/v1/media', files={'file': ('fixture.png', image_bytes(arr), 'image/png')})
    assert res.status_code == 201
    return res.json()


def inspect(client, Session, media):
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        assert run_once(db)
    response = client.get(f"/api/v1/jobs/{job['id']}/report")
    assert response.status_code == 200
    return response.json()


def bind(client, inspection_id, camera_uuid=CAM_A):
    response = client.post(f'/api/v1/inspections/{inspection_id}/camera-bindings', json={
        'decision': 'confirm', 'camera_uuid': camera_uuid, 'note': '人工检查原始录像来源确认设备通道',
    })
    assert response.status_code == 201, response.text
    return response.json()


def register(client, media_id, camera_uuid=CAM_A):
    return client.post(f'/api/v1/cameras/{camera_uuid}/view-references', json={
        'media_id': media_id, 'note': '人工确认这张授权测试截图为固定角度的参考场景',
    })


def test_same_scene_correspondence_and_translation_warning():
    a = scene()
    res = compare_static_scene(a, a)
    assert res['status'] == 'reference_view_consistent_candidate', res
    assert res['is_absolute_heading_validation'] is False
    assert res['measurements']['ransac_inliers'] >= 14
    scaled = cv2.resize(a, (360, 240))
    assert compare_static_scene(a, scaled)['status'] == 'reference_view_consistent_candidate'
    cropped = cv2.resize(a, (400, 400))
    assert compare_static_scene(a, cropped)['reason'] == 'aspect_ratio_mismatch'
    b = cv2.warpAffine(a, np.float32([[1, 0, 115], [0, 1, 0]]), (720, 480))
    moved = compare_static_scene(a, b)
    assert moved['status'] == 'possible_view_change', moved
    assert moved['measurements']['median_displacement_fraction'] > 0.09


def test_low_information_and_unrelated_are_not_proven_mismatches():
    uniform = np.full((480, 720, 3), 30, dtype=np.uint8)
    r = compare_static_scene(scene(), uniform)
    assert r['status'] == 'indeterminate'
    assert r['reason'] in ('too_few_background_features', 'insufficient_static_feature_matches')
    assert compare_static_scene(None, scene())['status'] == 'indeterminate'
    random_other = np.random.default_rng(999).integers(10, 246, (480, 720, 3), dtype=np.uint8)
    assert compare_static_scene(scene(), random_other)['status'] == 'indeterminate'


def test_reference_requires_valid_camera_image_and_note(env):
    client, Session, _ = env
    catalog()
    img = upload(client, scene())
    assert register(client, img['id']).status_code == 201
    refs = client.get(f'/api/v1/cameras/{CAM_A}/view-references').json()
    assert len(refs) == 1 and refs[0]['media_sha256'] == img['sha256']
    assert client.post(f'/api/v1/cameras/{CAM_A}/view-references', json={
        'media_id': img['id'], 'note': 'short',
    }).status_code == 422
    assert register(client, img['id'], '00000000-0000-4000-8000-000000000000').status_code == 422
    assert register(client, 'missing').status_code == 422


def test_end_to_end_append_only_and_binding_revocation(env):
    client, Session, _ = env
    catalog()
    ref_media = upload(client, scene())
    observed = upload(client, scene())
    inspection = inspect(client, Session, observed)
    inspection_id = inspection['id']
    before = inspection['result_json']
    r = register(client, ref_media['id']).json()
    endpoint = f'/api/v1/inspections/{inspection_id}/view-checks'
    assert client.post(endpoint, json={'reference_id': r['id']}).status_code == 409
    bind(client, inspection_id)
    first = client.post(endpoint, json={'reference_id': r['id']})
    assert first.status_code == 201, first.text
    data = first.json()
    assert data['result_json']['status'] == 'reference_view_consistent_candidate'
    assert data['camera_uuid'] == CAM_A
    assert data['result_json']['is_absolute_heading_validation'] is False
    assert client.post(endpoint, json={'reference_id': r['id']}).status_code == 201
    assert len(client.get(endpoint).json()) == 2
    assert client.get(f'/api/v1/inspections/{inspection_id}').json()['result_json'] == before
    client.post(f'/api/v1/inspections/{inspection_id}/camera-bindings', json={
        'decision': 'revoke', 'camera_uuid': None, 'note': '更正之前人工确认的绑定信息，来源未能核实',
    })
    assert client.post(endpoint, json={'reference_id': r['id']}).status_code == 409
    assert len(client.get(endpoint).json()) == 2


def test_wrong_camera_and_catalog_change_and_hash_tampering(env):
    client, Session, _ = env
    catalog()
    image = upload(client, scene())
    insp = inspect(client, Session, image)
    ref = register(client, image['id']).json()
    endpoint = f"/api/v1/inspections/{insp['id']}/view-checks"
    bind(client, insp['id'], CAM_B)
    assert client.post(endpoint, json={'reference_id': ref['id']}).json()['error']['code'] == 'camera_binding_mismatch'
    bind(client, insp['id'], CAM_A)
    p = catalog()
    d = json.loads(p.read_text());d['spatial_revision'] = 'new-revision';p.write_text(json.dumps(d))
    assert client.post(endpoint, json={'reference_id': ref['id']}).json()['error']['code'] == 'catalog_revision_changed'
    # Restore original content (including identical whitespace) by using catalog() helper.
    catalog()
    original_path = settings.storage_root/'originals'/f"{image['id']}.png"
    original_path.write_bytes(b'changed outside application')
    assert client.post(endpoint, json={'reference_id': ref['id']}).json()['error']['code'] == 'media_hash_mismatch_or_missing'


def test_video_without_frame_is_indeterminate(tmp_path, monkeypatch):
    # No invented view mismatch when FFmpeg cannot yield a frame.
    path = tmp_path/'sample.mp4';path.write_bytes(b'video bytes fixture')
    ref_path=tmp_path/'ref.png';ref_path.write_bytes(image_bytes(scene()))
    ref=MediaAsset(id='ref',kind='image',sha256=hashlib.sha256(ref_path.read_bytes()).hexdigest(),storage_relpath='ref.png',size_bytes=ref_path.stat().st_size,original_name='r.png',mime_detected='image/png')
    obs=MediaAsset(id='obs',kind='video',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),storage_relpath='sample.mp4',size_bytes=path.stat().st_size,original_name='v.mp4',mime_detected='video/mp4')
    monkeypatch.setattr(settings, 'storage_root', tmp_path)
    result = check_reference_view(ref, obs, {'media': {'technical_metadata': {'duration_seconds':'3'}}})
    assert result['status'] == 'indeterminate'
    assert result['is_absolute_heading_validation'] is False
