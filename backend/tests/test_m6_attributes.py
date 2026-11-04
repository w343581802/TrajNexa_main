import json

import numpy as np
import pytest

from app import m4_vision, m6_attributes, m6_person_reid, model_capabilities as mc


BASE = {
    "schema": "m6-model-capabilities/1",
    "default": {"object_detection": "dfine-n", "person_reid": "osnet_x0_25",
                "attribute_extraction": "qwen3vl4b", "visual_grounding": "none"},
    "tasks": {"m6.one_hop": {"object_detection": "dfine-n"}},
    "fallbacks": {"object_detection": ["yolo11n"], "person_reid": [],
                  "attribute_extraction": ["qwen3vl"], "visual_grounding": []},
    "strategies": {"object_detection": "small_only", "person_reid": "small_only",
                   "attribute_extraction": "cascade", "visual_grounding": "observe_only"},
    "sizes": {"attribute_extraction": {"small": "qwen3vl", "large": "qwen3vl4b"}},
    "models": {"osnet_x0_25": {"capability": "person_reid", "provider": "onnxruntime",
                              "path": "models/reid/osnet_x0_25_msmt17_b1.onnx"}},
}


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "caps.json"
    path.write_text(json.dumps(BASE), encoding="utf-8")
    monkeypatch.setattr(mc, "CONFIG_PATH", path)
    mc._CACHE.update({"config_key": None, "config": None, "digest": {}})
    mc.reset_executions()
    return BASE


def _image():
    return np.zeros((64, 64, 3), dtype=np.uint8)


def test_reid_invocation_uses_resolved_weight(config, monkeypatch):
    used = {}

    class Engine:
        def extract_embeddings(self, image, boxes):
            return [[0.0], [[0.1, 0.2, 0.3]]]

    def fake_engine_for(source):
        used["path"] = source
        return Engine(), "deadbeef" * 8

    monkeypatch.setattr(m6_person_reid, "engine_for", fake_engine_for)
    result = mc.invoke("person_reid", {"image": _image(), "box": [1, 1, 20, 30]})
    assert used["path"] == "models/reid/osnet_x0_25_msmt17_b1.onnx"
    assert result["model"] == "osnet_x0_25"
    assert result["weights_digest"] == "deadbeefdeadbeef"


def test_provenance_records_executed_and_fallback(config, monkeypatch):
    from app import detectors

    def fake(image, model_ids, min_confidence=0.35):
        if model_ids[0] == "dfine-n":
            raise RuntimeError("broken")
        return {"candidates": []}

    monkeypatch.setattr(detectors, "detect_flat", fake)
    mc.invoke("object_detection", {"image": _image()})
    record = mc.provenance()
    executed = record["executed"]["object_detection"]
    fallback = next(item for item in executed if item["model"] == "yolo11n")
    assert fallback["fallback_used"] is True
    assert fallback["fallback_from"] == "dfine-n"
    assert fallback["count"] == 1
    assert all(item["model"] != "dfine-n" or item["fallback_used"] for item in executed)
    assert record["strategies"]["attribute_extraction"] == "cascade"


@pytest.mark.parametrize("mode,expected", [
    ("small_only", ["qwen3vl"]),
    ("large_only", ["qwen3vl4b"]),
    ("cascade", ["qwen3vl", "qwen3vl4b"]),
    ("parallel", ["qwen3vl", "qwen3vl4b"]),
    ("observe_only", ["qwen3vl", "qwen3vl4b"]),
])
def test_plan_models_modes(config, mode, expected):
    assert [m["model"] for m in mc.plan_models("attribute_extraction", mode)] == expected


def test_plan_models_rejects_unknown_mode(config):
    with pytest.raises(mc.CapabilityError):
        mc.plan_models("attribute_extraction", "sometimes")


ATTR = {"garment_color": {"value": "浅蓝", "visible": True},
        "garment_type": {"value": "外套", "visible": True},
        "vehicle_color": {"value": "奶白", "visible": True},
        "vehicle_type": {"value": "踏板电动车", "visible": True},
        "companion_present": {"value": "车前有小孩", "visible": True}}


def test_attribute_cascade_stops_when_complete(config, monkeypatch):
    calls = []

    def fake(entry, jpeg):
        calls.append(entry["model"])
        return json.dumps(ATTR, ensure_ascii=False)

    monkeypatch.setattr(m6_attributes, "_chat", fake)
    result = m6_attributes.extract(_image(), [0, 0, 40, 60], mode="cascade")
    assert calls == ["qwen3vl"]
    assert result["results"][0]["attributes"]["garment_color"] == "浅蓝"
    assert result["results"][0]["evidence"]["vehicle_color"] is True
    assert result["identity_effect"] == "none"


def test_attribute_parallel_runs_both_models(config, monkeypatch):
    calls = []

    def fake(entry, jpeg):
        calls.append(entry["model"])
        return json.dumps(ATTR, ensure_ascii=False)

    monkeypatch.setattr(m6_attributes, "_chat", fake)
    result = m6_attributes.extract(_image(), [0, 0, 40, 60], mode="parallel")
    assert calls == ["qwen3vl", "qwen3vl4b"]
    assert len(result["results"]) == 2


def test_attribute_incomplete_json_is_dropped(config, monkeypatch):
    def fake(entry, jpeg):
        return "抱歉，看不清"

    monkeypatch.setattr(m6_attributes, "_chat", fake)
    result = m6_attributes.extract(_image(), [0, 0, 40, 60], mode="small_only")
    assert result["chosen"] is None
    assert result["results"][0]["error"] == "attribute_invalid_json"


def test_external_upload_requires_permission(config, monkeypatch):
    entry = {"model": "remote-vlm", "version": "remote", "provider": "openai_compatible",
             "api_url": "https://example.com/v1"}
    monkeypatch.setattr(mc, "plan_models", lambda capability, mode=None, task=None: [entry])
    with pytest.raises(m6_attributes.AttributeError_) as exc:
        m6_attributes.extract(_image(), [0, 0, 40, 60], mode="small_only")
    assert str(exc.value) == "external_upload_not_allowed"
