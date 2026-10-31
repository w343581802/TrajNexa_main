"""Offline M4d HTTP contract tests; never call an actual model."""
from types import SimpleNamespace

from app import m4d_api
from app.config import settings
from app.m4d_attributes import AttributeCandidate, AttributeParseResult
from app.m4d_experiment import ExperimentReview, ExperimentSource
from app.m4d_vlm import VLMResult


def test_disabled_by_default(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "m4d_enabled", False)
    response = client.post("/api/v1/cases/a/recordings/clips/b/vlm/preview",
                           json={"offset_seconds": 0.5})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "m4d-disabled"


def test_readonly_preview_returns_unverified_candidates(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "m4d_enabled", True)
    monkeypatch.setattr(settings, "m4d_model", "local-vision")
    monkeypatch.setattr(m4d_api, "make_client", lambda: object())
    calls = []
    def fake_review(db, **kwargs):
        calls.append(kwargs)
        return ExperimentReview(
            source=ExperimentSource(case_id="case", media_id="clip",
                                    frame_offset_ms=1250, image_sha256="a" * 64),
            vlm=VLMResult("completed", "local-vision", "a" * 64),
            attributes=AttributeParseResult(status="parsed", candidates=(
                AttributeCandidate(name="clothing_upper", state="visible",
                                   value="red", source="vlm_unverified"),)),
        )
    monkeypatch.setattr(m4d_api.m4d_frame_review, "review_case_clip_frame", fake_review)
    response = client.post("/api/v1/cases/case/recordings/clips/clip/vlm/preview",
                           json={"offset_seconds": 1.25})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["identity_established"] is False
    assert data["requires_human_review"] is True
    assert data["attributes"][0]["source"] == "vlm_unverified"
    assert calls[0]["offset_seconds"] == 1.25


def test_model_required(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, "m4d_enabled", True)
    monkeypatch.setattr(settings, "m4d_model", "")
    response = client.post("/api/v1/cases/a/recordings/clips/b/vlm/preview",
                           json={"offset_seconds": 0.5})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "m4d-model-required"
