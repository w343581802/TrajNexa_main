"""M4d demo case/clip guard tests; no database or video files."""
import pytest

from app import m4d_case_guard, m4_video


def test_case_clip_passes_ids_to_existing_resolver(monkeypatch):
    calls = []
    def resolver(db, *, case_id, clip_id):
        calls.append((case_id, clip_id))
        return "clip", "path"
    monkeypatch.setattr(m4_video, "resolve_case_clip", resolver)
    result = m4d_case_guard.resolve_case_clip(None, case_id="case-a", clip_id="clip-a")
    assert result == ("clip", "path")
    assert calls == [("case-a", "clip-a")]


@pytest.mark.parametrize("case_id,clip_id", [("", "clip-a"), ("case-a", ""), (None, "clip-a")])
def test_invalid_ids_rejected_before_resolution(monkeypatch, case_id, clip_id):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected resolver call")
    monkeypatch.setattr(m4_video, "resolve_case_clip", forbidden)
    with pytest.raises(m4d_case_guard.M4dClipError):
        m4d_case_guard.resolve_case_clip(None, case_id=case_id, clip_id=clip_id)


def test_other_case_or_changed_clip_is_unavailable(monkeypatch):
    def resolver(db, *, case_id, clip_id):
        raise m4_video.KeyframeError("m4c_clip_not_found")
    monkeypatch.setattr(m4_video, "resolve_case_clip", resolver)
    with pytest.raises(m4d_case_guard.M4dClipError, match="clip_unavailable"):
        m4d_case_guard.resolve_case_clip(None, case_id="case-a", clip_id="clip-b")
