import pytest

from app import m6_experimental_api as api

CAM = {"width": 1920, "height": 1080, "hfov_deg": 64.0, "pitch_deg": 25.0,
       "yaw_deg": 168.0, "height_m": 4.0, "lon": 110.211389, "lat": 25.256698}


def test_report_marks_experimental_and_not_persisted():
    payload = api.ProjectionIn(camera=api.CameraProfile(**CAM),
                               points=[api.PointIn(pixel=[960, 540], observation_id="obs-1")])
    report = api.build_report(payload)
    assert report["layer"] == "experimental_estimate"
    assert report["persisted"] is False
    entry = report["points"][0]
    assert entry["spatial_level"] == "L2-candidate"
    assert entry["quality"] == "experimental_estimate"
    assert entry["observation_id"] == "obs-1"


def test_missing_fov_degrades_point():
    cam = dict(CAM)
    cam["hfov_deg"] = None
    payload = api.ProjectionIn(camera=api.CameraProfile(**cam), points=[api.PointIn(pixel=[960, 540])])
    report = api.build_report(payload)
    assert report["degraded"] == 1
    assert report["points"][0]["degrade_reason"] == "hfov_deg_missing"


def test_experimental_route_is_registered_on_main_app(env):
    """真实 POST 一次：同时验证路由注册、请求解析与 persisted=false。

    注：曾用 app.routes 列表断言，但该列表与请求实际路由结果不一致
    （实测 routes 里查不到该路径时 TestClient 仍返回 200），因此改为发真实请求。
    """
    client, _, _ = env
    response = client.post(
        "/api/v1/experimental-projection",
        json={"camera": CAM, "points": [{"pixel": [960, 540], "observation_id": "route-check"}]},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["layer"] == "experimental_estimate"
    assert data["persisted"] is False
    assert data["points"][0]["spatial_level"] == "L2-candidate"
