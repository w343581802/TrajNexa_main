"""M6-S4-2 多视角匹配：视角标注、质量权重、A/B/C 三种比对的差异。"""
import numpy as np

from app.m6_multiview_match import compare, label_views, quality_weight, separated


def _obs(frame, ts, box=(100, 100, 160, 260), conf=0.9):
    return {"frame_id": str(frame), "timestamp_seconds": float(ts), "bbox": list(box), "confidence": conf}


def _pair(obs, view, vec):
    return (obs, view, np.asarray(vec, dtype=np.float32))


def test_label_views_falls_back_to_unknown():
    observations = [_obs(0, 0.0), _obs(1, 1.0), _obs(2, 5.0)]
    labelled = label_views(observations, [{"from": 0.0, "to": 2.0, "view": "front"}])
    assert [v for _, v in labelled] == ["front", "front", "unknown"]


def test_quality_weight_rewards_big_clear_boxes():
    big = quality_weight(_obs(0, 0.0, box=(100, 100, 200, 360), conf=0.95))
    small = quality_weight(_obs(1, 0.0, box=(100, 100, 130, 150), conf=0.95))
    clipped = quality_weight(_obs(2, 0.0, box=(0, 100, 100, 360), conf=0.95))
    assert big > small
    assert clipped < big


def test_separation_blocks_self_scoring():
    anchor = _obs(0, 10.0)
    assert separated(anchor, _obs(1, 10.4), {"min_gap_seconds": 1.0}) is False
    assert separated(anchor, _obs(2, 12.0), {"min_gap_seconds": 1.0}) is True


def test_per_frame_beats_aggregate_on_mixed_views():
    """候选里 2 帧与参考同视角、1 帧是另一视角：聚合会被平均掉，逐帧能留住同视角那两帧。"""
    a = [_pair(_obs(0, 0.0), "front", [1.0, 0.0]), _pair(_obs(1, 0.2), "front", [1.0, 0.0])]
    cand = [_pair(_obs(10, 10.0), "front", [0.95, 0.05]),
            _pair(_obs(11, 10.2), "front", [0.93, 0.07]),
            _pair(_obs(12, 10.4), "back", [0.2, 0.98])]
    agg = compare(a, cand, "aggregate")
    per = compare(a, cand, "per_frame")
    assert per["score"] > agg["score"]


def test_view_weighted_keeps_two_views_in_evidence():
    a = [_pair(_obs(0, 0.0), "front", [1.0, 0.0])]
    cand = [_pair(_obs(10, 10.0), "front", [0.99, 0.01]),
            _pair(_obs(11, 10.4), "front", [0.98, 0.02]),
            _pair(_obs(12, 10.8), "back", [0.80, 0.60])]
    stats = compare(a, cand, "view_weighted", {"top_k": 2})
    assert stats["views_used"] == 2


def test_compare_without_pairs_returns_zero():
    stats = compare([], [], "per_frame")
    assert stats["score"] == 0.0 and stats["pairs"] == 0


def test_supporting_frames_are_frames_not_pairs():
    """锚点 5 帧、候选只有 1 帧：配对数可以很高，但候选支撑帧只有 1，不能算多帧证据。"""
    anchor = [_pair(_obs(i, i * 0.2), "front", [1.0, 0.0]) for i in range(5)]
    cand = [_pair(_obs(50, 20.0), "front", [0.99, 0.01])]
    stats = compare(anchor, cand, "per_frame")
    assert stats["supporting_pairs"] == 5
    assert stats["supporting_anchor_frames"] == 5
    assert stats["supporting_candidate_frames"] == 1


def test_single_frame_share_uses_distinct_candidate_frames():
    """两帧几乎一样好时，不能因为配对多就认为"有独立支撑"。"""
    anchor = [_pair(_obs(i, i * 0.2), "front", [1.0, 0.0]) for i in range(3)]
    cand = [_pair(_obs(50, 20.0), "front", [0.99, 0.01]),
            _pair(_obs(51, 20.2), "front", [0.98, 0.02]),
            _pair(_obs(52, 20.4), "front", [0.30, 0.95])]
    stats = compare(anchor, cand, "per_frame")
    assert stats["supporting_candidate_frames"] >= 2


def test_all_modes_use_the_same_filtered_observations():
    """A 组也必须在时间过滤之后求平均，否则三组输入不是同一批数据。"""
    anchor = [_pair(_obs(0, 10.0), "front", [1.0, 0.0]),   # 与候选取样只差 0.2 s，应被剔除
              _pair(_obs(1, 30.0), "front", [1.0, 0.0])]
    cand = [_pair(_obs(9, 10.2), "front", [1.0, 0.0])]
    for mode in ("aggregate", "per_frame", "view_weighted"):
        stats = compare(anchor, cand, mode)
        assert stats["anchor_frames_used"] == 1, "%s 组没有做同样的时间过滤" % mode
        assert stats["candidate_frames_used"] == 1
