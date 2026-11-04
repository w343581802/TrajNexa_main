import numpy as np
import pytest

from app import m6_attributes, model_capabilities as mc, wevisone_provider as wp


BASE = {
    "schema": "m6-model-capabilities/1",
    "default": {"object_detection": "dfine-n", "person_reid": "osnet_x0_25",
                "attribute_extraction": "qwen3vl4b", "visual_grounding": "wevisone_vl_2b"},
    "tasks": {"m6.one_hop": {"object_detection": "dfine-n"}},
    "fallbacks": {"object_detection": ["yolo11n"], "person_reid": [],
                  "attribute_extraction": ["qwen3vl"], "visual_grounding": []},
    "strategies": {"object_detection": "small_only", "person_reid": "small_only",
                   "attribute_extraction": "cascade", "visual_grounding": "observe_only"},
    "sizes": {"attribute_extraction": {"small": "qwen3vl", "large": "qwen3vl4b"},
              "visual_grounding": {"small": "wevisone_vl_2b", "large": "wevisone_vl_4b"}},
    "models": {
        "osnet_x0_25": {"capability": "person_reid", "provider": "onnxruntime",
                        "path": "models/reid/osnet_x0_25_msmt17_b1.onnx"},
        "wevisone_vl_2b": {"capability": "visual_grounding", "provider": "wevisone_local",
                           "weight": "tencent/WeVisOne-VL-2B"},
        "wevisone_vl_4b": {"capability": "visual_grounding", "provider": "wevisone_local",
                           "weight": "tencent/WeVisOne-VL-4B"},
    },
}


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "caps.json"
    path.write_text(__import__("json").dumps(BASE), encoding="utf-8")
    monkeypatch.setattr(mc, "CONFIG_PATH", path)
    mc._CACHE.update({"config_key": None, "config": None, "digest": {}})
    return BASE


class FakeGenerated:
    response = "<ref>rider</ref><box><100><200><400><700></box>"
    latency_s = 0.42


class FakeEngine:
    def predict(self, image, **kwargs):
        assert kwargs["task"] == "referring"
        assert kwargs["decode_matting"] is False
        return FakeGenerated()


def test_grounding_returns_unverified_boxes(config, monkeypatch):
    monkeypatch.setattr(wp, "installed", lambda: True)
    monkeypatch.setattr(wp, "_load_engine", lambda entry: FakeEngine())
    out = wp.ground(np.zeros((100, 200, 3), dtype=np.uint8), "person riding a two-wheeler",
                    {"model": "wevisone_vl_2b", "version": "tencent/WeVisOne-VL-2B"})
    assert out["status"] == "candidates_unverified"
    assert len(out["boxes"]) == 1
    assert out["boxes"][0]["box_px_xyxy"] == [20, 20, 80, 70]
    assert out["score"] is None
    assert out["identity_established"] is False
    assert out["identity_effect"] == "none"


def test_grounding_reports_absent_without_inventing_boxes(config, monkeypatch):
    class AbsentEngine:
        def predict(self, image, **kwargs):
            class G:
                response = "None"
            return G()

    monkeypatch.setattr(wp, "installed", lambda: True)
    monkeypatch.setattr(wp, "_load_engine", lambda entry: AbsentEngine())
    out = wp.ground(np.zeros((50, 50, 3), dtype=np.uint8), "person",
                    {"model": "wevisone_vl_2b", "version": "tencent/WeVisOne-VL-2B"})
    assert out["status"] == "model_reported_absent"
    assert out["boxes"] == []


def test_missing_package_is_reported_not_silently_empty(config, monkeypatch):
    monkeypatch.setattr(wp, "installed", lambda: False)
    with pytest.raises(wp.WeVisOneError) as exc:
        wp.ground(np.zeros((50, 50, 3), dtype=np.uint8), "person",
                  {"model": "wevisone_vl_2b", "version": "tencent/WeVisOne-VL-2B"})
    assert str(exc.value) == "wevisone_not_installed"


def test_capability_lists_grounding_models_and_plans_observe_only(config):
    models = [item["model"] for item in mc.available("visual_grounding")]
    assert models == ["wevisone_vl_2b", "wevisone_vl_4b"]
    plan = mc.plan_models("visual_grounding", "observe_only")
    assert [item["model"] for item in plan] == ["wevisone_vl_2b", "wevisone_vl_4b"]


def test_invoke_grounding_routes_to_provider(config, monkeypatch):
    monkeypatch.setattr(wp, "installed", lambda: True)
    monkeypatch.setattr(wp, "_load_engine", lambda entry: FakeEngine())
    out = mc.invoke("visual_grounding", {"image": np.zeros((100, 200, 3), dtype=np.uint8),
                                         "query": "person"})
    assert out["model"] == "wevisone_vl_2b"
    assert out["status"] == "candidates_unverified"
    assert out["identity_established"] is False
