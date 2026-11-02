"""M6-S1-0：BoT-SORT 适配层。语义必须与 ByteTrack 适配层一致。"""
from __future__ import annotations

import pytest

from app.m6_botsort import DEFAULT_BOTSORT_PARAMS, BoTSortAdapter
from app.m6_track import TrackDependencyError, TrackObservation

pytest.importorskip("trackers")
pytest.importorskip("supervision")


def _obs(frame_id, t, box, conf=0.8):
    return TrackObservation(frame_id=frame_id, timestamp_seconds=t,
                            bbox=tuple(float(v) for v in box), confidence=conf)


def _adapter(interval=0.5, **params):
    base = {"max_gap_seconds": 1.5, "max_missing_frames": 3,
            "activation_threshold": 0.2, "high_conf_threshold": 0.25}
    base.update(params)
    return BoTSortAdapter(sample_interval_seconds=interval, params=base)


def test_cmc_is_off_by_default_for_fixed_camera():
    adapter = _adapter()
    described = adapter.describe()
    assert described["enable_cmc"] is False
    assert described["reid"] is None
    assert described["lost_track_buffer"] == 3      # 1.5s / 0.5s，被 max_missing_frames 压到 3


def test_cmc_enabled_is_rejected():
    with pytest.raises(TrackDependencyError):
        _adapter(enable_cmc=True)


def test_thresholds_follow_detection_distribution_not_library_defaults():
    adapter = _adapter()
    described = adapter.describe()
    assert described["track_activation_threshold"] == 0.2
    assert described["high_conf_det_threshold"] == 0.25


def test_single_static_target_keeps_one_track():
    adapter = _adapter()
    for i in range(6):
        adapter.update([_obs("f%d" % i, i * 0.5, [600 + i * 2, 300, 640 + i * 2, 400])])
    ids = [tid for tid, t in adapter._tracks.items() if t.observations]  # noqa: SLF001
    assert len(ids) == 1
    assert len(adapter._tracks[ids[0]].observations) >= 4  # noqa: SLF001


def test_short_gap_is_recovered_not_split():
    adapter = _adapter(max_gap_seconds=1.5, max_missing_frames=3)
    adapter.update([_obs("f0", 0.0, [600, 300, 640, 400])])
    adapter.update([_obs("f1", 0.5, [602, 300, 642, 400])])
    adapter.update([])
    adapter.update([])
    adapter.update([_obs("f4", 2.0, [606, 300, 646, 400])])
    assert len(adapter._tracks) == 1  # noqa: SLF001


def test_stream_gap_rebuilds_tracker_and_keeps_ids_global():
    adapter = _adapter(max_gap_seconds=1.5, max_missing_frames=3)
    adapter.update([_obs("f0", 0.0, [600, 300, 640, 400])])
    adapter.update([_obs("f1", 0.5, [602, 300, 642, 400])])
    adapter.update([_obs("f2", 10.0, [600, 300, 640, 400])])
    adapter.update([_obs("f3", 10.5, [602, 300, 642, 400])])
    assert adapter._session_id == 2  # noqa: SLF001
    old = {t.track_id for t in adapter._tracks.values() if t.session_id == 1}  # noqa: SLF001
    new = {t.track_id for t in adapter._tracks.values() if t.session_id == 2}  # noqa: SLF001
    assert old and new
    assert not (old & new), "断档后不应复用旧轨迹号"


def test_empty_frames_do_not_crash_and_state_stays_consistent():
    adapter = _adapter(max_gap_seconds=1.5, max_missing_frames=2)
    adapter.update([_obs("f0", 0.0, [600, 300, 640, 400])])
    for i in range(1, 4):
        adapter.update([])
    active = adapter.update([])
    assert active == []


def test_default_params_mark_reid_as_absent():
    assert DEFAULT_BOTSORT_PARAMS["enable_cmc"] is False
    assert "reid" not in DEFAULT_BOTSORT_PARAMS
