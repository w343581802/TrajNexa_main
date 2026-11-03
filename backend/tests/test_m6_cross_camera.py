"""M6-S6 跨摄像头：时空门控与状态合成。"""
from app.m6_cross_camera import rank_cross_candidates, temporal_gate, wall_clock


def test_wall_clock_uses_offset():
    # 文件 14:59:59 起、OSD 慢 4 秒、轨迹内 60s → 墙钟 15:00:55
    assert wall_clock(14 * 3600 + 59 * 60 + 59, -4, 60) == 15 * 3600 + 55


def test_same_time_in_two_cameras_is_rejected():
    gate = temporal_gate((100.0, 160.0), (100.0, 160.0))
    assert gate["compatible"] is False
    assert gate["reason"] == "time_overlap_needs_frame_level_check"


def test_too_close_and_too_far_are_rejected():
    assert temporal_gate((100.0, 160.0), (161.0, 200.0))["reason"] == \
        "too_close_to_be_another_camera"
    assert temporal_gate((100.0, 160.0), (400.0, 460.0))["reason"] == \
        "too_far_to_be_same_person"


def test_transition_window_is_compatible():
    gate = temporal_gate((100.0, 160.0), (175.0, 240.0))
    assert gate["compatible"] is True and gate["gap_seconds"] == 15.0


def test_rank_keeps_rejected_candidates_visible():
    appearance = {"score": 0.9, "supporting_candidate_frames": 5}
    candidates = [{"camera": "2-2", "track": 7, "span": (100.0, 160.0),
                   "appearance": appearance},
                  {"camera": "2-1", "track": 9, "span": (180.0, 240.0),
                   "appearance": {"score": 0.75, "supporting_candidate_frames": 4}}]
    ranked = rank_cross_candidates((100.0, 160.0), candidates)
    assert ranked[0]["status"] == "rejected_by_time"
    assert ranked[1]["status"] == "cross_camera_candidate"
    assert ranked[1]["rank"] == 2 and len(ranked) == 2
