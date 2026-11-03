import pytest

from app.m6_directed import (absolute_track, advance, camera_link, candidates_in_window,
                             mark_missing, plan_windows, target_profile, update_profile)


def _links(direction="forward"):
    return [camera_link("L22-21", "2-2", "2-1", (2.0, 10.0), direction=direction),
            camera_link("L22-11", "2-2", "1-1", (5.0, 30.0), exit_side="left",
                        direction=direction),
            camera_link("L21-11", "2-1", "1-1", (3.0, 12.0))]


def _profile(exit_side="right"):
    # 绝对 OSD 秒：2-2 锚点 53997，相对 6.2–11.6
    return target_profile("2-2", 111, 54003.2, 54008.6, exit_side=exit_side)


def test_relative_seconds_are_rejected_instead_of_silently_mixed():
    with pytest.raises(ValueError):
        target_profile("2-2", 111, 6.2, 11.6)
    track = {"track_id": 37, "observations": [{"timestamp_seconds": 14.6}]}
    with pytest.raises(ValueError):
        candidates_in_window([track], (54010.6, 54020.6))


def test_absolute_track_uses_each_camera_own_anchor():
    track = {"track_id": 37, "observations": [
        {"timestamp_seconds": 14.6, "bbox": [0, 0, 1, 1]},
        {"timestamp_seconds": 17.6, "bbox": [0, 0, 1, 1]}]}
    out = absolute_track(track, 53998)
    assert out["observations"][0]["absolute_seconds"] == 54012.6
    # 同一个相对秒用错锚点会偏 4 s
    assert absolute_track(track, 53997)["observations"][0]["absolute_seconds"] == 54011.6


def test_forward_window_keeps_the_real_4s_gap():
    plans = [p for p in plan_windows(_profile(), _links()) if p["mode"] == "forward"]
    plan = [p for p in plans if p["camera"] == "2-1"][0]
    assert plan["window"] == (54008.6, 54020.6)
    track = absolute_track({"track_id": 37, "observations": [
        {"timestamp_seconds": 14.6, "bbox": [0, 0, 1, 1]},
        {"timestamp_seconds": 17.6, "bbox": [0, 0, 1, 1]}]}, 53998)
    picked = candidates_in_window([track], plan["window"])
    assert picked and picked[0]["track"]["track_id"] == 37
    assert picked[0]["enter"] - 54008.6 == pytest.approx(4.0)


def test_backward_window_looks_before_the_earliest_observation():
    plans = [p for p in plan_windows(_profile(), _links("backward")) if p["mode"] == "backward"]
    plan = [p for p in plans if p["camera"] == "2-1"][0]
    assert plan["window"] == (53991.2, 54003.2)      # 最早观测 54003.2 之前


def test_backward_camera_is_not_reported_as_next_hop():
    profile = _profile()
    forward = [p["camera"] for p in plan_windows(profile, _links("forward"))]
    assert "1-1" not in forward                       # 该通道要求从左侧离开
    both = plan_windows(profile, _links("both"))
    assert {p["mode"] for p in both} == {"forward", "backward"}


def test_missing_detection_is_recorded_not_treated_as_absence():
    profile = _profile()
    window = (54008.6, 54020.6)
    tracks = {"2-1": [absolute_track({"track_id": 900, "observations": [
        {"timestamp_seconds": 40.0, "bbox": [0, 0, 1, 1]},
        {"timestamp_seconds": 41.0, "bbox": [0, 0, 1, 1]}]}, 53998)]}
    links = [camera_link("L22-21", "2-2", "2-1", (2.0, 10.0))]
    result = advance(profile, tracks, links)
    assert result["candidates"]["2-1"] == []
    assert result["missing"][0]["camera"] == "2-1"           # 记为缺失，不是"没经过"
    marked = mark_missing(profile, "2-1", window, "目标可见但检测无框")
    assert marked["missing"][0]["reason"] == "目标可见但检测无框"
    # 缺失后仍能继续规划下一跳
    assert plan_windows({**marked, "observations": marked["observations"]}, _links())
    assert marked["version" if "version" in marked else "observations"]


def test_update_profile_keeps_unverified_candidate_out_of_confirmed_observations():
    updated = update_profile(_profile(), "2-1", 37, 54012.6, 54015.6,
                            features=[{"attribute": "upper_clothing", "value": "紫色"}])
    assert updated["observations"][-1]["status"] == "candidate"
    assert updated["features"][0]["verified"] is False
    assert updated["features"][0]["source_camera"] == "2-1"
    assert plan_windows(updated, _links())[0]["camera"] == "1-1"
