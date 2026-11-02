"""Commit E3 轨迹接回规则的确定性测试：只验证"什么时候拒绝"。"""
from scripts.reid_track_link import link_tracks


def _vec(seed):
    """构造确定性的单位向量。"""
    import math
    v = [math.cos(seed), math.sin(seed), 0.0]
    return v


def _track(tid, start, end, count=6, box=(600, 300, 640, 400)):
    obs = []
    for i in range(count):
        obs.append({"timestamp_seconds": start + (end - start) * i / max(1, count - 1),
                    "bbox": list(box), "confidence": 0.8, "frame_id": "f%d" % i})
    return {"track_id": tid, "start_seconds": start, "end_seconds": end, "observations": obs}


PARAMS = {"min_frames": 5, "min_similarity": 0.85, "min_margin": 0.05,
          "max_gap_seconds": 8.0, "max_center_distance_px": 250.0, "px_per_second": 100.0}


def test_identical_templates_link_when_motion_is_consistent():
    feats = {"tracks": [{"track_id": 1, "aggregate": [1.0, 0.0, 0.0]},
                        {"track_id": 2, "aggregate": [1.0, 0.0, 0.0]}]}
    tracks = {"tracks": [_track(1, 10.0, 14.0), _track(2, 15.0, 19.0)]}
    links, rejected = link_tracks(feats, tracks, PARAMS)
    assert len(links) == 1 and links[0]["from"] == 1 and links[0]["to"] == 2


def test_different_templates_are_rejected():
    feats = {"tracks": [{"track_id": 1, "aggregate": [1.0, 0.0, 0.0]},
                        {"track_id": 2, "aggregate": [0.0, 1.0, 0.0]}]}
    tracks = {"tracks": [_track(1, 10.0, 14.0), _track(2, 15.0, 19.0)]}
    links, rejected = link_tracks(feats, tracks, PARAMS)
    assert not links
    assert any(r["reason"] == "similarity_below_threshold" for r in rejected)


def test_ambiguous_successor_is_not_linked():
    feats = {"tracks": [{"track_id": 1, "aggregate": [1.0, 0.0, 0.0]},
                        {"track_id": 2, "aggregate": [1.0, 0.0, 0.0]},
                        {"track_id": 3, "aggregate": [1.0, 0.0, 0.0]}]}
    tracks = {"tracks": [_track(1, 10.0, 14.0), _track(2, 15.0, 19.0), _track(3, 15.2, 19.2)]}
    links, rejected = link_tracks(feats, tracks, PARAMS)
    assert not links, "两个同样好的候选必须拒绝，宁可断轨"
    assert any(r["reason"] == "ambiguous_candidate" for r in rejected)


def test_motion_jump_is_rejected():
    feats = {"tracks": [{"track_id": 1, "aggregate": [1.0, 0.0, 0.0]},
                        {"track_id": 2, "aggregate": [1.0, 0.0, 0.0]}]}
    far = _track(2, 15.0, 19.0, box=(1800, 900, 1840, 1000))   # 画面另一侧
    tracks = {"tracks": [_track(1, 10.0, 14.0), far]}
    links, rejected = link_tracks(feats, tracks, PARAMS)
    assert not links
    assert any(r["reason"] == "motion_inconsistent" for r in rejected)


def test_gap_beyond_limit_is_ignored():
    feats = {"tracks": [{"track_id": 1, "aggregate": [1.0, 0.0, 0.0]},
                        {"track_id": 2, "aggregate": [1.0, 0.0, 0.0]}]}
    tracks = {"tracks": [_track(1, 10.0, 14.0), _track(2, 40.0, 44.0)]}
    links, rejected = link_tracks(feats, tracks, PARAMS)
    assert not links and not rejected   # 超过 max_gap 连候选都不生成


def test_short_track_is_not_used_as_reference():
    feats = {"tracks": [{"track_id": 1, "aggregate": [1.0, 0.0, 0.0]},
                        {"track_id": 2, "aggregate": [1.0, 0.0, 0.0]}]}
    tracks = {"tracks": [_track(1, 10.0, 10.6, count=2), _track(2, 11.0, 15.0)]}
    links, _ = link_tracks(feats, tracks, PARAMS)
    assert not links, "观测数不足的轨迹不得作为接回依据"
