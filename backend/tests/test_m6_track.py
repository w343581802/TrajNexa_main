"""m6_track（ByteTrack 适配层）确定性测试。

不依赖真实录像：用合成观测验证缺口换算、遮挡恢复、两人交错与证据可追溯性。
跟踪器缺失时跳过（依赖在 backend/requirements-track.txt）。
"""
import numpy as np
import pytest

from app.m6_track import (
    ByteTrackAdapter,
    TrackDependencyError,
    TrackObservation,
    gap_frames_from_seconds,
)

pytest.importorskip("trackers")
pytest.importorskip("supervision")


def _obs(frame_id, t, box, conf=0.8):
    return TrackObservation(frame_id=frame_id, timestamp_seconds=t,
                            bbox=tuple(float(v) for v in box), confidence=conf)


def _adapter(interval=0.5, **params):
    base = {"max_gap_seconds": 1.5, "max_missing_frames": 3,
            "activation_threshold": 0.2, "high_conf_threshold": 0.25}
    base.update(params)
    return ByteTrackAdapter(sample_interval_seconds=interval, params=base)


def test_gap_frames_respect_interval_and_cap():
    assert gap_frames_from_seconds(1.5, 0.25) == 6       # 0.25s 采样，1.5s = 6 帧
    assert gap_frames_from_seconds(1.5, 2.0) == 1        # 2s 采样，1 帧就到 1.5s
    assert gap_frames_from_seconds(20, 0.5, 5) == 5       # 被帧数上限压住


def test_single_static_target_keeps_one_track():
    adapter = _adapter()
    for i in range(6):
        box = [600 + i * 2, 300, 640 + i * 2, 400]
        adapter.update([_obs("f%d" % i, i * 0.5, box)])
    ids = [tid for tid, t in adapter._tracks.items() if t.observations]  # noqa: SLF001
    assert len(ids) == 1
    track = adapter._tracks[ids[0]]  # noqa: SLF001
    # ByteTrack 首帧只做预测不激活，实际从第 2 帧才有轨迹：库行为，不是漏检
    assert len(track.observations) >= 5
    assert track.state in ("active", "lost", "ended")


def test_short_gap_is_recovered_not_split():
    adapter = _adapter(max_gap_seconds=1.5, max_missing_frames=3)
    adapter.update([_obs("f0", 0.0, [600, 300, 640, 400])])
    adapter.update([_obs("f1", 0.5, [602, 300, 642, 400])])
    adapter.update([])                       # 遮挡一帧
    adapter.update([])                       # 再遮挡一帧
    adapter.update([_obs("f4", 2.0, [604, 300, 644, 400])])
    ids = sorted(tid for tid, t in adapter._tracks.items() if len(t.observations) >= 2)  # noqa: SLF001
    assert len(ids) == 1, adapter.tracks_json()
    assert any(e["event"] == "track_lost" for e in adapter.events_json()["events"])


def test_gap_beyond_limit_ends_track():
    adapter = _adapter(max_gap_seconds=1.0, max_missing_frames=2)
    adapter.update([_obs("f0", 0.0, [600, 300, 640, 400])])
    adapter.update([_obs("f1", 0.5, [600, 300, 640, 400])])   # 首帧不激活，需要第 2 帧
    for i in range(2, 6):
        adapter.update([])
    adapter.update([_obs("f9", 6.0, [600, 300, 640, 400])])
    assert any(e["event"] == "track_ended" for e in adapter.events_json()["events"])


def test_time_gap_resets_tracker_instead_of_merging_segments():
    adapter = _adapter(max_gap_seconds=1.0)
    adapter.update([_obs("f0", 0.0, [600, 300, 640, 400])])
    adapter.update([_obs("f1", 0.5, [600, 300, 640, 400])])
    adapter.update([_obs("f2", 120.0, [600, 300, 640, 400])])   # 两分钟后同一位置
    assert any(w.startswith("tracker_reset_after_time_gap") for w in adapter._warnings)  # noqa: SLF001


def test_two_people_crossing_do_not_become_one_track():
    adapter = _adapter(interval=0.25)
    left, right = [500, 300, 540, 400], [800, 300, 840, 400]
    for i in range(8):
        a = [left[0] + i * 12, 300, left[2] + i * 12, 400]
        b = [right[0] - i * 12, 300, right[2] - i * 12, 400]
        adapter.update([_obs("f%d" % i, i * 0.25, a, 0.9), _obs("f%d" % i, i * 0.25, b, 0.85)])
    tracks = [t for t in adapter._tracks.values() if len(t.observations) >= 4]  # noqa: SLF001
    assert len(tracks) >= 2


def test_observations_trace_back_to_detection_index():
    adapter = _adapter()
    obs = [_obs("f0", 0.0, [100, 100, 140, 220]),
           _obs("f1", 0.5, [500, 100, 540, 220])]
    for i, o in enumerate(obs):
        o.detection_index = i
    adapter.update(obs)
    adapter.update([_obs("f2", 1.0, [101, 100, 141, 220])])
    served = [o for t in adapter._tracks.values() for o in t.observations]  # noqa: SLF001
    assert served and all(isinstance(o.detection_index, int) for o in served)


def test_missing_dependency_raises_not_silently_empty(monkeypatch):
    adapter = _adapter()
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "trackers":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(TrackDependencyError):
        adapter.update([_obs("f0", 0.0, [600, 300, 640, 400])])
