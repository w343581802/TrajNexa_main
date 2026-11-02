"""M6-S4-1 目标证据层：质量门槛、取样、比较与排序。"""
import numpy as np

from app.m6_target_evidence import (
    compare_sets,
    rank_tracklets,
    sample_observations,
    usable_observations,
)


def _track(boxes, conf=0.8):
    return {"track_id": 1, "observations": [
        {"frame_id": str(i), "timestamp_seconds": float(i), "bbox": box, "confidence": conf}
        for i, box in enumerate(boxes)
    ]}


def test_quality_gate_keeps_only_large_near_frames():
    track = _track([[0, 0, 40, 100], [0, 0, 60, 160], [0, 0, 80, 200]])
    kept = usable_observations(track)
    assert [o["bbox"][3] - o["bbox"][1] for o in kept] == [160, 200]


def test_quality_gate_rejects_low_confidence():
    track = _track([[0, 0, 80, 200]], conf=0.2)
    assert usable_observations(track) == []


def test_sample_observations_is_time_layered():
    obs = [{"frame_id": str(i)} for i in range(100)]
    picked = sample_observations(obs, 5)
    assert len(picked) == 5
    assert picked[0]["frame_id"] == "0"
    assert picked[-1]["frame_id"] == "80"


def test_compare_sets_identical_vector_is_one():
    vec = np.ones(4, dtype=np.float32) / 2.0
    stats = compare_sets([({"frame_id": "0"}, vec)], [({"frame_id": "1"}, vec)])
    assert abs(stats["cosine_max"] - 1.0) < 1e-6


def test_compare_sets_empty_returns_zero():
    assert compare_sets([], [({"frame_id": "1"}, np.ones(4))])["cosine_max"] == 0.0


def test_rank_tracklets_prefers_higher_similarity():
    anchor = [({"frame_id": "0"}, np.array([1.0, 0.0], dtype=np.float32))]
    vectors = {
        "close": [({"frame_id": "1"}, np.array([0.9, 0.1], dtype=np.float32))],
        "far": [({"frame_id": "2"}, np.array([0.0, 1.0], dtype=np.float32))],
    }
    ranked = rank_tracklets(anchor, list(vectors), vectors)
    assert [r["tracklet_id"] for r in ranked] == ["close", "far"]
    assert ranked[0]["status"] == "candidate"
