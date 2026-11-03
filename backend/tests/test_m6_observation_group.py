import math

from app.m6_observation_group import coverage, group_observers, region_from_state


def _square(lon, lat, half):
    return {"type": "Polygon", "coordinates": [[
        [lon - half, lat - half], [lon + half, lat - half],
        [lon + half, lat + half], [lon - half, lat + half], [lon - half, lat - half]]]}


REGION = _square(110.2100, 25.2570, 0.0002)


def _camera(cid, lon, lat, half=0.0006):
    return {"map_camera_id": cid, "camera_uuid": "uuid-%s" % cid,
            "fov": {"observation": _square(lon, lat, half)}}


def test_camera_covering_region_is_member():
    cam = _camera("58", 110.2100, 25.2570)
    result = group_observers({"geometry": REGION, "source": "possible_region"}, [cam])
    assert result["members"][0]["camera"] == "58"
    assert result["members"][0]["coverage"]["covers"] is True


def test_nearby_but_opposite_camera_is_not_grouped_by_distance():
    covering = _camera("58", 110.2100, 25.2570)
    facing_away = _camera("57", 110.21001, 25.25701)
    facing_away["fov"]["observation"] = _square(110.2140, 25.2570, 0.0002)
    result = group_observers({"geometry": REGION, "source": "possible_region"},
                             [covering, facing_away])
    assert [m["camera"] for m in result["members"]] == ["58"]
    assert result["excluded"][0]["camera"] == "57"
    assert result["excluded"][0]["reason"] == "fov_does_not_cover_region"


def test_distant_camera_covering_same_junction_is_grouped():
    near = _camera("58", 110.2100, 25.2570)
    far = _camera("37", 110.2105, 25.2570)
    result = group_observers({"geometry": REGION, "source": "possible_region"}, [near, far])
    assert {m["camera"] for m in result["members"]} == {"58", "37"}


def test_missing_fov_is_never_merged():
    cam = {"map_camera_id": "99", "camera_uuid": "uuid-99", "fov": {"observation": None}}
    result = group_observers({"geometry": REGION, "source": "possible_region"}, [cam])
    assert result["members"] == []
    assert result["excluded"][0]["reason"] == "fov_missing"


def test_selection_respects_limit_and_orders_by_coverage():
    wide = _camera("58", 110.2100, 25.2570, half=0.0008)
    partial = _camera("37", 110.2103, 25.2570, half=0.0002)
    result = group_observers({"geometry": REGION, "source": "possible_region"},
                             [partial, wide], max_observers=1)
    assert result["members"][0]["camera"] == "58"
    assert result["members"][0]["selected"] is True
    assert result["members"][1]["selected"] is False


def test_region_from_state_buffer():
    region = region_from_state({"last_seen": {"lon": 110.21, "lat": 25.257}})
    assert region["source"] == "arrival_point_buffer"
    assert region["geometry"]["type"] == "Polygon"
    assert region_from_state({})["geometry"] is None
