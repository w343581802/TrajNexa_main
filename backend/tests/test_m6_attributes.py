import json

import numpy as np
import pytest

from app import m4_vision, m6_attributes, model_capabilities as mc


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


BOX = [60, 60, 200, 360]


def _image():
    return np.zeros((400, 300, 3), dtype=np.uint8)


PERSON_JSON = json.dumps({"garment_color": {"value": "深灰", "visible": True},
                          "garment_type": {"value": "夹克", "visible": True}})
CONTEXT_JSON = json.dumps({"vehicle_color": {"value": "黑色", "visible": True},
                           "vehicle_type": {"value": "自行车", "visible": True},
                           "companion_present": {"value": "", "visible": False}})


def _fake_chat(calls):
    def fake(entry, prompt, jpeg):
        calls.append((entry["model"], prompt))
        return PERSON_JSON if "行人" in prompt else CONTEXT_JSON

    return fake


def test_prompts_do_not_leak_answers():
    for prompt in (m6_attributes.PROMPT_PERSON, m6_attributes.PROMPT_CONTEXT):
        for leak in ("浅蓝", "奶白", "踏板", "车前", "小孩"):
            assert leak not in prompt


def test_extract_uses_two_crops_and_keeps_sources(config, monkeypatch):
    calls = []
    monkeypatch.setattr(m6_attributes, "_chat", _fake_chat(calls))
    result = m6_attributes.extract(_image(), BOX, mode="cascade")
    assert len(calls) == 2
    assert result["attributes"]["garment_color"] == "深灰"
    assert result["attributes"]["vehicle_type"] == "自行车"
    assert result["attributes"]["companion_present"] is None
    assert result["sources"]["garment_color"]["crop"] == "person"
    assert result["sources"]["vehicle_color"]["crop"] == "context"
    assert result["sources"]["garment_color"]["verification"] == "model_claim"
    person_region = next(s for s in result["scopes"] if s["scope"] == "person")["region"]
    context_region = next(s for s in result["scopes"] if s["scope"] == "context")["region"]
    assert context_region[2] - context_region[0] > person_region[2] - person_region[0]
    assert result["identity_effect"] == "none"


def test_fields_from_wrong_crop_are_dropped(config, monkeypatch):
    def fake(entry, prompt, jpeg):
        if "行人" in prompt:
            return json.dumps({"garment_color": {"value": "浅蓝", "visible": True},
                               "vehicle_color": {"value": "奶白", "visible": True}})
        return "{}"

    monkeypatch.setattr(m6_attributes, "_chat", fake)
    result = m6_attributes.extract(_image(), BOX, mode="small_only")
    assert result["attributes"]["garment_color"] == "浅蓝"
    assert result["attributes"]["vehicle_color"] is None


def test_visible_false_is_unknown_not_absent(config, monkeypatch):
    def fake(entry, prompt, jpeg):
        if "行人" in prompt:
            return json.dumps({"garment_color": {"value": "", "visible": False}})
        return ctx

    ctx = json.dumps({"vehicle_color": {"value": "", "visible": False}})
    monkeypatch.setattr(m6_attributes, "_chat", fake)
    result = m6_attributes.extract(_image(), BOX, mode="cascade")
    assert result["attributes"]["garment_color"] is None
    assert result["evidence"]["garment_color"] is False
    assert "garment_color" not in result["sources"]


def test_invalid_json_is_recorded_not_guessed(config, monkeypatch):
    def fake(entry, prompt, jpeg):
        return "抱歉，看不清"

    monkeypatch.setattr(m6_attributes, "_chat", fake)
    result = m6_attributes.extract(_image(), BOX, mode="small_only")
    assert all(scope["calls"][0]["error"] == "attribute_invalid_json"
               for scope in result["scopes"])


def test_external_upload_requires_permission(config, monkeypatch):
    entry = {"model": "remote-vlm", "version": "remote", "provider": "openai_compatible",
             "api_url": "https://example.com/v1"}
    monkeypatch.setattr(mc, "plan_models", lambda capability, mode=None, task=None: [entry])
    with pytest.raises(m6_attributes.AttributeError_) as exc:
        m6_attributes.extract(_image(), BOX, mode="small_only")
    assert str(exc.value) == "external_upload_not_allowed"
