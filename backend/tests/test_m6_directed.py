from app.m6_directed import (camera_link, candidates_in_window, plan_windows,
                             target_profile, update_profile)


def _links():
    return [camera_link("L22-21", "2-2", "2-1", (2.0, 10.0)),
            camera_link("L22-11", "2-2", "1-1", (5.0, 30.0), exit_side="left"),
            camera_link("L21-11", "2-1", "1-1", (3.0, 12.0))]


def test_plan_windows_uses_travel_range_plus_clock_uncertainty():
    profile = target_profile("2-2", 111, 6.2, 8.6, exit_side="right")
    plans = plan_windows(profile, _links())
    assert plans[0]["camera"] == "2-1"
    start, end = plans[0]["window"]
    assert start == 8.6 + 2.0 - 2.0          # 通行下限 + 误差
    assert end == 8.6 + 10.0 + 2.0           # 通行上限 + 误差
    assert plans[0]["priority"] == "high"


def test_plan_windows_skips_links_with_conflicting_exit_side():
    profile = target_profile("2-2", 111, 9.2, 12.0, exit_side="right")
    cams = [p["camera"] for p in plan_windows(profile, _links())]
    assert "1-1" not in cams                  # 该通道要求从左侧离开
    assert cams == ["2-1"]


def test_candidates_in_window_keeps_only_overlapping_tracks():
    tracks = [{"track_id": 37, "observations": [{"timestamp_seconds": 14.6}, {"timestamp_seconds": 17.6}]},
              {"track_id": 99, "observations": [{"timestamp_seconds": 60.0}, {"timestamp_seconds": 62.0}]}]
    picked = candidates_in_window(tracks, (10.6, 20.6))
    assert [c["track"]["track_id"] for c in picked] == [37]
    assert picked[0]["offset_seconds"] == 4.0


def test_update_profile_keeps_unverified_candidate_out_of_confirmed_observations():
    profile = target_profile("2-2", 111, 9.2, 11.6, exit_side="right")
    updated = update_profile(profile, "2-1", 37, 14.6, 17.6,
                             features=[{"attribute": "upper_clothing", "value": "紫色"}])
    assert updated["observations"][-1]["status"] == "candidate"
    assert updated["features"][0]["verified"] is False
    assert updated["features"][0]["source_camera"] == "2-1"
    # 规划仍以最后一条观测为起点，但确认状态不因并入而升级
    assert plan_windows(updated, _links())[0]["camera"] == "1-1"
