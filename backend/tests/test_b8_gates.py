"""B8 治理开关：关闭实验层时端点不可见，正式路径不受影响。"""
from app.config import settings
from app import b8_gates


def _off(monkeypatch):
    monkeypatch.setattr(settings, "b8_experimental_geo_display", False)


def test_flag_defaults_keep_auto_paths_off():
    # 自动标定与自动地理发布永远默认关闭；实验层在演示阶段默认开启。
    assert b8_gates.auto_geo_publish_enabled() is False
    assert settings.b8_auto_geo_publish_enabled is False
    assert settings.b8_auto_calibration_enabled is False


def test_experimental_projection_hidden_when_disabled(env, monkeypatch):
    _off(monkeypatch)
    client, _, _ = env
    response = client.post("/api/v1/experimental-projection",
                           json={"camera": {"width": 1920, "height": 1080, "hfov_deg": 64.0},
                                 "points": [{"pixel": [960, 540]}]})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "experimental_geo_display_disabled"


def test_target_track_hidden_when_disabled(env, monkeypatch):
    _off(monkeypatch)
    client, _, _ = env
    response = client.get("/api/v1/target-track/sample")
    assert response.status_code == 404


def test_track_sample_hidden_when_disabled(env, monkeypatch):
    _off(monkeypatch)
    client, _, _ = env
    assert client.get("/api/v1/track-spatial/sample").status_code == 404


def test_enabled_flag_keeps_routes_visible(env):
    client, _, _ = env
    assert settings.b8_experimental_geo_display is True
    assert client.post("/api/v1/experimental-projection",
                       json={"camera": {"width": 1920, "height": 1080, "hfov_deg": 64.0},
                             "points": [{"pixel": [960, 540]}]}).status_code == 200