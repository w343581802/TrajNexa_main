"""M5 Rebucca's pixel-space detector contract and evidence boundaries."""
import pytest

from app.m5_rebucca_contract import normalize_detections, RebuccaContractError


def test_rebucca_pixel_box_normalization_and_source_model():
    items = normalize_detections(
        [{"box": [64, 36, 320, 324], "label": "car", "score": 0.92}],
        width=640, height=360, model_id="dfine-n", model_name="D-FINE N",
        class_map={"car": "vehicle"})
    assert items == [{
        "candidate_index": 0, "model_id": "dfine-n",
        "model_name": "D-FINE N", "label_raw": "car",
        "object_class": "vehicle", "confidence": 0.92,
        "bbox_normalized": [.1, .1, .5, .9],
        "status": "unverified_detection_proposal",
    }]


def test_unmapped_labels_remain_unknown_not_person_or_vehicle():
    found = normalize_detections(
        [{"box": [0, 0, 100, 100], "label": "电动自行车", "score": .5}],
        width=200, height=200, model_id="custom", model_name="custom")
    assert found[0]["label_raw"] == "电动自行车"
    assert found[0]["object_class"] == "unknown"


def test_overlapping_models_preserve_each_source():
    detection = [{"box": [10, 10, 90, 90], "label": "person", "score": .8}]
    a = normalize_detections(detection, width=100, height=100,
                             model_id="model-a", model_name="model-a")
    b = normalize_detections(detection, width=100, height=100,
                             model_id="model-b", model_name="model-b")
    assert len(a + b) == 2
    assert a[0]["model_id"] != b[0]["model_id"]


def test_invalid_boxes_scores_are_not_evidence():
    items = normalize_detections([
        {"box": [float("nan"), 1, 50, 50], "label": "person", "score": .8},
        {"box": [20, 10, 19, 15], "label": "person", "score": .8},
        {"box": [0, 0, 200, 200], "label": "person", "score": 1.5},
        {"box": [0, 0, 200, 200], "label": "person", "score": .99},
    ], width=100, height=100, model_id="a", model_name="a")
    assert len(items) == 1
    assert items[0]["bbox_normalized"] == [0, 0, 1, 1]


def test_segmentation_polygon_preserved_if_valid():
    found = normalize_detections([{
        "box": [10, 10, 50, 50], "label": "car", "score": .8,
        "mask_polygon": [[10, 10], [50, 10], [50, 50], [10, 50]],
    }], width=100, height=100, model_id="yolo-seg", model_name="YOLO-seg")
    assert found[0]["mask_polygon_normalized"] == [
        [.1, .1], [.5, .1], [.5, .5], [.1, .5]]


@pytest.mark.parametrize("dims", [(0, 100), (100, 0), (True, 100), (100, 30000)])
def test_invalid_size_rejected(dims):
    with pytest.raises(RebuccaContractError, match="m5_invalid_frame_size"):
        normalize_detections([], width=dims[0], height=dims[1], model_id="x", model_name="x")


def test_invalid_class_map_rejected():
    with pytest.raises(RebuccaContractError, match="m5_invalid_class_map"):
        normalize_detections(
            [{"box": [1, 1, 10, 10], "label": "car", "score": .8}],
            width=100, height=100, model_id="x", model_name="x",
            class_map={"car": "confirmed_identity"})
