import pytest

from app.m6_spatial_planner import (fov_relation, mark_exploratory, plan_spatial_candidates,
                                    speed_consistency, transit_window, windows_for_candidates)
from app.m6_target_spatial import camera_site_groups, target_spatial_state


def _square(lon, lat, side=0.0004):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + side, lat],
                                                [lon + side, lat + side], [lon, lat + side],
                                                [lon, lat]]]}


def _facing_a(lon, lat):
    """朝东的一小块视域（与对向镜头不重叠）。"""
    return _square(lon + 0.00003, lat + 0.00003, 0.00008)


def _facing_b(lon, lat):
    """朝西的一小块视域，与 A 相距约 2 m。"""
    return _square(lon - 0.00009, lat + 0.00003, 0.00008)


def _cam(map_id, lon, lat, bearing, fov=True, quality="modelled_unverified"):
    return {"map_camera_id": map_id, "camera_uuid": "u" + map_id, "display_name": "cam " + map_id,
            "location": {"lon": lon, "lat": lat, "crs": "EPSG:4326"},
            "bearing_deg": bearing, "tilt_deg": 20.0, "horizontal_fov_deg": None,
            "range_m": None, "pose_quality": "calibrated",
            "fov": {"observation": _square(lon, lat) if fov else None,
                    "recognition": None, "identification": None, "quality": quality},
            "spatial_revision": "rev-1"}


# 场景 A：同杆反向，不得把转移时间压成 0
def test_same_site_opposite_cameras_never_get_zero_transit():
    a = _cam("2-2", 110.210883, 25.257293, 331.0)
    b = _cam("2-1", 110.210879, 25.257289, 161.0)
    a["fov"]["observation"] = _facing_a(110.210883, 25.257293)
    b["fov"]["observation"] = _facing_b(110.210883, 25.257293)
    state = target_spatial_state(a, 54008.6, "electric_bicycle")
    cand = [c for c in plan_spatial_candidates(state, [a, b]) if c["candidate_camera"] == "2-1"][0]
    assert cand["same_site_suspected"] is True
    assert cand["installation_distance_m"] < 5.0
    assert cand["transit_min"] > 0
    assert "same_site_opposite_camera" in cand["reasons"]
    assert cand["transit_basis"] == "same_site_pair"


def test_site_group_detects_colocated_but_stays_unverified():
    a = _cam("2-2", 110.210883, 25.257293, 331.0)
    b = _cam("2-1", 110.210879, 25.257289, 161.0)
    groups = camera_site_groups([a, b])
    assert len(groups) == 1
    assert groups[0]["map_camera_ids"] == ["2-1", "2-2"]
    assert groups[0]["bearing_delta_deg"] == pytest.approx(170.0, abs=0.5)
    assert groups[0]["site_relation_verified"] is False


# 场景 B：不同地点，不能用安装点距离判速度
def test_far_cameras_do_not_imply_speed_from_install_points():
    verdict = speed_consistency(36.9, 2.4, "electric_bicycle")
    assert verdict["verdict"] == "spatiotemporal_consistency_unverified"
    assert "不等于不可能" in verdict["note"]
    ok = speed_consistency(36.9, 7.0, "electric_bicycle")
    assert ok["verdict"] == "consistent_with_mode"


def test_transit_uses_region_distance_not_install_points():
    assert transit_window(37.0, "electric_bicycle")["estimated_min"] > 0
    far = transit_window(37.0, "electric_bicycle")
    near = transit_window(3.0, "electric_bicycle")
    assert far["estimated_max"] > near["estimated_max"]


def test_search_window_is_not_a_hard_cutoff_from_fov_gap():
    window = transit_window(8.24, "electric_bicycle")
    assert window["estimated_max"] == pytest.approx(8.24 / 3.5, abs=0.05)
    assert window["search_max"] >= 30.0          # 不把最短路线估算当截止
    assert window["search_max"] > window["estimated_max"]
    assert window["uncertainty"] == "detour_and_dwell_not_modelled"


def test_same_site_uses_installation_distance_not_fov_gap():
    src = _cam("2-2", 110.210883, 25.257293, 331.0)
    far_but_close_fov = _cam("71", 110.211600, 25.257900, 45.0)
    src["fov"]["observation"] = _square(110.21090, 25.25733, 0.00020)
    far_but_close_fov["fov"]["observation"] = _square(110.21095, 25.25738, 0.00020)
    state = target_spatial_state(src, 54008.6)
    cand = plan_spatial_candidates(state, [src, far_but_close_fov])[0]
    assert cand["fov_gap_m"] < 5.0                  # 视域很近
    assert cand["installation_distance_m"] > 50     # 安装点很远
    assert cand["same_site_suspected"] is False     # 不能判为同点位


# 场景 C：FOV 未经验证，不能静默排除
def test_unverified_fov_is_kept_with_lower_priority():
    src = _cam("2-2", 110.210883, 25.257293, 331.0)
    far = _cam("9-9", 110.2150, 25.2600, 90.0)
    state = target_spatial_state(src, 54008.6)
    cands = plan_spatial_candidates(state, [src, far])
    assert len(cands) == 1                      # 没有被丢弃
    assert "fov_not_field_verified" in cands[0]["uncertainties"]
    assert cands[0]["status"] == "candidate_camera"


def test_missing_fov_yields_unknown_relation_not_exclusion():
    src = _cam("2-2", 110.210883, 25.257293, 331.0)
    blind = _cam("7-7", 110.2120, 25.2580, 200.0, fov=False)
    state = target_spatial_state(src, 54008.6)
    cand = plan_spatial_candidates(state, [src, blind])[0]
    assert cand["spatial_relation"] in ("unknown", "camera_point_distance")
    assert "spatial_relation_unknown" in cand["reasons"]


# 场景 D：绑定待核实，只能探索
def test_pending_binding_results_are_marked_exploratory():
    src = _cam("2-2", 110.210883, 25.257293, 331.0)
    near = _cam("2-1", 110.210879, 25.257289, 161.0)
    state = target_spatial_state(src, 54008.6)
    cands = mark_exploratory(plan_spatial_candidates(state, [src, near]),
                             "pending_human_review")
    assert cands[0]["exploratory"] is True
    assert cands[0]["binding_status"] == "pending_human_review"


def test_per_camera_windows_differ_by_transit_time():
    src = _cam("2-2", 110.210883, 25.257293, 331.0)
    near = _cam("2-1", 110.210879, 25.257289, 161.0)
    far = _cam("1-1", 110.210547, 25.257427, 87.0)
    src["fov"]["observation"] = _facing_a(110.210883, 25.257293)
    near["fov"]["observation"] = _facing_b(110.210883, 25.257293)
    far["fov"]["observation"] = _square(110.21055, 25.25742, 0.0003)
    state = target_spatial_state(src, 54008.6)
    plans = {p["camera"]: p for p in
             windows_for_candidates(state, plan_spatial_candidates(state, [src, near, far]))}
    assert plans["2-1"]["window"][0] >= 54008.6        # 同杆转移 1 s 减去 1 s 裕量
    assert plans["2-1"]["window"][1] > 54008.6 + 20    # 但不压缩成 0~1 s
    assert plans["1-1"]["window"] != plans["2-1"]["window"]
    # 估算窗口与搜索窗口分开：搜索上界不被最短路线估算卡死
    est = plans["2-1"]["estimated_window"]
    assert plans["2-1"]["window"][1] >= est[1]
    assert plans["1-1"]["window"][1] >= plans["1-1"]["estimated_window"][1]


def test_camera_without_fov_downgrades_to_L0():
    cam = _cam("8-8", 110.2100, 25.2570, 90.0, fov=False)
    state = target_spatial_state(cam, 54000.0)
    assert state["level"] == "L0"
    assert state["possible_region"] is None
    assert "fov_missing_region_unknown" in state["uncertainties"]


def test_geometry_handles_holes_and_touching():
    outer = [[110.0, 25.0], [110.002, 25.0], [110.002, 25.002], [110.0, 25.002], [110.0, 25.0]]
    hole = [[110.0008, 25.0008], [110.0012, 25.0008], [110.0012, 25.0012], [110.0008, 25.0012],
            [110.0008, 25.0008]]
    donut = {"type": "Polygon", "coordinates": [outer, hole]}
    center = {"type": "Polygon", "coordinates": [[[110.00085, 25.00085], [110.00115, 25.00085],
                                                  [110.00115, 25.00115], [110.00085, 25.00115],
                                                  [110.00085, 25.00085]]]}
    # 中心小多边形落在孔洞里：不应判定为 overlap
    assert fov_relation(donut, center)["relation"] == "disjoint"
    # 贴边接触：distance 为 0，关系不得是 overlap
    touch = {"type": "Polygon", "coordinates": [[[110.002, 25.0], [110.003, 25.0],
                                                 [110.003, 25.001], [110.002, 25.001],
                                                 [110.002, 25.0]]]}
    rel = fov_relation(donut, touch)
    assert rel["relation"] in ("touching", "disjoint")
    assert rel["distance_m"] == 0.0


def test_multipolygon_is_supported():
    multi = {"type": "MultiPolygon", "coordinates": [
        [[[110.0, 25.0], [110.001, 25.0], [110.001, 25.001], [110.0, 25.001], [110.0, 25.0]]],
        [[[110.01, 25.01], [110.011, 25.01], [110.011, 25.011], [110.01, 25.011], [110.01, 25.01]]]]}
    near = {"type": "Polygon", "coordinates": [[[110.0105, 25.0105], [110.0108, 25.0105],
                                                [110.0108, 25.0108], [110.0105, 25.0108],
                                                [110.0105, 25.0105]]]}
    assert fov_relation(multi, near)["relation"] == "overlap"


def test_overlap_detection_and_distance():
    a = _square(110.2100, 25.2570)
    b = _square(110.2101, 25.2571)          # 重叠
    c = _square(110.2200, 25.2600)          # 远离
    assert fov_relation(a, b)["relation"] == "overlap"
    rel = fov_relation(a, c)
    assert rel["relation"] == "disjoint" and rel["distance_m"] > 10


def test_recently_observed_camera_is_deprioritized_not_dropped():
    src = _cam("2-1", 110.2109, 25.2573, 331.0)
    back = _cam("2-2", 110.210883, 25.257293, 161.0)
    state = target_spatial_state(src, 54015.6)
    cands = plan_spatial_candidates(state, [src, back], observed_cameras=["2-2"])
    entry = [c for c in cands if c["candidate_camera"] == "2-2"]
    assert entry, "刚看过的镜头仍应保留为候选"
    assert entry[0]["priority"] == "low"
    assert "recently_observed_camera" in entry[0]["reasons"]
