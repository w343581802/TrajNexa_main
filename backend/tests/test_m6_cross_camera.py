"""M6-S6-2 跨摄像头：三态时间关系、同步配对与状态裁决。"""
from app.m6_cross_camera import (alignment_offset, clock_record, cross_status,
                                 observed_at, osd_clock, pair_by_time,
                                 summarise_pairs, temporal_relation,
                                 time_sufficiency, to_wall, transit_priority)


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
    appearance["time_sufficiency"] = "verified"
    assert cross_status(rel, appearance, {"overlap_confirmed": True})["status"] == \
        "overlap_match_candidate"


def test_provisional_clock_blocks_strict_sync_upgrade():
    rel = temporal_relation((100.0, 120.0), (110.0, 130.0))
    provisional = {"score": 0.85, "supporting_candidate_frames": 8,
                   "time_sufficiency": "provisional"}
    assert cross_status(rel, provisional, {"overlap_confirmed": True})["status"] == \
        "pending_time_calibration"
    assert time_sufficiency({"calibration_status": "osd_boundary"},
                            {"calibration_status": "osd_boundary"}) == "provisional"
    assert time_sufficiency({"calibration_status": "verified_for_sync"},
                            {"calibration_status": "verified_for_sync"}) == "verified"
    assert time_sufficiency({"calibration_status": "uncalibrated"},
                            {"calibration_status": "uncalibrated"}) == "uncalibrated"


def test_confirmed_non_overlapping_views_exclude():
    rel = temporal_relation((100.0, 120.0), (110.0, 130.0))
    verdict = cross_status(rel, {"score": 0.9, "supporting_candidate_frames": 5},
                           {"overlap_confirmed": False})
    assert verdict["status"] == "excluded_by_spatiotemporal_evidence"


def test_uncalibrated_clock_never_excludes():
    rel = temporal_relation((100.0, 120.0), (110.0, 130.0))
    verdict = cross_status(rel, {"score": 0.9, "supporting_candidate_frames": 5,
                                 "time_sufficiency": "uncalibrated"},
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


def test_alignment_offset_uses_each_camera_own_file_start():
    # 回归：曾把 2-2 的推算时间按 1-1 的基准算成 15:02:29.8，得 -3.8 秒的错误结论
    file_wall_2_2 = 53997 + 149.8          # 文件名 14:59:57 起，t=149.8
    assert alignment_offset(54146.0, file_wall_2_2) == -0.8
    file_wall_1_1 = 54001 + 149.8          # 文件名 15:00:01 起
    assert alignment_offset(54147.0, file_wall_1_1) == -3.8
    assert round(file_wall_1_1 - file_wall_2_2, 3) == 4.0


def test_transit_priority_marks_fast_passes_high_but_never_excludes():
    fast = temporal_relation((100.0, 120.0), (125.0, 135.0))
    slow = temporal_relation((100.0, 120.0), (160.0, 175.0))
    assert transit_priority(fast) == "high"
    assert transit_priority(slow) == "normal"
    # 优先级不影响裁决：同样是 transition_candidate
    appearance = {"score": 0.8, "supporting_candidate_frames": 4}
    assert cross_status(fast, appearance)["status"] == "transition_candidate"
    assert cross_status(slow, appearance)["status"] == "transition_candidate"


def test_observed_at_uses_first_frame_osd_not_requested_window_start():
    # 回归：三路都请求 15:02:00 抽帧，1-1 实得首帧 OSD 15:01:56（=54116）
    assert observed_at(54116, 0, 5) == 54116.0
    assert observed_at(54116, 1029, 5) == 54321.8
    # 若误用请求起点 54120，就会凭空多出 4 s
    assert observed_at(54120, 0, 5) - observed_at(54116, 0, 5) == 4.0


def test_osd_clock_separates_file_offset_from_camera_clock():
    clock = osd_clock("T口2-2", 53997.0, 53997.0, 1.0)
    assert clock["file_osd_alignment_offset_seconds"] == 0.0
    assert clock["intercamera_clock_offset_seconds"] is None      # 未由共同事件确定
    assert clock["calibration_status"] == "osd_boundary"
    assert to_wall(clock, 78.8) == 54075.8
    other = osd_clock("T口1-1", 53997.0, 54001.0, 1.0)
    assert other["file_osd_alignment_offset_seconds"] == -4.0


def test_summarise_pairs_reports_distribution():
    stats = summarise_pairs([{}] * 3, [0.7, 0.8, 0.9])
    assert stats["paired_frames"] == 3
    assert stats["median_cosine"] == 0.8
    assert stats["min_cosine"] == 0.7 and stats["max_cosine"] == 0.9
