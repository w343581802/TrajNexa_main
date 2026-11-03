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
    assert cand["same_site"] is True
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
    assert transit_window(37.0, "electric_bicycle")["transit_min"] > 0
    far = transit_window(37.0, "electric_bicycle")
    near = transit_window(3.0, "electric_bicycle")
    assert far["transit_max"] > near["transit_max"]


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
    plans = {p["camera"]: p["window"] for p in
             windows_for_candidates(state, plan_spatial_candidates(state, [src, near, far]))}
    assert plans["2-1"][0] >= 54008.6        # 同杆转移 1 s 减去 1 s 裕量
    assert plans["2-1"][1] > 54008.6 + 20    # 但不压缩成 0~1 s
    assert plans["1-1"] != plans["2-1"]        # 不再统一窗口
    assert plans["2-1"][1] - plans["2-1"][0] > plans["1-1"][1] - plans["1-1"][0]


def test_overlap_detection_and_distance():
    a = _square(110.2100, 25.2570)
    b = _square(110.2101, 25.2571)          # 重叠
    c = _square(110.2200, 25.2600)          # 远离
    assert fov_relation(a, b)["relation"] == "overlap"
    rel = fov_relation(a, c)
    assert rel["relation"] == "disjoint" and rel["distance_m"] > 10
