"""M5 Rebucca image-only integration: no Rebucca weights or running service."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import m5_rebucca_client
from app.config import settings
from app.m5_rebucca_contract import normalize_detections


def image_bytes():
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    image[20:90, 35:100] = 225
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def owned_image(client):
    case = client.post("/api/v1/cases", json={"name": "Rebucca 图片模型测试"}).json()
    response = client.post("/api/v1/media",
        files={"file": ("source.png", image_bytes(), "image/png")},
        data={"case_id": case["id"]})
    assert response.status_code == 201, response.text
    return case, response.json()


def fake_detection(**overrides):
    base = {
        "candidates": [{
            "proposal_index": 0, "model_id": "car-model", "model_name": "Vehicle Detector",
            "detector_label": "汽车", "object_class": "vehicle",
            "category_unmapped": False, "confidence": .88,
            "bbox_normalized": [.2, .2, .6, .8],
            "status": "unverified_detection_proposal",
        }],
        "model_runs": [{"model_id": "car-model", "status": "returned_unverified",
                        "candidate_count": 1}],
    }
    base.update(overrides)
    return base


def test_rebucca_models_disabled_is_explicit(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "m5_rebucca_enabled", False)
    response = client.get("/api/v1/ai/image-models")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "models": []}


def test_rebucca_image_detection_uses_case_source_and_selected_models(env, monkeypatch):
    client, _, _ = env
    case, media = owned_image(client)
    monkeypatch.setattr(settings, "m5_rebucca_enabled", True)
    calls = {}
    def detect(image, **kwargs):
        calls.update(kwargs)
        assert image.shape == (120, 160, 3)
        return fake_detection()
    monkeypatch.setattr(m5_rebucca_client, "detect_image", detect)
    response = client.post(f"/api/v1/media/{media['id']}/detect",
                           json={"engine": "rebucca", "model_ids": ["car-model"],
                                 "min_confidence": .3})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["case_id"] == case["id"]
    assert data["media_id"] == media["id"]
    assert data["source_sha256"] == media["sha256"]
    assert data["engine"] == "rebucca"
    assert data["candidate_count"] == 1
    assert data["candidates"][0]["model_id"] == "car-model"
    assert data["model_runs"][0]["status"] == "returned_unverified"
    assert calls["model_ids"] == ["car-model"]
    assert calls["min_confidence"] == .3
    # Detection preview never saves an observation. Existing M4a persistence
    # remains a separate explicit user click.
    assert client.get(f"/api/v1/media/{media['id']}/observations").json() == []


def test_rebucca_model_listing_is_sanitized(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "m5_rebucca_enabled", True)
    monkeypatch.setattr(m5_rebucca_client, "list_models", lambda **kw: [{
        "id": "car-model", "name": "Vehicle Detector",
        "engine": "onnxruntime", "model_sha256": "a" * 64,
    }])
    body = client.get("/api/v1/ai/image-models").json()
    assert body["models"][0]["id"] == "car-model"
    assert "model_file" not in str(body)
    assert "token" not in str(body)


def test_rebucca_failed_detection_is_not_mistaken_for_empty_scene(env, monkeypatch):
    client, _, _ = env
    _, media = owned_image(client)
    monkeypatch.setattr(settings, "m5_rebucca_enabled", True)
    def fail(**kwargs):
        raise m5_rebucca_client.RebuccaImageError("m5_service_unavailable")
    monkeypatch.setattr(m5_rebucca_client, "detect_image", lambda *a, **kw: fail(**kw))
    response = client.post(f"/api/v1/media/{media['id']}/detect",
                           json={"engine": "rebucca", "model_ids": ["car-model"]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "m5-service-unavailable"


def test_rebucca_requires_explicit_model_selection_and_case_owned_image(env, monkeypatch):
    client, _, _ = env
    _, media = owned_image(client)
    monkeypatch.setattr(settings, "m5_rebucca_enabled", True)
    response = client.post(f"/api/v1/media/{media['id']}/detect",
                           json={"engine": "rebucca"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "m5-model-selection-required"


def test_local_endpoint_validation():
    assert m5_rebucca_client._endpoint("http://host.docker.internal:8766") == (
        "http://host.docker.internal:8766")
    for url in ("https://example.com:8766", "http://8.8.8.8:8766",
                "http://host.docker.internal:8766/path",
                "http://host.docker.internal:8766/?redirect=1",
                "http://user:pass@127.0.0.1:8766"):
        with pytest.raises(m5_rebucca_client.RebuccaImageError):
            m5_rebucca_client._endpoint(url)


def test_client_maps_unknown_class_to_other_without_guessing(monkeypatch):
    response = {
        "status": "candidates_unverified", "image_size": [160, 120],
        "model_runs": [{"model_id": "vehicle", "status": "returned_unverified",
                        "candidate_count": 1}],
        "candidates": [{
            "model_id": "vehicle", "model_name": "Special Model",
            "label_raw": "电动两轮车", "object_class": "unknown",
            "confidence": .61, "bbox_normalized": [.1, .2, .4, .8],
        }],
    }
    monkeypatch.setattr(m5_rebucca_client, "_request", lambda *a, **k: response)
    image = np.zeros((120, 160, 3), np.uint8)
    result = m5_rebucca_client.detect_image(
        image, model_ids=["vehicle"], min_confidence=.3,
        base_url="http://127.0.0.1:8766", token="", timeout=10)
    row = result["candidates"][0]
    assert row["object_class"] == "other"
    assert row["category_unmapped"] is True
    assert row["detector_label"] == "电动两轮车"


def test_client_rejects_wrong_frame_coordinates(monkeypatch):
    bad = {
        "status": "candidates_unverified", "image_size": [160, 120],
        "model_runs": [{"model_id": "vehicle", "status": "returned_unverified",
                        "candidate_count": 1}],
        "candidates": [{
            "model_id": "vehicle", "model_name": "Detector",
            "label_raw": "汽车", "object_class": "vehicle",
            "confidence": .81, "bbox_normalized": [.9, .2, .4, .8],
        }],
    }
    monkeypatch.setattr(m5_rebucca_client, "_request", lambda *a, **k: bad)
    with pytest.raises(m5_rebucca_client.RebuccaImageError, match="m5_service_bad_response"):
        m5_rebucca_client.detect_image(
            np.zeros((120, 160, 3), np.uint8), model_ids=["vehicle"],
            min_confidence=.3, base_url="http://127.0.0.1:8766",
            token="", timeout=10)


def _image_service_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "rebucca_image_server.py"
    spec = importlib.util.spec_from_file_location("rebucca_image_server_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rebucca_service_calls_existing_engines_and_preserves_model_sources():
    service_module = _image_service_module()
    class FakeEngine:
        def __init__(self, label):
            self.label = label
        def detect(self, frame):
            assert frame.shape == (120, 160, 3)
            return [{"box": [10, 10, 100, 90], "label": self.label, "score": .9}]

    models = [{
        "id": "person", "name": "People", "engine_kind": "onnxruntime",
        "model_sha256": "a" * 64, "engine": FakeEngine("人"),
        "class_map": {"人": "person"},
    }, {
        "id": "vehicle", "name": "Vehicles", "engine_kind": "openvino",
        "model_sha256": "b" * 64, "engine": FakeEngine("汽车"),
        "class_map": {"汽车": "vehicle"},
    }]
    service = service_module.ImageService(models, normalize_detections, cv2, np)
    image = np.zeros((120, 160, 3), np.uint8)
    assert cv2.imencode(".jpg", image)[0]
    _, buffer = cv2.imencode(".jpg", image)
    import base64
    result = service.infer({
        "image_base64": base64.b64encode(buffer.tobytes()).decode("ascii"),
        "model_ids": ["person", "vehicle"], "min_confidence": .2,
    })
    assert result["candidate_count"] == 2
    assert {c["model_id"] for c in result["candidates"]} == {"person", "vehicle"}
    assert all(r["status"] == "returned_unverified" for r in result["model_runs"])


def test_rebucca_service_rejects_unknown_model():
    service_module = _image_service_module()
    service = service_module.ImageService([], normalize_detections, cv2, np)
    with pytest.raises(service_module.ImageServiceError, match="invalid_model_selection"):
        service.infer({"image_base64": "", "model_ids": ["not-installed"]})
