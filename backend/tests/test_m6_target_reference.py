import numpy as np
import pytest

from app.m6_target_reference import ReferenceUnavailable, extract_reference


def _reader(frame_id):
    return np.zeros((8, 8, 3), dtype="uint8") if frame_id != 99 else None


def _extract(frame_id, bbox):
    if bbox[0] == 13:
        raise RuntimeError("onnx_failed")
    return np.array([1.0, 0.0, 0.0], dtype="float32")


OBS = [{"frame_id": 1, "bbox": [0, 0, 4, 4]},
       {"frame_id": 2, "bbox": [0, 0, 4, 4]},
       {"frame_id": 99, "bbox": [0, 0, 4, 4]},
       {"frame_id": 3, "bbox": [13, 0, 4, 4]}]


def test_reference_uses_only_starting_track_frames():
    ref = extract_reference(_reader, _extract, OBS, model_id="osnet_x0_25_msmt17_b1.onnx",
                            max_frames=2)
    assert [s["frame_id"] for s in ref["sources"]] == [1, 2]
    assert ref["model_id"] == "osnet_x0_25_msmt17_b1.onnx"
    assert ref["failures"] == []
    assert float(np.linalg.norm(ref["vector"])) == pytest.approx(1.0, abs=1e-5)


def test_failures_are_recorded_not_silently_dropped():
    ref = extract_reference(_reader, _extract, OBS, model_id="m", max_frames=4)
    reasons = [f["reason"] for f in ref["failures"]]
    assert reasons == ["frame_unreadable", "extract_failed"]


def test_no_usable_frame_raises():
    with pytest.raises(ReferenceUnavailable):
        extract_reference(_reader, _extract, OBS[2:], model_id="m")
