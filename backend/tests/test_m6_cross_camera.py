"""M6-S6-2 跨摄像头：三态时间关系、同步配对与状态裁决。"""
from app.m6_cross_camera import (clock_record, cross_status, pair_by_time,
                                 summarise_pairs, temporal_relation, to_wall)


def test_overlap_reports_real_duration_not_negative_gap():
    rel = temporal_relation((54068.8, 54097.6), (54075.8, 54080.2))
    assert rel["relation"] == "overlapping"
    assert rel["overlap_seconds"] == 4.4
    assert rel["transition_gap_seconds"] is None


def test_sequential_reports_gap_and_direction():
    rel = temporal_relation((100.0, 120.0), (135.0, 150.0))
    assert rel["relation"] == "sequential"
    assert rel["transition_gap_seconds"] == 15.0
    assert rel["direction"] == "anchor_first"


def test_overlap_without_confirmed_coverage_is_pending():
    rel = temporal_relation((100.0, 120.0), (110.0, 130.0))
    appearance = {"score": 0.85, "supporting_candidate_frames": 8}
    assert cross_status(rel, appearance, {"overlap_confirmed": None})["status"] == \
        "pending_spatial_verification"
    assert cross_status(rel, appearance, {"overlap_confirmed": True})["status"] == \
        "overlap_match_candidate"


def test_confirmed_non_overlapping_views_exclude():
    rel = temporal_relation((100.0, 120.0), (110.0, 130.0))
    verdict = cross_status(rel, {"score": 0.9, "supporting_candidate_frames": 5},
                           {"overlap_confirmed": False})
    assert verdict["status"] == "excluded_by_spatiotemporal_evidence"


def test_uncalibrated_clock_never_excludes():
    rel = temporal_relation((100.0, 120.0), (110.0, 130.0))
    verdict = cross_status(rel, {"score": 0.9, "supporting_candidate_frames": 5,
                                 "calibration_status": "uncalibrated"},
                           {"overlap_confirmed": False})
    assert verdict["status"] == "pending_time_calibration"


def test_transition_states():
    appearance = {"score": 0.8, "supporting_candidate_frames": 4}
    assert cross_status(temporal_relation((100.0, 120.0), (135.0, 150.0)),
                        appearance)["status"] == "transition_candidate"
    assert cross_status(temporal_relation((100.0, 120.0), (400.0, 460.0)),
                        appearance)["status"] == "rejected_by_travel_time"
    assert cross_status(temporal_relation((100.0, 120.0), (121.0, 130.0)),
                        appearance)["status"] == "pending_transition_check"


def test_pair_by_time_is_one_to_one():
    anchors = [(100.0, "a1"), (100.2, "a2")]
    candidates = [(100.1, "c1"), (100.3, "c2")]
    pairs = pair_by_time(anchors, candidates, 1.0)
    assert len(pairs) == 2
    used = [p["candidate"] for p in pairs]
    assert len(set(used)) == 2


def test_clock_record_keeps_unknown_uncertainty_null():
    clock = clock_record("T口2-2", 53997, -1.0, None)
    assert clock["offset_uncertainty_seconds"] is None
    assert clock["calibration_status"] == "provisional"
    assert to_wall(clock, 78.8) == 54074.8


def test_summarise_pairs_reports_distribution():
    stats = summarise_pairs([{}] * 3, [0.7, 0.8, 0.9])
    assert stats["paired_frames"] == 3
    assert stats["median_cosine"] == 0.8
    assert stats["min_cosine"] == 0.7 and stats["max_cosine"] == 0.9
