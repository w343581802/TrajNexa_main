"""检测器注册表：配置读取、推理路由、保存与回滚（引擎用假实现，不依赖权重）。"""
import json

import numpy as np
import pytest

from app import detectors


class FakeEngine:
    def __init__(self, **kw):
        self.kw = kw
        self.conf_threshold = kw.get("conf_threshold", 0.35)

    def load(self):
        return True

    def detect(self, image):
        return [{"box": [0.1, 0.1, 0.2, 0.2], "label": "人", "score": 0.9},
                {"box": [0.3, 0.3, 0.4, 0.4], "label": "人", "score": 0.91}]


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    labels = tmp_path / "labels.txt"
    labels.write_text("人\n车\n", encoding="utf-8")
    weight = tmp_path / "m.onnx"
    weight.write_bytes(b"not-a-real-weight")
    data = {"detectors": [{
        "id": "m1", "name": "M1", "engine": "onnxruntime", "algorithm_type": "yolo8",
        "task_type": "detect", "device": "cpu", "model_file": str(weight),
        "labels_file": str(labels), "input_width": 640, "input_height": 640,
        "conf_threshold": 0.35, "iou_threshold": 0.5}]}
    path = tmp_path / "detectors.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("M5_DETECTORS_CONFIG", str(path))
    monkeypatch.setattr(detectors.EngineFactory, "create",
                        staticmethod(lambda *a, **kw: FakeEngine(**kw)))
    detectors.reload()
    return path


def test_catalog_reads_conf_and_digest(cfg):
    models = detectors.list_models()
    assert [m["id"] for m in models] == ["m1"]
    assert models[0]["conf_threshold"] == 0.35
    assert models[0]["model_sha256"] == detectors._sha256(cfg.parent / "m.onnx")
    assert "engine_obj" not in models[0]


def test_detect_returns_predictions_and_drops_low_scores(cfg):
    out = detectors.detect(np.zeros((64, 64, 3), np.uint8), ["m1"], 0.95)
    # 假引擎固定返回 0.9/0.91，阈值 0.95 时一个都不应留下
    assert out["models"][0]["predictions"] == []
    out = detectors.detect(np.zeros((64, 64, 3), np.uint8), ["m1"], 0.9)
    assert len(out["models"][0]["predictions"]) == 2
    assert out["models"][0]["predictions"][0]["label"] == "人"


def test_detect_rejects_unknown_engine_and_bad_threshold(cfg):
    with pytest.raises(detectors.DetectorError) as err:
        detectors.detect(np.zeros((8, 8, 3), np.uint8), ["nope"], 0.5)
    assert err.value.code == "invalid_model_selection"
    with pytest.raises(detectors.DetectorError) as err:
        detectors.detect(np.zeros((8, 8, 3), np.uint8), ["m1"], 0.1)
    assert err.value.code == "invalid_threshold"


def test_detect_is_serialized_by_single_lock(cfg):
    assert detectors._infer_lock.acquire(blocking=False)
    try:
        with pytest.raises(detectors.DetectorError) as err:
            detectors.detect(np.zeros((8, 8, 3), np.uint8), ["m1"], 0.5)
        assert err.value.code == "detector_busy"
    finally:
        detectors._infer_lock.release()


def test_update_spec_writes_and_hot_reloads(cfg):
    models = detectors.update_spec("m1", {"conf_threshold": 0.6, "name": "路边相机"})
    assert models[0]["conf_threshold"] == 0.6
    assert models[0]["name"] == "路边相机"
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    assert raw["detectors"][0]["conf_threshold"] == 0.6
    assert raw["detectors"][0]["iou_threshold"] == 0.5  # 未改字段保留


def test_update_spec_rejects_bad_input_and_keeps_file(cfg):
    before = cfg.read_text(encoding="utf-8")
    for bad, code in [({"conf_threshold": 2}, "detector_update_bad_threshold"),
                      ({"name": ""}, "detector_update_bad_name"),
                      ({"model_file": "/etc/passwd"}, "detector_update_unknown_field:model_file"),
                      ({}, "detector_update_empty")]:
        with pytest.raises(detectors.DetectorError) as err:
            detectors.update_spec("m1", bad)
        assert err.value.code == code
    assert cfg.read_text(encoding="utf-8") == before
    with pytest.raises(detectors.DetectorError) as err:
        detectors.update_spec("nope", {"name": "x"})
    assert err.value.code == "detector_not_found"


def test_broken_config_keeps_previous_models(cfg, monkeypatch):
    detectors.list_models()
    cfg.write_text("{ not json", encoding="utf-8")
    with pytest.raises(detectors.DetectorError) as err:
        detectors.reload()
    assert err.value.code == "detector_config_invalid"


def test_create_spec_appends_and_loads(cfg):
    weight = cfg.parent / "m.onnx"
    labels = cfg.parent / "labels.txt"
    models = detectors.create_spec({
        "id": "m2", "name": "第二台", "engine": "onnxruntime",
        "algorithm_type": "yolo8", "task_type": "detect", "device": "cpu",
        "model_file": str(weight), "labels_file": str(labels),
        "conf_threshold": 0.42})
    assert [x["id"] for x in models] == ["m1", "m2"]
    assert models[1]["conf_threshold"] == 0.42
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    assert [d["id"] for d in raw["detectors"]] == ["m1", "m2"]


def test_create_spec_rejects_bad_entries(cfg):
    weight = cfg.parent / "m.onnx"
    base = {"id": "m3", "name": "x", "engine": "onnxruntime", "algorithm_type": "yolo8",
            "task_type": "detect", "device": "cpu", "model_file": str(weight)}
    cases = [
        ({"id": "M3"}, "detector_create_bad_id"),
        ({"id": "m1"}, "detector_create_duplicate_id"),
        ({"engine": "tensorrt"}, "detector_create_bad_engine"),
        ({"algorithm_type": "dinov2"}, "detector_create_bad_algorithm"),
        ({"model_file": str(cfg.parent / "nope.onnx")}, "detector_create_weight_missing"),
        ({"algorithm_type": "dfine", "labels_file": None}, "detector_create_labels_required"),
        ({"device": "tpu"}, "detector_update_bad_device"),
        ({"extra_key": 1}, "detector_create_unknown_field:extra_key"),
    ]
    for patch, code in cases:
        spec = dict(base, **patch)
        if patch.get("labels_file", "x") is None:
            spec.pop("labels_file", None)
        with pytest.raises(detectors.DetectorError) as err:
            detectors.create_spec(spec)
        assert err.value.code == code
    assert [d["id"] for d in json.loads(cfg.read_text(encoding="utf-8"))["detectors"]] == ["m1"]


def test_delete_spec_removes_entry_only(cfg):
    weight = cfg.parent / "m.onnx"
    models = detectors.create_spec({
        "id": "m2", "name": "第二台", "model_file": str(weight),
        "algorithm_type": "yolo8", "conf_threshold": 0.4})
    assert len(models) == 2
    models = detectors.delete_spec("m2")
    assert [x["id"] for x in models] == ["m1"]
    assert weight.is_file()  # 权重文件不删
    with pytest.raises(detectors.DetectorError) as err:
        detectors.delete_spec("m2")
    assert err.value.code == "detector_not_found"


def test_proposals_normalize_and_clamp() -> None:
    """引擎给像素框，契约要归一化坐标；越界裁剪、零面积丢弃、类别映射。"""
    raw = [
        {"box": [64, 48, 192, 144], "label": "人", "score": 0.9, "model_id": "m",
         "model_name": "M", "label_index": 0},
        {"box": [-10, -10, 700, 900], "label": "汽车", "score": 1.8, "model_id": "m",
         "model_name": "M", "label_index": 2},
        {"box": [10, 10, 10, 20], "label": "x", "score": 0.5, "label_index": 5},
        {"box": [0, 0, 1], "label": "bad", "score": 0.5},
    ]
    out = detectors.proposals(raw, (640, 480))
    assert [item["object_class"] for item in out] == ["person", "vehicle"]
    assert out[0]["bbox_normalized"] == [0.1, 0.1, 0.3, 0.3]
    assert out[1]["bbox_normalized"] == [0.0, 0.0, 1.0, 1.0]
    assert out[1]["confidence"] == 1.0
    assert out[0]["status"] == "unverified_detection_proposal"
    assert out[0]["proposal_index"] == 0 and out[0]["model_name"] == "M"


def test_proposals_reject_bad_image_size() -> None:
    with pytest.raises(detectors.DetectorError) as exc:
        detectors.proposals([], (0, 10))
    assert exc.value.code == "detector_invalid_image_size"


def test_update_labels_inline_replaces_file(cfg):
    """支持类别可以直接在页面填：逗号拆分、去重、中文原样，并顶掉标签文件。"""
    models = detectors.update_spec("m1", {"labels": "人, 自行车，汽车,人"})
    assert models[0]["labels"] == ["人", "自行车", "汽车"]
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    assert raw["detectors"][0]["labels"] == ["人", "自行车", "汽车"]
    assert "labels_file" not in raw["detectors"][0]
    # 推理时用的就是新类别表
    out = detectors.detect_flat(np.zeros((64, 64, 3), np.uint8), ["m1"], 0.9)
    assert out["candidates"][0]["label"] == "人"


def test_update_labels_rejects_empty_and_too_many(cfg):
    for bad in ("", " , ，", ["x" * 41], [str(i) for i in range(201)]):
        with pytest.raises(detectors.DetectorError) as err:
            detectors.update_spec("m1", {"labels": bad})
        assert err.value.code == "detector_update_bad_labels"


def test_create_with_inline_labels(cfg):
    models = detectors.create_spec({"id": "m2", "name": "M2", "engine": "onnxruntime",
                                    "algorithm_type": "dfine", "task_type": "detect",
                                    "model_file": str(cfg.parent / "m.onnx"),
                                    "labels": "人,车", "input_width": 640,
                                    "input_height": 640})
    assert [row["id"] for row in models] == ["m1", "m2"]
    assert models[1]["labels"] == ["人", "车"]
