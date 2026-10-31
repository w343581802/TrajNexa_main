"""M4d: 统一识别入口（D-FINE / Qwen3-VL）与选中框后的关键特征。

视觉模型全部用替身，不访问真实 Ollama；这里验的是接口契约与降级行为。
"""
import cv2
import numpy as np
import pytest

from app import m4_detection, m4_vision
from app.config import settings


def image_bytes():
    img = np.full((180, 240, 3), (65, 95, 145), np.uint8)
    img[30:145, 65:175] = (170, 80, 30)
    ok, encoded = cv2.imencode('.png', img)
    assert ok
    return encoded.tobytes()


def _case_and_media(client):
    case = client.post('/api/v1/cases', json={'name': 'M4d 识别测试'}).json()
    media = client.post('/api/v1/media', files={
        'file': ('frame.png', image_bytes(), 'image/png'),
    }, data={'case_id': case['id']})
    assert media.status_code == 201, media.text
    return case, media.json()


def _candidate(**over):
    item = {'proposal_index': 0, 'object_class': 'vehicle', 'detector_label': '黑色轿车',
            'confidence': None, 'bbox_normalized': [.2, .25, .6, .7],
            'status': 'unverified_vlm_proposal'}
    item.update(over)
    return item


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setattr(settings, 'm4a_enabled', True)
    monkeypatch.setattr(settings, 'm4b_enabled', True)
    monkeypatch.setattr(settings, 'vision_enabled', True)


def test_qwen_detect_returns_unverified_candidates(env, enabled, monkeypatch):
    client, _, _ = env
    _, media = _case_and_media(client)
    calls = {}

    def fake_detect(image, **kwargs):
        calls.update(kwargs)
        return [_candidate()]

    monkeypatch.setattr(m4_vision, 'detect_objects', fake_detect)
    body = client.post(f"/api/v1/media/{media['id']}/detect", json={'engine': 'qwen3vl'})
    assert body.status_code == 200, body.text
    data = body.json()
    assert data['engine'] == 'qwen3vl'
    assert data['status'] == 'candidates_unverified'
    assert data['candidate_count'] == 1
    assert data['candidates'][0]['bbox_normalized'] == [.2, .25, .6, .7]
    assert data['image_size'] == [240, 180]
    # 引擎参数确实透传到视觉层（不是写死一个地址）
    assert calls['model'] == settings.vision_model
    assert calls['base_url'] == settings.vision_base_url


def test_vision_unavailable_maps_to_503(env, enabled, monkeypatch):
    client, _, _ = env
    _, media = _case_and_media(client)

    def boom(*_a, **_k):
        raise m4_vision.VisionError('m4d_model_unavailable')

    monkeypatch.setattr(m4_vision, 'detect_objects', boom)
    response = client.post(f"/api/v1/media/{media['id']}/detect", json={'engine': 'qwen3vl'})
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'm4d-model-unavailable'


def test_unknown_engine_and_missing_dfine_weights(env, enabled, monkeypatch):
    client, _, _ = env
    _, media = _case_and_media(client)
    assert client.post(f"/api/v1/media/{media['id']}/detect",
                       json={'engine': 'yolo'}).status_code == 422
    monkeypatch.setattr(settings, 'm4b_model_sha256', '')
    dfine = client.post(f"/api/v1/media/{media['id']}/detect", json={'engine': 'dfine'})
    assert dfine.status_code == 503
    assert dfine.json()['error']['code'] == 'm4b-model-not-installed'


def test_roi_features_adds_vlm_attributes(env, enabled, monkeypatch):
    client, _, _ = env
    _, media = _case_and_media(client)
    monkeypatch.setattr(m4_vision, 'describe_roi',
                        lambda *a, **k: ['深色车身', '白色牌照'])
    body = client.post(f"/api/v1/media/{media['id']}/roi-features",
                       json={'engine': 'qwen3vl', 'bbox_normalized': [.25, .15, .75, .85]})
    assert body.status_code == 200, body.text
    data = body.json()
    assert data['appearance']['semantic_status'] == 'vlm_unverified'
    assert data['appearance']['semantic_attributes']['features_unverified'] == \
        ['深色车身', '白色牌照']
    # 确定性的统计仍在，模型只是加分项
    assert data['quality']['heuristic_only'] is True
    assert data['appearance']['roi_tones_unverified']
    assert data['linkage']['identity_status'] == 'not_established'


def test_roi_features_degrades_when_model_fails(env, enabled, monkeypatch):
    client, _, _ = env
    _, media = _case_and_media(client)

    def boom(*_a, **_k):
        raise m4_vision.VisionError('m4d_model_empty_response')

    monkeypatch.setattr(m4_vision, 'describe_roi', boom)
    body = client.post(f"/api/v1/media/{media['id']}/roi-features",
                       json={'bbox_normalized': [.25, .15, .75, .85]})
    assert body.status_code == 200, body.text
    data = body.json()
    assert data['appearance']['semantic_status'] == 'unavailable'
    assert data['appearance']['semantic_error'] == 'm4d_model_empty_response'


def test_vision_helpers_tolerate_sloppy_model_output():
    # 围栏 + 解释性文字：取第一个平衡花括号
    assert m4_vision.extract_json('结果如下：\n```json\n{"objects":[]}\n```') == {'objects': []}
    # 0-1000 相对坐标 → 0..1，坏框丢弃
    assert m4_vision._clean_box([0, 0, 500, 500], 1280, 960) == [0.0, 0.0, 0.5, 0.5]
    assert m4_vision._clean_box([0, 0, 5, 5], 1280, 960) is None
    assert m4_vision._clean_box('nope', 1280, 960) is None
    # 只给 label 不给 category 时按关键词兜底
    assert m4_vision.infer_category('黑色轿车') == 'vehicle'
    assert m4_vision.infer_category('pedestrian') == 'person'
    assert m4_vision.infer_category('路障') == 'other'
    # 模型不按 schema 写一句话时切成短语
    assert m4_vision._fragments('白色轿车，车头有蓝色车牌，车身有后视镜。') == \
        ['白色轿车', '车头有蓝色车牌', '车身有后视镜']
    # 同一目标重复框按 IoU 去重，并重新编号
    boxes = [{'proposal_index': 0, 'bbox_normalized': [0, 0, .5, .5]},
             {'proposal_index': 1, 'bbox_normalized': [0, 0, .51, .5]},
             {'proposal_index': 2, 'bbox_normalized': [.5, .5, 1, 1]}]
    kept = m4_vision._dedupe_boxes(boxes)
    assert [item['bbox_normalized'] for item in kept] == [[0, 0, .5, .5], [.5, .5, 1, 1]]
    assert [item['proposal_index'] for item in kept] == [0, 1]


def test_hf_export_outputs_convert_to_official_triplet():
    logits = np.full((1, 3, 80), -10.0)
    logits[0, 0, 2] = 5.0        # car
    logits[0, 1, 0] = 4.0        # person
    boxes = np.array([[[0.5, 0.5, 0.2, 0.4],
                       [0.2, 0.2, 0.1, 0.1],
                       [0.6, 0.6, 0.2, 0.2]]])
    labels, xyxy, scores = m4_detection.parse_hf_outputs(logits, boxes)
    assert labels.tolist() == [2, 0, 0]
    assert scores[0] > 0.99 and scores[1] > 0.98
    # cx,cy,w,h（归一化）× 640 → xyxy 像素
    assert xyxy[0].round(3).tolist() == [256.0, 192.0, 384.0, 448.0]


def test_nms_drops_duplicate_detections():
    a = {'bbox_normalized': [.2, .2, .3, .4], 'confidence': .9}
    b = {'bbox_normalized': [.201, .2, .301, .4], 'confidence': .8}
    c = {'bbox_normalized': [.7, .7, .9, .9], 'confidence': .7}
    assert [item['confidence'] for item in m4_detection._nms([a, b, c])] == [.9, .7]


def test_detect_rejects_out_of_case_media(env, enabled, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(m4_vision, 'detect_objects', lambda *a, **k: [_candidate()])
    orphan = client.post('/api/v1/media', files={
        'file': ('orphan.png', image_bytes(), 'image/png')}).json()
    body = client.post(f"/api/v1/media/{orphan['id']}/detect", json={'engine': 'qwen3vl'})
    assert body.json()['error']['code'] == 'm4-case-image-required'
