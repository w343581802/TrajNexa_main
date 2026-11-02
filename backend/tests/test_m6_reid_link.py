"""M6-S1-1C：外观重关联层（纯逻辑测试，不依赖 ONNX）。"""
from __future__ import annotations

import numpy as np

from app.m6_reid_link import (DEFAULT_PARAMS, candidate_links, link_tracks,
                              merge_track_objects)


def _track(track_id, start, end, box, step=0.2):
    obs = []
    t = start
    while t <= end + 1e-6:
        obs.append({"frame_id": "%05d" % round(t / step), "timestamp_seconds": round(t, 3),
                    "bbox": list(box)})
        t += step
    return {"track_id": track_id, "start_seconds": start, "end_seconds": end,
            "duration_seconds": round(end - start, 3), "observations": obs}


def _gallery(pairs):
    return {tid: vec / np.linalg.norm(vec) for tid, vec in pairs.items()}


def test_links_require_plausible_gap_and_position():
    a = _track(1, 0.0, 1.0, [600, 300, 640, 400])
    far = _track(2, 2.0, 3.0, [1800, 900, 1840, 1000])   # 缺口 1 s，但空间上完全另一处
    gallery = _gallery({1: np.array([1.0, 0.0]), 2: np.array([1.0, 0.0])})
    assert candidate_links([a, far], gallery) == []


def test_link_accepts_same_person_with_high_similarity():
    a = _track(1, 0.0, 1.0, [600, 300, 640, 400])
    b = _track(2, 2.0, 3.0, [604, 302, 644, 402])
    gallery = _gallery({1: np.array([1.0, 0.0]), 2: np.array([0.99, 0.1])})
    result = link_tracks([a, b], gallery)
    assert len(result["links"]) == 1
    assert result["merge_map"] == {2: 1}


def test_low_similarity_is_not_linked():
    a = _track(1, 0.0, 1.0, [600, 300, 640, 400])
    b = _track(2, 2.0, 3.0, [604, 302, 644, 402])
    gallery = _gallery({1: np.array([1.0, 0.0]), 2: np.array([0.30, 0.95])})
    assert link_tracks([a, b], gallery)["links"] == []


def test_competition_between_two_candidates_blocks_linking():
    """两个候选人外观分数接近时，宁可断，不可接错。"""
    a = _track(1, 0.0, 1.0, [600, 300, 640, 400])
    b = _track(2, 2.0, 3.0, [604, 302, 644, 402])
    c = _track(3, 2.1, 3.0, [640, 300, 680, 400])
    gallery = _gallery({1: np.array([1.0, 0.0]), 2: np.array([0.99, 0.05]), 3: np.array([0.98, 0.1])})
    result = link_tracks([a, b, c], gallery)
    assert result["links"] == []


def test_merge_keeps_observations_in_time_order():
    a = _track(1, 0.0, 0.6, [600, 300, 640, 400])
    b = _track(2, 2.0, 2.6, [604, 302, 644, 402])
    merged = merge_track_objects([a, b], {2: 1})
    assert len(merged) == 1
    track = merged[0]
    assert track["track_id"] == 1
    assert track["merged_from"] == [2]
    times = [o["timestamp_seconds"] for o in track["observations"]]
    assert times == sorted(times)
    assert track["end_seconds"] == 2.6


def test_default_similarity_threshold_matches_product_setting():
    assert DEFAULT_PARAMS["min_cosine"] == 0.72
