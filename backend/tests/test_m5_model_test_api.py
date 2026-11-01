"""M5 model test isolation and provider selection (models mocked)."""
import hashlib
import json

import cv2
import numpy as np
import pytest
from sqlalchemy import func, select

from app import detectors, m4_vision
from app.config import settings
from app.models import MediaAsset, TargetObservation
from app.m5_vision_registry import VisionRegistryError, configured_vision_models


def sample():
    image = np.zeros((150, 200, 3), np.uint8)
    image[20:125, 40:160] = 200
    ok, enc = cv2.imencode(".png", image)
    assert ok
    return enc.tobytes()


def post(client, route, **data):
    return client.post("/api/v1/ai/model-test/" + route,
                       files={"file": ("image.png", sample(), "image/png")}, data=data)


def test_detector_compare_no_case_writes(env, monkeypatch):
    client, Session, _ = env
    monkeypatch.setattr(detectors, "enabled", lambda: True)
    chosen = []
    def fake(image, model_ids, min_confidence):
        """引擎给的是像素框 + 标签，接口负责翻成归一化契约。"""
        chosen.extend(model_ids)
        height, width = image.shape[0], image.shape[1]
        return {"candidates": [{"box": [width * .2, height * .2, width * .8, height * .9],
                                "label": "汽车", "score": .9, "model_id": "dfine",
                                "model_name": "D-FINE", "label_index": 2}],
                "model_runs": [{"id": "dfine", "name": "D-FINE", "candidate_count": 1}]}
    monkeypatch.setattr(detectors, "detect_flat", fake)
    r = post(client, "detect", small_model_ids='["dfine"]')
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["test_only"] and d["case_id"] is None
    assert d["source_sha256"] == hashlib.sha256(sample()).hexdigest()
    assert d["candidate_count"] == 1 and chosen == ["dfine"]
    first = d["candidates"][0]
    assert first["object_class"] == "vehicle" and first["detector_label"] == "汽车"
    assert first["bbox_normalized"] == pytest.approx([.2, .2, .8, .9], abs=1e-3)
    assert first["status"] == "unverified_detection_proposal"
    with Session() as session:
        assert session.scalar(select(func.count(MediaAsset.id))) == 0
        assert session.scalar(select(func.count(TargetObservation.id))) == 0


def test_remote_model_catalog_hides_endpoint_key_and_can_run(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "vision_enabled", True)
    monkeypatch.setattr(settings, "m5_vision_models_json", json.dumps([
        {"id": "remote", "name": "Remote VLM", "model": "remote-vision",
         "provider": "openai_compatible", "api_url": "https://api.example.ai/v1",
         "api_key_env": "TEST_REMOTE_API_KEY"}]))
    monkeypatch.setenv("TEST_REMOTE_API_KEY", "secret-value-not-for-browser")
    from app import m5_vision_transport
    monkeypatch.setattr(m5_vision_transport, "remote_chat",
        lambda entry, prompt, jpeg, timeout:
        '{"objects":[{"box":[100,100,600,800],"category":"vehicle","label":"汽车"}]}')
    catalog = client.get("/api/v1/ai/vision-models")
    assert catalog.status_code == 200
    assert "TEST_REMOTE_API_KEY" not in catalog.text
    assert "secret-value-not-for-browser" not in catalog.text
    assert "api.example.ai" not in catalog.text
    assert next(m for m in catalog.json()["models"] if m["id"] == "remote")["location"] == "remote"
    result = post(client, "detect", vision_model_ids='["remote"]')
    assert result.status_code == 200, result.text
    assert result.json()["candidate_count"] == 1
    assert result.json()["candidates"][0]["model_id"] == "remote"


def test_roi_comparison_uses_prompt_and_models(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "vision_enabled", True)
    seen = []
    def fake(image, box, **kw):
        seen.append((kw["model"], kw["object_class"], kw["prompt_override"], box))
        return ["深色车身"]
    monkeypatch.setattr(m4_vision, "describe_roi", fake)
    r = post(client, "roi", vision_model_ids='["qwen3vl","qwen3vl4b"]',
             bbox_normalized="[0.2,0.2,0.8,0.8]", object_class="vehicle",
             prompt_override="只写车身外观")
    assert r.status_code == 200, r.text
    assert len(r.json()["results"]) == 2
    assert all(x["status"] == "returned_unverified" for x in r.json()["results"])
    assert seen[0][1:] == ("vehicle", "只写车身外观", [.2,.2,.8,.8])


@pytest.mark.parametrize("endpoint", [
    "http://example.ai/v1", "http://169.254.169.254/v1",
    "file:///etc/passwd", "https://example.ai/v1?key=secret"])
def test_reject_unsafe_remote_endpoint(monkeypatch, endpoint):
    monkeypatch.setattr(settings, "m5_vision_models_json", json.dumps([
        {"id": "bad", "name": "bad", "model": "x", "provider": "openai_compatible",
         "api_url": endpoint, "api_key_env": "TEST_KEY"}]))
    with pytest.raises(VisionRegistryError):
        configured_vision_models(settings)


def test_invalid_model_selection_rejected(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "vision_enabled", True)
    assert post(client, "detect", vision_model_ids='["not_registered"]').status_code == 422
    assert post(client, "detect", vision_model_ids="[]").status_code == 422
    assert post(client, "roi", vision_model_ids='["qwen3vl"]',
                bbox_normalized="[1,0,0,1]").status_code == 422


def test_remote_only_works_with_ollama_disabled(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "vision_enabled", False)
    monkeypatch.setattr(settings, "m5_vision_models_json", json.dumps([
        {"id": "remote", "name": "Remote", "model": "remote-vision",
         "provider": "openai_compatible", "api_url": "https://api.example.ai/v1",
         "api_key_env": "TEST_REMOTE_API_KEY"}]))
    monkeypatch.setenv("TEST_REMOTE_API_KEY", "fake-for-test")
    from app import m5_vision_transport
    monkeypatch.setattr(m5_vision_transport, "remote_chat",
        lambda entry, prompt, jpeg, timeout:
        '{"objects":[{"box":[100,100,700,700],"label":"红色轿车","category":"vehicle"}]}')
    catalog = client.get("/api/v1/ai/vision-models").json()
    assert catalog["enabled"] is True
    assert next(x for x in catalog["models"] if x["id"] == "remote")["enabled"] is True
    assert next(x for x in catalog["models"] if x["id"] == "qwen3vl")["enabled"] is False
    result = post(client, "detect", vision_model_ids='["remote"]')
    assert result.status_code == 200, result.text
    assert result.json()["candidate_count"] == 1


def test_default_business_detector_ignores_experiment_selection(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(detectors, "enabled", lambda: True)
    monkeypatch.setattr(settings, "m5_workspace_detector_id", "case-model")
    monkeypatch.setattr(detectors, "list_models",
        lambda: [{"id": "case-model"}, {"id": "lab-only"}])
    selected = []
    def fake(image, model_ids, min_confidence):
        selected.extend(model_ids)
        return {"candidates": [], "model_runs": [
            {"id": "case-model", "status": "returned_unverified",
             "candidate_count": 0}]}
    monkeypatch.setattr(detectors, "detect_flat", fake)
    case = client.post("/api/v1/cases", json={"name": "实验与案件分离"}).json()
    asset = client.post("/api/v1/media",
        files={"file": ("image.png", sample(), "image/png")},
        data={"case_id": case["id"]}).json()
    response = client.post("/api/v1/media/" + asset["id"] + "/detect",
                           json={"engine": "auto", "model_ids": ["lab-only"]})
    assert response.status_code == 200, response.text
    assert selected == ["case-model"]
    assert response.json()["effective_profile"] == "workspace_server_default"
