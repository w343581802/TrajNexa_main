"""M1c deterministic snapshot and append-only manual binding tests."""
import json
import cv2
import numpy as np
from app.camera_catalog import load_catalog, annotate_camera_report, CatalogError
from app.config import settings
from app.worker import run_once
A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'


def snapshot(path, entries=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'schema_version': '1.0', 'spatial_revision': 'rev-test',
        'cameras': entries if entries is not None else [
            {'camera_uuid': A, 'display_name': '东侧 01', 'aliases': ['测试设备']},
            {'camera_uuid': B, 'display_name': '西侧 02', 'aliases': ['别名B']},
        ]}, ensure_ascii=False), encoding='utf-8')


def report(name, others=None, status='labelled_candidate_unbound'):
    return {'camera': {'overlay_name_raw': name, 'camera_candidates': others or [],
                       'camera_uuid': None, 'match_status': status}}


def test_catalog_absent_and_schema_error(tmp_path):
    try:
        load_catalog(tmp_path/'missing.json')
        assert False
    except CatalogError as e:
        assert str(e) == 'catalog_not_configured'
    path = tmp_path/'camera.json'
    for cameras in [[{'camera_uuid': 'bad', 'display_name': 'A'}],
                    [{'camera_uuid': A, 'display_name': 'A', 'aliases': [12]}],
                    [{'camera_uuid': A, 'display_name': 'A'}, {'camera_uuid': A, 'display_name': 'B'}]]:
        snapshot(path, cameras)
        try:
            load_catalog(path)
            assert False
        except CatalogError:
            pass


def test_exact_alias_and_inactive(tmp_path):
    path = tmp_path/'camera.json'
    snapshot(path)
    r = report('  测试设备  ')
    annotate_camera_report(r, load_catalog(path))
    assert r['camera']['directory_status'] == 'single_candidate_unconfirmed'
    assert r['camera']['camera_uuid'] is None
    assert r['camera']['directory_candidates'][0]['camera_uuid'] == A
    r = report('测试设备公司')
    annotate_camera_report(r, load_catalog(path))
    assert r['camera']['directory_status'] == 'no_exact_match'
    snapshot(path, [{'camera_uuid': A, 'display_name': '东侧 01', 'aliases': ['测试设备'], 'active': False}])
    r = report('测试设备')
    annotate_camera_report(r, load_catalog(path))
    assert r['camera']['directory_candidates'] == []


def test_duplicate_alias_and_conflicting_ocr(tmp_path):
    path = tmp_path/'camera.json'
    snapshot(path, [
        {'camera_uuid': A, 'display_name': '东侧 01', 'aliases': ['同名']},
        {'camera_uuid': B, 'display_name': '西侧 02', 'aliases': ['同名']},
    ])
    cat = load_catalog(path)
    r = report('同名')
    annotate_camera_report(r, cat)
    assert r['camera']['directory_status'] == 'ambiguous_alias'
    assert len(r['camera']['directory_candidates']) == 2
    r = report('东侧 01', ['西侧 02'], 'conflicting_names')
    annotate_camera_report(r, cat)
    assert r['camera']['directory_status'] == 'conflicting_overlay_names'


def test_human_confirm_correct_revoke_keeps_original_report(env):
    client, Session, _ = env
    snapshot(settings.storage_root/'camera_catalog.json')
    assert client.get('/api/v1/cameras/catalog').json()['status'] == 'available'
    image = np.full((60, 80, 3), 100, dtype=np.uint8)
    _, png = cv2.imencode('.png', image)
    media = client.post('/api/v1/media',
                        files={'file': ('t.png', png.tobytes(), 'image/png')}).json()
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        assert run_once(db)
    rsp = client.get(f"/api/v1/jobs/{job['id']}/report").json()
    original = rsp['result_json']
    assert original['camera']['camera_uuid'] is None
    endpoint = f"/api/v1/inspections/{rsp['id']}/camera-bindings"
    assert client.post(endpoint, json={'decision':'confirm','camera_uuid':A,'note':'short'}).status_code == 422
    assert client.post(endpoint, json={'decision':'confirm','camera_uuid':'bogus',
                                       'note':'人工复核录像设备通道来源'}).status_code == 422
    one = client.post(endpoint, json={'decision':'confirm','camera_uuid':A,
                                      'note':'已查看完整录像和导出的通道清单'}).json()
    assert one['decision'] == 'confirm'
    two = client.post(endpoint, json={'decision':'confirm','camera_uuid':B,
                                      'note':'更正：原始录像是另一设备通道'}).json()
    assert two['camera_uuid'] == B
    assert two['matched_from_ocr'] is False
    revoke = client.post(endpoint, json={'decision':'revoke','camera_uuid':None,
                                         'note':'现有信息不足以确认原设备来源'}).json()
    assert revoke['decision'] == 'revoke'
    assert len(client.get(endpoint).json()) == 3
    assert client.get(f"/api/v1/inspections/{rsp['id']}").json()['result_json'] == original
