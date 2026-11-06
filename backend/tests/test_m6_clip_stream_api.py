from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import m4_video, m6_clip_proxy
from app.config import settings
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "m4c_enabled", True)
    monkeypatch.setattr(settings, "m4a_enabled", True)
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"0123456789abcdef")
    clip = SimpleNamespace(duration_seconds=10.0, id="clip-1")
    monkeypatch.setattr(m4_video, "resolve_case_clip",
                        lambda db, *, case_id, clip_id: (clip, media))
    return TestClient(app)


def test_video_stream_returns_range_slice(client):
    response = client.get("/api/v1/cases/c1/recordings/clips/clip-1/video",
                          headers={"Range": "bytes=4-7"})
    assert response.status_code == 206
    assert response.content == b"4567"
    assert response.headers["content-range"] == "bytes 4-7/16"
    assert response.headers["accept-ranges"] == "bytes"
    assert "path" not in response.headers


def test_video_stream_full_body_when_no_range(client):
    response = client.get("/api/v1/cases/c1/recordings/clips/clip-1/video")
    assert response.status_code == 200
    assert response.content == b"0123456789abcdef"


def test_video_stream_rejects_unsatisfiable_range(client):
    response = client.get("/api/v1/cases/c1/recordings/clips/clip-1/video",
                          headers={"Range": "bytes=99-120"})
    assert response.status_code == 416


def test_open_ended_range_is_capped(client):
    response = client.get("/api/v1/cases/c1/recordings/clips/clip-1/video",
                          headers={"Range": "bytes=0-"})
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-15/16"


def test_proxy_video_serves_transcoded_copy(client, tmp_path, monkeypatch):
    proxy = tmp_path / "proxy.mp4"
    proxy.write_bytes(b"proxy-bytes-0123456789")
    calls = {}

    def fake_build(path, *, clip_id, start, duration):
        calls.update(path=str(path), clip_id=clip_id, start=start, duration=duration)
        return proxy

    monkeypatch.setattr(m6_clip_proxy, "build_proxy", fake_build)
    response = client.get(
        "/api/v1/cases/c1/recordings/clips/clip-1/proxy-video?start=2&duration=20",
        headers={"Range": "bytes=0-4"})
    assert response.status_code == 206
    assert response.content == b"proxy"
    assert response.headers["content-range"] == "bytes 0-4/22"
    assert response.headers["x-clip-time-semantics"].startswith("proxy-h264-reencoded")
    assert response.headers["x-proxy-start-seconds"] == "2.00"
    assert calls["clip_id"] == "clip-1"
    assert calls["start"] == 2.0 and calls["duration"] == 20.0


def test_proxy_video_reports_transcode_failure(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise m6_clip_proxy.ProxyError("proxy_transcode_failed")

    monkeypatch.setattr(m6_clip_proxy, "build_proxy", boom)
    response = client.get("/api/v1/cases/c1/recordings/clips/clip-1/proxy-video")
    assert response.status_code == 503
    assert "proxy-transcode-failed" in response.text
