import pytest

from app import m6_track_spatial as ts

CAM = {"width": 1920, "height": 1080, "hfov_deg": 64.0, "pitch_deg": 25.0,
       "yaw_deg": 168.0, "height_m": 4.0, "lon": 110.211389, "lat": 25.256698}


def test_track_projects_to_experimental_points():
    samples = [{"t": 0.0, "box": [900, 600, 980, 760]},
               {"t": 1.0, "box": [880, 590, 960, 745]}]
    report = ts.project_track(CAM, samples, kind="vehicle")
    assert report["layer"] == "experimental_estimate"
    assert report["persisted"] is False
    assert report["summary"]["total"] == 2
    assert report["summary"]["projected"] >= 1
    first = report["points"][0]
    assert first["pixel"][1] == pytest.approx(760.0)
    assert first["spatial_level"] == "L2-candidate"
    assert first["ground_point"]["crs"] == "EPSG:4326"


def test_bottom_contact_is_flagged_and_still_projected():
    report = ts.project_track(CAM, [{"t": 0.0, "box": [900, 900, 980, 1079]}], kind="person")
    point = report["points"][0]
    assert point["contact_quality"] == "cut_off"
    assert "contact_at_frame_bottom" in point["contact_uncertainties"]
    assert report["summary"]["reliable_contact"] == 0


def test_invalid_box_degrades_without_crashing():
    report = ts.project_track(CAM, [{"t": 0.0, "box": [10, 10, 5, 5]}])
    point = report["points"][0]
    assert point["spatial_level"] == "L1"
    assert point["degrade_reason"] == "box_invalid"
    assert report["summary"]["degraded"] == 1


def test_missing_fov_degrades_points():
    cam = dict(CAM)
    cam["hfov_deg"] = None
    report = ts.project_track(cam, [{"t": 0.0, "box": [900, 600, 980, 760]}])
    assert report["points"][0]["degrade_reason"] == "hfov_deg_missing"


def test_route_serves_track_spatial(env):
    client, _, _ = env
    response = client.post("/api/v1/track-spatial", json={
        "camera": CAM,
        "samples": [{"t": 0.0, "box": [900, 600, 980, 760]}],
    })
    assert response.status_code == 200, response.text
    assert response.json()["layer"] == "experimental_estimate"


def test_sample_track_endpoint(env):
    client, _, _ = env
    response = client.get("/api/v1/track-spatial/sample")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["layer"] == "experimental_estimate"
    assert data["persisted"] is False
    assert data["summary"]["total"] == data["sample"]["count"]
    assert data["summary"]["projected"] >= 1


def test_projected_points_carry_quality_and_evidence():
    report = ts.project_track(CAM, [{"t": 1.5, "box": [900, 600, 980, 760]}],
                              evidence={"clip": "c.mp4", "fps": 2.0})
    point = report["points"][0]
    assert point["quality"] == "experimental_estimate"
    assert point["degrade_reason"] is None
    assert point["evidence"]["clip"] == "c.mp4"
    assert point["t"] == 1.5


def test_track_layer_does_not_write_formal_observations(env):
    from sqlalchemy import func, select
    from app.models import TargetSpatialObservation
    client, Session, _ = env
    with Session() as db:
        before = db.scalar(select(func.count()).select_from(TargetSpatialObservation))
    response = client.post("/api/v1/track-spatial", json={
        "camera": CAM,
        "samples": [{"t": 0.0, "box": [900, 600, 980, 760]}],
    })
    assert response.status_code == 200
    with Session() as db:
        after = db.scalar(select(func.count()).select_from(TargetSpatialObservation))
    assert after == before
