from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import m4_video
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
