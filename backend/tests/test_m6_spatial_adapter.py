import pytest

from app.m6_spatial_adapter import (SpatialBindingError, bind_channel, bearing_between,
                                    camera_spec, haversine_m, name_match_guard,
                                    point_in_polygon)


def _cam(map_id="37", uuid="u-1", name="YD-桂康路中仁路T口1-1", fov=True):
    levels = [{"level": "observation", "geometry": {"type": "Polygon", "coordinates": [
        [[110.0, 25.0], [110.001, 25.0], [110.001, 25.001], [110.0, 25.001], [110.0, 25.0]]]},
        "occlusion_applied": False, "status": "modelled_unverified"}] if fov else []
    return {"map_camera_id": map_id, "camera_uuid": uuid, "display_name": name,
            "location": {"lon": 110.210547, "lat": 25.257427, "crs": "EPSG:4326"},
            "bearing_deg": 87.0, "tilt_deg": 22.0, "horizontal_fov_deg": None,
            "range_m": None, "pose_quality": "calibrated", "position_quality": "reported",
            "has_video": True, "fov_levels": levels}


def _payload(*cams):
    return {"spatial_revision": "mapdb-spatial:test", "cameras": list(cams)}


def test_name_based_binding_is_refused_when_ambiguous():
    cams = [_cam("37", "u-1", "YD-桂康路中仁路T口2-1"), _cam("35", "u-2", "YD-金水路中仁路口2-1")]
    with pytest.raises(SpatialBindingError, match="camera_name_ambiguous"):
        name_match_guard("YD-金水路中仁路口2-1", cams + [_cam("36", "u-3", "YD-金水路中仁路口2-1")])


def test_pending_binding_is_not_treated_as_verified():
    payload = _payload(_cam())
    bindings = {"channels": {"1-1": {"camera_uuid": "u-1", "status": "pending_human_review",
                                     "evidence": "名称相似"}}}
    result = bind_channel(payload, bindings, "1-1")
    assert result["status"] == "pending_human_review"
    assert result["camera"]["camera_binding_status"] == "pending_human_review"
    assert "尚未人工核实" in result["camera"]["camera_binding_note"]


def test_unknown_uuid_does_not_create_fake_camera():
    payload = _payload(_cam())
    bindings = {"channels": {"2-1": {"camera_uuid": "missing", "status": "human_verified"}}}
    result = bind_channel(payload, bindings, "2-1")
    assert result["status"] == "unbound" and result["camera"] is None


def test_channel_absent_from_table_stays_unbound():
    result = bind_channel(_payload(_cam()), {"channels": {}}, "3-9")
    assert result["status"] == "unbound"
    assert result["reason"] == "channel_not_in_binding_table"


def test_missing_fov_is_unknown_not_fabricated():
    spec = camera_spec(_cam(fov=False), "rev")
    assert spec["fov"]["quality"] == "unknown"
    assert spec["fov"]["observation"] is None
    assert spec["horizontal_fov_deg"] is None      # 不编造视场角


def test_pose_calibrated_does_not_imply_verified_coverage():
    spec = camera_spec(_cam(), "rev")
    assert spec["pose_quality"] == "calibrated"
    assert spec["fov"]["quality"] == "modelled_unverified"


def test_has_video_never_means_historical_recording_available():
    spec = camera_spec(_cam(), "rev")
    assert spec["recording_availability"] == "unknown"


def test_geometry_helpers():
    a = {"lon": 110.210547, "lat": 25.257427}
    b = {"lon": 110.210883, "lat": 25.257293}
    assert 30 < haversine_m(a, b) < 45
    assert 90 < bearing_between(a, b) < 180
    square = {"type": "Polygon", "coordinates": [[[110.0, 25.0], [110.002, 25.0],
                                                  [110.002, 25.002], [110.0, 25.002],
                                                  [110.0, 25.0]]]}
    assert point_in_polygon({"lon": 110.001, "lat": 25.001}, square) is True
    assert point_in_polygon({"lon": 110.01, "lat": 25.001}, square) is False
