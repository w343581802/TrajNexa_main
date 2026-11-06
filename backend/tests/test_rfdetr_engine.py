"""RF-DETR contract tests; fake predictor means no large PyTorch weight is needed for CI."""
import numpy as np
import pytest
from types import SimpleNamespace
import sys

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
    monkeypatch.setattr(rfdetr_engine.RfdetrEngine, "is_available", staticmethod(lambda: True))
    assert engine.load() is False
    assert engine.ready() is False


def test_inference_failure_does_not_look_like_zero_detections():
    engine = RfdetrEngine()
    with pytest.raises(EngineInferenceError, match="rfdetr_not_loaded"):
        engine.detect(np.zeros((16, 16, 3), dtype=np.uint8))


@pytest.mark.parametrize("variant,resolution,expected_class", [
    ("small", 512, "RFDETRSmall"),
    ("medium", 576, "RFDETRMedium"),
    ("large", 704, "RFDETRLarge"),
])
def test_each_variant_uses_matching_official_constructor(tmp_path, monkeypatch,
                                                         variant, resolution, expected_class):
    from app.engines import rfdetr_engine
    checkpoint = tmp_path / (variant + ".pth")
    checkpoint.write_bytes(b"fake-local-weight")
    called = []

    def constructor(name):
        def build(**kwargs):
            called.append((name, kwargs))
            return FakeModel()
        return build

    module = SimpleNamespace(**{
        name: constructor(name) for name in ("RFDETRSmall", "RFDETRMedium", "RFDETRLarge")
    })
    monkeypatch.setitem(sys.modules, "rfdetr", module)
    monkeypatch.setattr(rfdetr_engine.RfdetrEngine, "is_available", staticmethod(lambda: True))
    engine = RfdetrEngine(
        model_variant=variant, model_file=str(checkpoint),
        input_size=(resolution, resolution), task_type="detect",
        labels=["cls" + str(i) for i in range(80)], device="cpu",
    )
    assert engine.load()
    assert called == [(expected_class, {
        "pretrain_weights": str(checkpoint.resolve()), "device": "cpu"
    })]


@pytest.mark.parametrize("variant,size", [
    ("medium", 512), ("large", 576), ("small", 704), ("xlarge", 704),
])
def test_invalid_variant_size_rejected_without_loading(tmp_path, monkeypatch, variant, size):
    from app.engines import rfdetr_engine
    checkpoint = tmp_path / "file.pth"
    checkpoint.write_bytes(b"fake-local-weight")
    monkeypatch.setattr(rfdetr_engine.RfdetrEngine, "is_available", staticmethod(lambda: True))
    engine = RfdetrEngine(model_variant=variant, model_file=str(checkpoint),
                          input_size=(size, size),
                          labels=["class"] * 80)
    assert not engine.load()
    assert not engine.ready()
