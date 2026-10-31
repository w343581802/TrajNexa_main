"""Offline M4c -> M4d bridge tests. No real video or model service."""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app import m4d_frame_review


JPEG = b"\xff\xd8\xffoffline\xff\xd9"


class FakeClient:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="fake", usage=None, choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                content='{"attributes":[{"name":"clothing_upper","state":"visible","value":"red"}]}',
                refusal=None))])


def test_frame_review_uses_m4c_source_and_never_writes(monkeypatch):
    calls = []
    monkeypatch.setattr(m4d_frame_review, "resolve_case_clip",
                        lambda db, **kw: (SimpleNamespace(id="clip", duration_seconds=20), "path"))
    monkeypatch.setattr(m4d_frame_review.m4_video, "fetch_frame",
                        lambda path, clip, offset: calls.append(("frame", offset)) or "frame")
    monkeypatch.setattr(m4d_frame_review.m4_video, "jpeg_preview", lambda frame: JPEG)
    monkeypatch.setattr(m4d_frame_review.m4_video, "ensure_same_recorded_file",
                        lambda path, clip: calls.append(("verified", path)))
    client = FakeClient()
    result = m4d_frame_review.review_case_clip_frame(
        None, case_id="case", clip_id="clip", offset_seconds=1.25,
        prompt="describe", model="test", client=client)
    assert result.source.frame_offset_ms == 1250
    assert result.source.image_sha256 == sha256(JPEG).hexdigest()
    assert result.attributes.status == "parsed"
    assert result.requires_human_review is True
    assert calls == [("frame", 1.25), ("verified", "path")]
    assert len(client.calls) == 1


def test_unavailable_frame_never_calls_model(monkeypatch):
    monkeypatch.setattr(m4d_frame_review, "resolve_case_clip",
                        lambda db, **kw: (SimpleNamespace(duration_seconds=20), "path"))
    monkeypatch.setattr(m4d_frame_review.m4_video, "fetch_frame",
                        lambda path, clip, offset: None)
    client = FakeClient()
    with pytest.raises(m4d_frame_review.FrameReviewError, match="frame_unavailable"):
        m4d_frame_review.review_case_clip_frame(
            None, case_id="case", clip_id="clip", offset_seconds=1,
            prompt="describe", model="test", client=client)
    assert client.calls == []
