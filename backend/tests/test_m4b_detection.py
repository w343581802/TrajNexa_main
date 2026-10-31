"""M4b ONNX bridge: injected output tests, no model weights or GPU needed."""
from hashlib import sha256
import numpy as np
import cv2
import pytest

from app.m4_detection import (DetectionError, to_onnx_input,
                              parse_detections, verified_image)


def test_letterbox_and_target_size():
    image = np.zeros((360, 640, 3), np.uint8)
    tensor, sizes, geometry = to_onnx_input(image)
    assert tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32
    assert sizes.tolist() == [[640, 640]]
    assert geometry == (1, 0, 140)


def test_official_onnx_outputs_unpadded_as_human_candidates():
    frame = np.zeros((360, 640, 3), np.uint8)
    _, _, geometry = to_onnx_input(frame)
    # First is a person at source pixels (64,36) to (320,324).
    outputs = (np.array([[0, 2, 9]]),
               np.array([[[64, 176, 320, 464], [100, 140, 180, 240],
                          [60, 160, 360, 350]]], dtype=np.float32),
               np.array([[.93, .58, .99]], dtype=np.float32))
    found = parse_detections(outputs, frame.shape, geometry)
    assert len(found) == 2
    assert found[0]['object_class'] == 'person'
    assert found[0]['bbox_normalized'] == [0.1, 0.1, 0.5, 0.9]
    assert found[1]['object_class'] == 'vehicle'
    assert all(p['status'] == 'unverified_detection_proposal' for p in found)


def test_invalid_predictions_discarded():
    frame = np.zeros((640, 640, 3), np.uint8)
    output = (np.array([[0, 0, 0]]),
              np.array([[[np.nan, 0, 200, 200],
                         [1, 1, 5, 5],
                         [-500, 20, 500, 500]]], dtype=np.float32),
              np.array([[.9, .9, .9]]))
    result = parse_detections(output, frame.shape, (1, 0, 0))
    assert len(result) == 1
    assert result[0]['bbox_normalized'][0] == 0


def test_bad_shapes_rejected():
    with pytest.raises(DetectionError, match='m4b_bad_model_outputs'):
        parse_detections([[], [], []], (300, 300, 3), (1, 0, 0))


def test_original_hash_must_match(tmp_path):
    ok, b = cv2.imencode('.png', np.zeros((100, 200, 3), np.uint8))
    assert ok
    file = tmp_path / 'original.png'
    file.write_bytes(b.tobytes())
    assert verified_image(file, sha256(b.tobytes()).hexdigest()).shape[:2] == (100, 200)
    with pytest.raises(DetectionError, match='m4b_source_hash_mismatch'):
        verified_image(file, sha256(b'wrong').hexdigest())


def test_model_is_not_automatically_downloaded(tmp_path):
    from app.m4_detection import _session
    with pytest.raises(DetectionError, match='m4b_model_unavailable'):
        _session(str(tmp_path / 'missing.onnx'), 'a' * 64)
    with pytest.raises(DetectionError, match='m4b_model_sha256_required'):
        _session(str(tmp_path / 'missing.onnx'), '')
