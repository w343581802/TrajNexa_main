import pytest

from app.m6_time_map import (TimeMapError, frame_at, frame_time_map, span_for_window,
                             time_at, validate_map)


def _map(anchor=53997.0, fps=5.0, clock="uncalibrated", offset=0.0):
    return frame_time_map("rec-1-1", "uuid-1-1", anchor, fps, clock_quality=clock,
                          offset_seconds=offset)


def test_same_window_maps_to_different_indices_per_camera():
    fast = _map(anchor=53997.0)
    late = frame_time_map("rec-2-1", "uuid-2-1", 53998.0, 5.0)
    a = span_for_window(fast, 54008.6, 54014.6)
    b = span_for_window(late, 54008.6, 54014.6)
    assert a["start_index"] - b["start_index"] == 5      # 1 秒锚点差 = 5 帧
    # 用错镜头的帧号去解另一路了会得到错误时刻
    assert time_at(b, a["start_index"]) != a["start_seconds"]


def test_time_and_frame_roundtrip():
    m = _map()
    assert time_at(m, 53) == pytest.approx(54007.6, abs=0.01)
    assert frame_at(m, 54007.6) == 53


def test_missing_anchor_stays_relative_only():
    m = frame_time_map("rec-x", "uuid-x", None, 5.0)
    assert time_at(m, 10) is None
    with pytest.raises(TimeMapError):
        frame_at(m, 54000.0)
    assert "no_absolute_anchor_relative_only" in validate_map(m)


def test_offset_shifts_mapping():
    base = _map()
    shifted = _map(offset=1.0)
    assert time_at(shifted, 53) - time_at(base, 53) == pytest.approx(1.0)


def test_uncalibrated_clock_is_not_cross_camera_comparable():
    assert _map()["comparable_across_cameras"] is False
    assert _map(clock="calibrated")["comparable_across_cameras"] is True


def test_window_is_clipped_to_available_frames():
    m = frame_time_map("rec", "uuid", 53997.0, 5.0, frame_count=370)
    span = span_for_window(m, 54070.0, 54099.0)
    assert span["end_index"] == 369
