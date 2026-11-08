"""真值点接口：写进去的文件必须能被指标脚本直接消费，且不接受算法输出当来源。"""
import json

import pytest

from app import m6_projection_metrics as metrics
from app.config import settings

CAMERA = '345f9636-0dff-4fb5-87e9-01d5f1cb9e1d'


@pytest.fixture()
def truth_root(env, monkeypatch, tmp_path):
    root = tmp_path / 'truth'
    monkeypatch.setattr(settings, 'truth_datasets_dir', str(root))
    return root


def body(points=None, **extra):
    payload = {
        'dataset_id': 'pilot-south-gate',
        'camera_uuid': CAMERA,
        'camera_label': '3-DX-中仁路98号-球',
        'time_basis': '录像起点（文件名 OCR）+ 帧秒数',
        'base_time': '2026-10-23T15:00:00',
        'points': points if points is not None else [
            {'id': 'T1', 'lon': 110.21138, 'lat': 25.25662, 'at': '2026-10-23T15:00:05',
             'source': 'map_control_point', 'evidence': '路口停止线'},
            {'id': 'T2', 'lon': 110.21152, 'lat': 25.25670, 'at': '2026-10-23T15:00:12',
             'source': 'map_control_point', 'evidence': '楼角'},
            {'id': 'T3', 'lon': 110.21161, 'lat': 25.25678, 'at': '2026-10-23T15:00:21',
             'source': 'map_control_point', 'evidence': '井盖'},
        ],
    }
    payload.update(extra)
    return payload


def test_save_dataset_writes_file_that_metrics_can_load(truth_root, env):
    client, *_ = env
    response = client.post('/api/v1/spatial/camera-truth/datasets', json=body())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'saved' and data['points'] == 3
    assert data['warnings'] == []
    assert data['sources'] == ['map_control_point']

    path = truth_root / 'pilot-south-gate.json'
    assert path.is_file()
    document = json.loads(path.read_text(encoding='utf-8'))
    # 写进去的东西必须能被指标模块读，否则等于「存下来跑不了」
    loaded = metrics.load_truth(document)
    assert loaded is not None and len(loaded['points']) == 3
    assert path.read_text(encoding='utf-8').count('"at"') == 3


def test_list_reports_dataset_and_marks_template(truth_root, env):
    client, *_ = env
    truth_root.mkdir(parents=True, exist_ok=True)
    (truth_root / 'pilot_camera_truth.example.json').write_text(
        json.dumps({'dataset_id': 'template', 'points': []}), encoding='utf-8')
    assert client.post('/api/v1/spatial/camera-truth/datasets', json=body()).status_code == 200

    listed = client.get('/api/v1/spatial/camera-truth/datasets').json()
    assert listed['counts'] == {'datasets': 2, 'usable': 1}
    template = [item for item in listed['datasets'] if item['template']]
    assert template and template[0]['dataset_id'] == 'template'
    assert listed['allowed_sources'] == list(metrics.TRUSTED_SOURCES)


def test_algorithm_output_is_refused(truth_root, env):
    client, *_ = env
    refused = body(points=[{'id': 'T1', 'lon': 110.21, 'lat': 25.25, 'at': '2026-10-23T15:00:05',
                            'source': 'ray_projection'}])
    response = client.post('/api/v1/spatial/camera-truth/datasets', json=refused)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'truth_source_is_algorithm_output'
    assert not (truth_root / 'pilot-south-gate.json').exists()


def test_unknown_source_is_refused(truth_root, env):
    client, *_ = env
    bad = body(points=[{'id': 'T1', 'lon': 110.21, 'lat': 25.25, 'source': 'guess'}])
    response = client.post('/api/v1/spatial/camera-truth/datasets', json=bad)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'truth_source_not_allowed'


def test_existing_dataset_needs_overwrite(truth_root, env):
    client, *_ = env
    assert client.post('/api/v1/spatial/camera-truth/datasets', json=body()).status_code == 200
    again = client.post('/api/v1/spatial/camera-truth/datasets', json=body())
    assert again.status_code == 409 and again.json()['error']['code'] == 'truth_dataset_exists'
    forced = client.post('/api/v1/spatial/camera-truth/datasets?overwrite=true', json=body())
    assert forced.status_code == 200


def test_time_must_be_naive_local_clock(truth_root, env):
    client, *_ = env
    bad = body(points=[{'id': 'T1', 'lon': 110.21, 'lat': 25.25, 'at': '2026-10-23 15:00:05'}])
    assert client.post('/api/v1/spatial/camera-truth/datasets', json=bad).status_code == 422
    zoned = body(points=[{'id': 'T1', 'lon': 110.21, 'lat': 25.25,
                          'at': '2026-10-23T15:00:05+08:00'}])
    assert client.post('/api/v1/spatial/camera-truth/datasets', json=zoned).status_code == 422


def test_empty_points_and_duplicate_ids(truth_root, env):
    client, *_ = env
    empty = client.post('/api/v1/spatial/camera-truth/datasets', json=body(points=[]))
    assert empty.status_code == 422 and empty.json()['error']['code'] == 'truth_points_missing'
    duplicated = body(points=[
        {'id': 'T1', 'lon': 110.21, 'lat': 25.25},
        {'id': 'T1', 'lon': 110.22, 'lat': 25.26},
    ])
    response = client.post('/api/v1/spatial/camera-truth/datasets', json=duplicated)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'truth_point_duplicate_id'


def test_missing_time_and_evidence_only_warn(truth_root, env):
    client, *_ = env
    response = client.post('/api/v1/spatial/camera-truth/datasets', json=body(points=[
        {'id': 'T1', 'lon': 110.21138, 'lat': 25.25662},
    ]))
    assert response.status_code == 200
    warnings = response.json()['warnings']
    assert 'fewer_than_three_points' in warnings
    assert 'time_missing_on_some_points' in warnings
    assert 'evidence_missing_on_some_points' in warnings


def test_get_dataset_404_and_id_validation(truth_root, env):
    client, *_ = env
    assert client.get('/api/v1/spatial/camera-truth/datasets/missing-set').status_code == 404
    assert client.get('/api/v1/spatial/camera-truth/datasets/BAD ID').status_code == 422
    assert client.post('/api/v1/spatial/camera-truth/datasets',
                       json=body(dataset_id='bad id')).status_code == 422