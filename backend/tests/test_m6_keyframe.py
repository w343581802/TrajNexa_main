"""M6-S4-4A 关键画面发现：可解释打分与去重。"""
from app.m6_keyframe import (DEFAULT_KEYFRAME_PARAMS, border_touch, crowding, edge_ratio,
                             outside_ratio, score_frames, select_keyframes)


def _obs(frame_id, bbox, ts=None):
    return {"frame_id": str(frame_id), "bbox": bbox,
            "timestamp_seconds": float(ts if ts is not None else int(frame_id) * 0.2),
            "frame_width": 1280, "frame_height": 720}


def test_edge_ratio_penalises_clipped_boxes():
    assert outside_ratio([300, 200, 400, 500], 1280, 720) == 0.0
    assert outside_ratio([-30, 200, 60, 500], 1280, 720) > 0.3
    assert outside_ratio([1240, 200, 1300, 500], 1280, 720) > 0.3


def test_border_touch_is_not_the_same_as_outside_ratio():
    """检测框被裁到 y=0：外面面积是 0，但框贴边，主体可能已经出画。"""
    clipped = [300, 0, 400, 300]
    assert outside_ratio(clipped, 1280, 720) == 0.0
    assert border_touch(clipped, 1280, 720) is True
    assert border_touch([300, 100, 400, 400], 1280, 720) is False


def test_size_baseline_keeps_caller_order():
    """sort_key=None 时必须按调用方给的顺序，不能又被综合分覆盖。"""
    scored = [
        {"frame_id": "a", "timestamp_seconds": 0.0, "score": 0.10, "height_px": 200.0},
        {"frame_id": "b", "timestamp_seconds": 1.0, "score": 0.90, "height_px": 90.0},
        {"frame_id": "c", "timestamp_seconds": 2.0, "score": 0.20, "height_px": 150.0},
    ]
    by_size = select_keyframes(sorted(scored, key=lambda s: -s["height_px"]),
                               k=2, min_gap_seconds=0.0, sort_key=None)
    by_score = select_keyframes(scored, k=2, min_gap_seconds=0.0)
    assert [s["frame_id"] for s in by_size] == ["a", "c"]
    assert [s["frame_id"] for s in by_score] == ["b", "c"]


def test_crowding_uses_max_iou_with_other_boxes():
    assert crowding([100, 100, 200, 300], [[100, 100, 200, 300]]) == 1.0
    assert crowding([100, 100, 200, 300], [[900, 100, 1000, 300]]) == 0.0


def test_bigger_sharper_frame_scores_higher():
    obs = [_obs(0, [100, 100, 180, 260]), _obs(1, [100, 100, 220, 500])]
    sharp = {"0": 5.0, "1": 90.0}
    scored = score_frames(obs, sharp)
    assert scored[1]["score"] > scored[0]["score"]
    assert scored[1]["size"] == 1.0


def test_crowded_frame_is_penalised():
    obs = [_obs(0, [100, 100, 200, 400]), _obs(1, [100, 100, 200, 400])]
    sharp = {"0": 40.0, "1": 40.0}
    open_scene = score_frames(obs, sharp, others_by_frame={})
    crowded = score_frames(obs, sharp, others_by_frame={"1": [[100, 100, 200, 400]]})
    assert crowded[1]["score"] < open_scene[1]["score"]


def test_preferred_view_gets_bonus():
    obs = [_obs(0, [100, 100, 200, 400]), _obs(1, [100, 100, 200, 400])]
    sharp = {"0": 40.0, "1": 40.0}
    plain = score_frames(obs, sharp)
    front = score_frames(obs, sharp, views_by_frame={"0": "front"})
    assert abs(front[0]["score"] - plain[0]["score"]
               - DEFAULT_KEYFRAME_PARAMS["orientation_bonus"]) < 1e-3


def test_selection_respects_time_gap_and_cap():
    scored = [{"frame_id": str(i), "timestamp_seconds": i * 0.2, "score": 1.0 - i * 0.01}
              for i in range(20)]
    picked = select_keyframes(scored, k=3, min_gap_seconds=0.9)
    assert len(picked) == 3
    for a, b in zip(picked, picked[1:]):
        assert abs(a["timestamp_seconds"] - b["timestamp_seconds"]) >= 0.9
