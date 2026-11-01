"""M5 menu selection of multiple configured VLMs; no external Ollama required."""
import json

import cv2
import numpy as np
import pytest

from app import m4_vision
from app.config import settings
from app.m5_vision_registry import (
    VisionRegistryError, configured_vision_models, resolve_vision_model,
)


def _media(client):
    case = client.post("/api/v1/cases", json={"name": "M5 视觉模型目录测试"}).json()
    image = np.zeros((120, 180, 3), np.uint8)
    image[12:92, 35:140] = 190
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    response = client.post("/api/v1/media", files={
        "file": ("test.png", buffer.tobytes(), "image/png")},
        data={"case_id": case["id"]})
    assert response.status_code == 201
    return response.json()


def test_default_model_list_is_server_configured(monkeypatch):
    monkeypatch.setattr(settings, "m5_vision_models_json", "")
    entries = configured_vision_models(settings)
    assert [m["id"] for m in entries] == ["qwen3vl", "qwen3vl4b"]
    assert entries[0]["model_id"] == settings.vision_model
    assert entries[1]["model_id"] == settings.vision_model_large
    assert all(m["provider"] == "ollama" for m in entries)
    assert next(m for m in entries if m["default_for_roi"])["model_id"] == settings.vision_feature_model


def test_extra_models_are_allowlisted_and_no_untrusted_model_id(monkeypatch):
    extra = [{"id": "another_vlm", "name": "Another local VLM",
              "model": "local-vision:latest"}]
    monkeypatch.setattr(settings, "m5_vision_models_json", json.dumps(extra))
    assert resolve_vision_model(settings, "another_vlm")["model_id"] == "local-vision:latest"
    with pytest.raises(VisionRegistryError, match="m5_unknown_vision_model"):
        resolve_vision_model(settings, "not-in-catalog")
    with pytest.raises(VisionRegistryError, match="m5_unknown_vision_model"):
        resolve_vision_model(settings, "http://remote.invalid/api")


@pytest.mark.parametrize("invalid", [
    '[{"id":"qwen3vl","name":"Duplicate","model":"foo"}]',
    '[{"id":"foo","name":"Missing model"}]',
    '[{"id":"../escape","name":"Bad ID","model":"bar"}]',
    '{"id":"object-instead-of-list"}',
])
def test_invalid_registry_config_rejected(monkeypatch, invalid):
    monkeypatch.setattr(settings, "m5_vision_models_json", invalid)
    with pytest.raises(VisionRegistryError, match="m5_invalid_vision_model_config"):
        configured_vision_models(settings)


def test_vision_catalog_and_selected_model_are_used_for_image_detection(env, monkeypatch):
    client, _, _ = env
    media = _media(client)
    monkeypatch.setattr(settings, "m4a_enabled", True)
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "m5_vision_models_json", json.dumps([{
        "id": "demo_vlm", "name": "Demo VLM", "model": "demo-image-model:v1"}]))
    catalog = client.get("/api/v1/ai/vision-models")
    assert catalog.status_code == 200
    assert catalog.json()["models"][-1]["id"] == "demo_vlm"
    calls = {}
    def fake_detect(image, **kwargs):
        calls.update(kwargs)
        return [{
            "proposal_index": 0, "object_class": "vehicle", "detector_label": "汽车",
            "confidence": None, "bbox_normalized": [.1, .1, .6, .6],
            "status": "unverified_vlm_proposal",
        }]
    monkeypatch.setattr(m4_vision, "detect_objects", fake_detect)
    response = client.post(f"/api/v1/media/{media['id']}/detect", json={
        "engine": "vlm", "vision_model_id": "demo_vlm"})
    assert response.status_code == 200, response.text
    assert calls["model"] == "demo-image-model:v1"
    assert response.json()["model_family"] == "Demo VLM"
    assert response.json()["candidate_count"] == 1
    assert client.post(f"/api/v1/media/{media['id']}/detect", json={
        "engine": "vlm", "vision_model_id": "unregistered"}).status_code == 422


def test_roi_uses_selected_vision_model_without_silent_fallback(env, monkeypatch):
    client, _, _ = env
    media = _media(client)
    monkeypatch.setattr(settings, "m4a_enabled", True)
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "m5_vision_models_json", json.dumps([{
        "id": "demo_vlm", "name": "Demo VLM", "model": "demo-vision:v1"}]))
    tried = []
    def fake_describe(image, box, **kwargs):
        tried.append(kwargs["model"])
        return ["白色车身"]
    monkeypatch.setattr(m4_vision, "describe_roi", fake_describe)
    response = client.post(f"/api/v1/media/{media['id']}/roi-features", json={
        "bbox_normalized": [.2, .2, .7, .8], "engine": "rebucca",
        "object_class": "vehicle", "vision_model_id": "demo_vlm"})
    assert response.status_code == 200, response.text
    assert tried == ["demo-vision:v1"]
    attributes = response.json()["appearance"]["semantic_attributes"]
    assert attributes["semantic_model"] == "demo-vision:v1"
    assert attributes["object_class"] == "vehicle"
    bad = client.post(f"/api/v1/media/{media['id']}/roi-features", json={
        "bbox_normalized": [.2, .2, .7, .8], "vision_model_id": "unregistered"})
    assert bad.status_code == 422
