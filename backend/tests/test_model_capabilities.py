import json

import numpy as np
import pytest

from app import detectors, model_capabilities as mc
from app.m6_window_analysis import analyze_window


BASE = {
    "schema": "m6-model-capabilities/1",
    "default": {"object_detection": "dfine-n", "person_reid": "osnet_x0_25",
                "attribute_extraction": "qwen3vl4b", "visual_grounding": "none"},
    "tasks": {"m6.one_hop": {"object_detection": "yolo11n"}},
    "fallbacks": {"object_detection": ["yolo11n"], "person_reid": [],
                  "attribute_extraction": ["qwen3vl"], "visual_grounding": []},
    "models": {"osnet_x0_25": {"capability": "person_reid", "provider": "onnxruntime",
                              "path": "models/reid/osnet_x0_25_msmt17_b1.onnx"}},
}


@pytest.fixture
def config(tmp_path, monkeypatch):
    def write(payload):
        path = tmp_path / "caps.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(mc, "CONFIG_PATH", path)
        mc._CACHE.update({"config_key": None, "config": None, "digest": {}})
        return payload

    return write


def test_default_resolution_uses_registry():
    resolved = mc.resolve("object_detection")
    assert resolved["model"] == "dfine-n"
    assert resolved["provider"] == "onnxruntime"
    assert resolved["task"] == "default"


def test_task_override_wins(config):
    config(BASE)
    assert mc.resolve("object_detection", task="m6.one_hop")["model"] == "yolo11n"
    assert mc.resolve("object_detection")["model"] == "dfine-n"


def test_explicit_model_beats_task(config):
    config(BASE)
    assert mc.resolve("object_detection", task="m6.one_hop", model="dfine-n")["model"] == "dfine-n"


def test_unknown_model_is_rejected(config):
    broken = dict(BASE, default=dict(BASE["default"], object_detection="not-a-model"))
    config(broken)
    with pytest.raises(mc.CapabilityError) as exc:
        mc.resolve("object_detection")
    assert str(exc.value) == "capability_model_unavailable"


def test_unwired_capability_does_not_raise(config):
    config(BASE)
    record = mc.provenance()
    assert record["person_reid"]["model"] == "osnet_x0_25"
    assert len(record["person_reid"]["version"]) == 16
    assert record["visual_grounding"]["error"] == "capability_model_unavailable"


def test_invoke_uses_resolved_model(config, monkeypatch):
    config(BASE)
    calls = []

    def fake(image, model_ids, min_confidence=0.35):
        calls.append(model_ids)
        return {"candidates": [{"label": "人", "box": [0, 0, 10, 10], "score": 0.9}]}

    monkeypatch.setattr(detectors, "detect_flat", fake)
    result = mc.invoke("object_detection", {"image": np.zeros((8, 8, 3), dtype=np.uint8)})
    assert calls == [["dfine-n"]]
    assert result["model"] == "dfine-n"
    assert result["fallback_used"] is False


def test_invoke_falls_back_when_primary_fails(config, monkeypatch):
    config(BASE)
    calls = []

    def fake(image, model_ids, min_confidence=0.35):
        calls.append(model_ids[0])
        if model_ids[0] == "dfine-n":
            raise RuntimeError("engine broken")
        return {"candidates": []}

    monkeypatch.setattr(detectors, "detect_flat", fake)
    result = mc.invoke("object_detection", {"image": np.zeros((8, 8, 3), dtype=np.uint8)})
    assert calls == ["dfine-n", "yolo11n"]
    assert result["model"] == "yolo11n"
    assert result["fallback_used"] is True


def test_analyze_window_asks_capability_layer(tmp_path, monkeypatch):
    import cv2

    for index in range(2):
        cv2.imwrite(str(tmp_path / ("%04d.jpg" % (index + 1))),
                    np.zeros((32, 32, 3), dtype=np.uint8))
    calls = []

    def fake_invoke(capability, payload, task=None, model=None):
        calls.append((capability, model, task))
        return {"candidates": [], "model": model or "dfine-n"}

    monkeypatch.setattr(mc, "invoke", fake_invoke)
    result = analyze_window(str(tmp_path), start_index=0, end_index=1, camera="c",
                            window_id="w", task="m6.one_hop")
    assert [item[0] for item in calls] == ["object_detection", "object_detection"]
    assert all(item[1] for item in calls)
    assert all(item[2] == "m6.one_hop" for item in calls)
    assert result["status"] in ("ok", "detection_missed")


def test_reid_model_path_comes_from_config(config):
    config(BASE)
    from app import m6_person_reid

    path, digest = m6_person_reid._model_path()
    assert path.name == "osnet_x0_25_msmt17_b1.onnx"
    assert len(digest) == 64


def test_environment_can_point_to_another_config(tmp_path, monkeypatch):
    alt = dict(BASE, default=dict(BASE["default"], object_detection="yolo11n"))
    path = tmp_path / "alt.json"
    path.write_text(json.dumps(alt), encoding="utf-8")
    monkeypatch.setenv(mc.CONFIG_ENV, str(path))
    mc._CACHE.update({"config_key": None, "config": None, "digest": {}})
    assert mc.resolve("object_detection")["model"] == "yolo11n"


def test_invoke_respects_pinned_model(config):
    assert mc.resolve("object_detection", model="yolo11n")["model"] == "yolo11n"


def _frames(tmp_path, count=3):
    import cv2

    for index in range(count):
        cv2.imwrite(str(tmp_path / ("%04d.jpg" % (index + 1))),
                    np.zeros((32, 32, 3), dtype=np.uint8))


def test_window_pins_backup_after_first_fallback(tmp_path, monkeypatch):
    _frames(tmp_path)
    calls = []

    def fake_invoke(capability, payload, task=None, model=None):
        calls.append(model)
        if len(calls) == 1:
            return {"candidates": [], "model": "yolo11n", "fallback_used": True}
        return {"candidates": [], "model": model, "fallback_used": False}

    monkeypatch.setattr(mc, "invoke", fake_invoke)
    result = analyze_window(str(tmp_path), start_index=0, end_index=2, camera="c",
                            window_id="w", task="m6.one_hop")
    assert calls[1:] == ["yolo11n", "yolo11n"]
    assert result["detector"]["final"] == "yolo11n"
    assert result["detector"]["events"] == [{"frame_index": 0, "from": "dfine-n", "to": "yolo11n"}]


def test_window_reports_detector_failure_instead_of_silent_empty(tmp_path, monkeypatch):
    _frames(tmp_path, 1)

    def broken(capability, payload, task=None, model=None):
        raise mc.CapabilityError("capability_invoke_failed")

    monkeypatch.setattr(mc, "invoke", broken)
    result = analyze_window(str(tmp_path), start_index=0, end_index=0, camera="c", window_id="w")
    assert result["status"] == "detector_failed"
    assert result["reason"] == "detector_unavailable"
