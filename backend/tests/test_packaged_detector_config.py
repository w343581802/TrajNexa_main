"""P0 guard for the ACTUAL bundled model configuration.

This does not claim weights are installed or real inference succeeds.  It keeps
wrong YOLO -> D-FINE references and COCO class-index shifts out of the demo.
"""
import json
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
CONFIG = BACKEND / "config" / "detectors.json"
LABELS = BACKEND / "config" / "labels" / "coco80_zh.txt"


def test_packaged_coco80_has_full_canonical_order():
    labels = LABELS.read_text(encoding="utf-8").splitlines()
    assert len(labels) == 80
    assert labels[:9] == [
        "人", "自行车", "汽车", "摩托车", "飞机",
        "公交车", "火车", "卡车", "船",
    ]
    assert len(set(labels)) == 80


def test_packaged_detector_models_have_consistent_weight_paths_and_labels():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    specs = config["detectors"]
    assert len(specs) == len({x["id"] for x in specs})
    assert specs, "No reference models configured"
    for spec in specs:
        algo = spec["algorithm_type"].lower()
        weight = spec["model_file"].lower().replace("\\", "/")
        # A YOLO decoder MUST NOT be given a D-FINE checkpoint.
        if algo != "dfine":
            assert "/dfine/" not in weight
            assert "dfine" not in Path(weight).name
        if algo == "dfine":
            assert spec.get("labels_file") == "config/labels/coco80_zh.txt"
            assert "labels" not in spec, "Do not shrink/misorder COCO class indexes"
    # YOLO26s 用端到端（NMS-free）解码契约，与 YOLO8 原头张量不同，必须单独登记
    assert {x["id"] for x in specs} == {"dfine-n", "rfdetr-s", "rfdetr-m", "rfdetr-l", "yolo11n", "yolo26s"}

    end2end = [x for x in specs if x["algorithm_type"] == "yolo_end2end"]
    assert [x["id"] for x in end2end] == ["yolo26s"]
    spec = end2end[0]
    assert spec["model_file"].replace("\\", "/") == "models/yolo/yolo26s.onnx"
    assert spec.get("labels_file") == "config/labels/coco80_zh.txt"
    assert "labels" not in spec, "Do not shrink/misorder COCO class indexes"
    assert 0.2 <= float(spec["conf_threshold"]) <= 0.5


def test_rfdetr_small_is_separate_from_dfine_onnx_contract():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in config["detectors"]}
    dfine = rows["dfine-n"]
    assert dfine["algorithm_type"] == "dfine"
    assert dfine["engine"] == "onnxruntime"
    for model_id, variant, resolution, checkpoint in (
        ("rfdetr-s", "small", 512, "rf-detr-small.pth"),
        ("rfdetr-m", "medium", 576, "rf-detr-medium.pth"),
        ("rfdetr-l", "large", 704, "rf-detr-large-2026.pth"),
    ):
        rfdetr = rows[model_id]
        assert rfdetr["algorithm_type"] == "rfdetr"
        assert rfdetr["engine"] == "rfdetr_pytorch"
        assert rfdetr["model_variant"] == variant
        assert (rfdetr["input_width"], rfdetr["input_height"]) == (resolution, resolution)
        assert rfdetr["model_file"] == "models/rfdetr/" + checkpoint
        assert rfdetr["labels_file"] == "config/labels/coco80_zh.txt"
