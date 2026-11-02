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
