"""RF-DETR contract tests; fake predictor means no large PyTorch weight is needed for CI."""
import numpy as np
import pytest
from types import SimpleNamespace

from app.engines.base import EngineInferenceError
from app.engines.rfdetr_engine import RfdetrEngine


class FakeModel:
    def __init__(self):
        self.received = None
        self.threshold = None

    def predict(self, image, threshold):
        self.received = image.copy()
        self.threshold = threshold
        return SimpleNamespace(
            xyxy=np.array([[1, 2, 16, 18], [0, 0, 5, 6]], dtype=float),
            class_id=np.array([0, 2]),
            confidence=np.array([0.91, 0.3]),
        )


def test_native_predictor_uses_rgb_and_normalized_output_contract():
    labels = ["class-" + str(i) for i in range(80)]
    labels[0], labels[2] = "人", "汽车"
    engine = RfdetrEngine(
        model_file="ignored.pth", labels=labels,
        input_size=(512, 512), conf_threshold=0.35,
    )
    fake = FakeModel()
    engine._model = fake
    engine._loaded = True
    bgr = np.zeros((24, 32, 3), dtype=np.uint8)
    bgr[0, 0] = [1, 2, 3]  # B G R
    out = engine.detect(bgr)
    assert fake.received[0, 0].tolist() == [3, 2, 1]
    assert fake.threshold == 0.35
    assert out == [{"box": [1.0, 2.0, 16.0, 18.0], "label": "人", "score": 0.91}]


def test_missing_weight_is_not_silently_downloaded(tmp_path, monkeypatch):
    from app.engines import rfdetr_engine
    engine = RfdetrEngine(
        model_file=str(tmp_path / "not-found.pth"), labels=["person"] * 80,
        input_size=(512, 512),
    )
    monkeypatch.setattr(rfdetr_engine.RfdetrEngine, "is_available", lambda: True)
    assert engine.load() is False
    assert engine.ready() is False


def test_inference_failure_does_not_look_like_zero_detections():
    engine = RfdetrEngine()
    with pytest.raises(EngineInferenceError, match="rfdetr_not_loaded"):
        engine.detect(np.zeros((16, 16, 3), dtype=np.uint8))
