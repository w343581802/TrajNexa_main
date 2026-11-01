"""Offline M5 chain probe tests: no Rebucca installation or real VLM required."""
import importlib.util
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.m5_rebucca_contract import normalize_detections


def probe():
    script = Path(__file__).resolve().parents[1] / "scripts" / "rebucca_chain_probe.py"
    spec = importlib.util.spec_from_file_location("m5_rebucca_chain_probe_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("url", [
    "https://localhost:11434/v1",
    "http://remote.example:11434/v1",
    "http://192.168.1.40:11434/v1",
    "http://127.0.0.1:11434/other",
    "http://127.0.0.1:11434/v1?secret=x",
    "http://127.0.0.1/v1",
    "http://user:pass@localhost:11434/v1",
])
def test_only_loopback_vlm(url):
    with pytest.raises(ValueError, match="m5_vlm_must_use_local_loopback_v1"):
        probe()._local_llm_endpoint(url)


def test_original_resolution_roi_and_multiple_vlms():
    p = probe()
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[100:260, 100:300] = (255, 255, 255)
    candidates = normalize_detections(
        [{"box": [100, 100, 300, 260], "label": "car", "score": .94}],
        width=640, height=360, model_id="yolo", model_name="YOLO",
        class_map={"car": "vehicle"})

    class FakeVLM:
        called = []
        def __init__(self, base_url, api_key, timeout, provider, model):
            assert base_url == "http://127.0.0.1:11434/v1"
            assert provider == "OpenAI"
            self.model = model
        def infer(self, prompt, jpeg):
            assert jpeg[:2] == bytes([255, 216])
            FakeVLM.called.append((self.model, prompt))
            return '{"features":["白色车身"]}'

    vlms = [
        {"id": "4b", "model": "qwen4b", "prompt": "只描述车身",
         "categories": ["vehicle"]},
        {"id": "other", "model": "vision-b", "prompt": "描述外观",
         "categories": ["vehicle"]},
    ]
    results = p._review_frame(image, candidates, vlms, FakeVLM, cv2, 2, 1)
    assert len(results) == 2
    assert {x["vlm_id"] for x in results} == {"4b", "other"}
    assert all(x["status"] == "unverified_vlm_candidate" for x in results)
    assert all(x["detector_model_id"] == "yolo" for x in results)
    assert all(x["roi_sha256"] for x in results)


def test_detector_models_loaded_through_rebucca_engine_factory(tmp_path):
    p = probe()
    model_file = tmp_path / "weight.onnx"
    model_file.write_bytes(b"placeholder test weight")

    class FakeEngine:
        def load(self):
            return True

    class FakeFactory:
        calls = []
        @staticmethod
        def create(engine_name, **kwargs):
            FakeFactory.calls.append((engine_name, kwargs))
            return FakeEngine()

    config = {"detectors": [{
        "id": "vehicle-dfine", "name": "D-FINE", "model_file": str(model_file),
        "algorithm_type": "dfine", "inference_engine": "onnxruntime",
        "labels": ["person", "car"],
        "class_map": {"car": "vehicle"},
    }]}
    models = p._load_detectors(config, tmp_path, FakeFactory)
    assert models[0]["id"] == "vehicle-dfine"
    assert len(models[0]["model_sha256"]) == 64
    assert FakeFactory.calls[0][1]["algorithm_type"] == "dfine"
    assert FakeFactory.calls[0][1]["labels"] == ["person", "car"]


def test_dfine_requires_original_label_order(tmp_path):
    p = probe()
    (tmp_path / "weight.onnx").write_bytes(b"dummy")
    config = {"detectors": [{"id": "d", "model_file": "weight.onnx",
                             "algorithm_type": "dfine"}]}
    with pytest.raises(ValueError, match="m5_dfine_requires_ordered_class_labels"):
        p._load_detectors(config, tmp_path, object())
