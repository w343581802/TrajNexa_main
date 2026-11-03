from app.m6_motion_predictor import (arrival_band, plausible_by_region,
                                     reachable_region)
from app.m6_target_spatial import target_spatial_state


def _square(lon, lat, side=0.0004):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + side, lat],
                                                [lon + side, lat + side], [lon, lat + side],
                                                [lon, lat]]]}


def _cam(map_id="2-2", lon=110.210883, lat=25.257293):
    return {"map_camera_id": map_id, "camera_uuid": "u" + map_id, "display_name": map_id,
            "location": {"lon": lon, "lat": lat, "crs": "EPSG:4326"},
            "bearing_deg": 331.0,
            "fov": {"observation": _square(lon, lat), "quality": "modelled_unverified"},
            "spatial_revision": "rev"}


def test_region_radius_grows_with_time_and_speed():
    state = target_spatial_state(_cam(), 54008.6, "electric_bicycle")
    near = reachable_region(state, 5.0, "electric_bicycle")
    far = reachable_region(state, 40.0, "electric_bicycle")
    assert far["radius_m"] > near["radius_m"]
    walk = reachable_region(state, 40.0, "walking")
    assert far["radius_m"] > walk["radius_m"]


def test_region_is_unknown_when_no_fov():
    cam = _cam()
    cam["fov"] = {"observation": None, "quality": "unknown"}
    state = target_spatial_state(cam, 54008.6, "walking")
    region = reachable_region(state, 10.0, "walking")
    assert region["center"] is None
    assert region["uncertainty"] == "no_region_only_camera"


def test_plausibility_is_not_a_hard_exclusion():
    state = target_spatial_state(_cam(), 54008.6, "walking")
    close = _cam("near", 110.210547, 25.257427)
    far = _cam("far", 110.2300, 25.2700)
    assert plausible_by_region(state, close, 40.0, "walking")["plausible"] is True
    assert plausible_by_region(state, far, 5.0, "walking")["plausible"] is False
    assert plausible_by_region(state, far, 5.0, "walking")["reason"] == "beyond_reachable_radius"


def test_arrival_band_is_ordered():
    state = target_spatial_state(_cam(), 54008.6, "electric_bicycle")
    band = arrival_band(state, _cam("far", 110.2130, 25.2590), "electric_bicycle")
    assert band["min_s"] <= band["max_s"]
