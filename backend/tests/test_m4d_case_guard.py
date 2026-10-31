"""M4d guard tests use a fake resolver, no DB or real video."""
import pytest

from app import m4d_case_guard, m4_video


def test_denied_before_clip_resolution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("clip resolution must not occur")
    monkeypatch.setattr(m4_video, "resolve_case_clip", forbidden)
    with pytest.raises(m4d_case_guard.M4dAccessError, match="access_denied"):
        m4d_case_guard.resolve_authorized_clip(None, case_id="case-a", clip_id="clip-a",
                                               can_read_case=lambda case: False)


def test_authorized_case_passes_case_id_to_resolver(monkeypatch):
    calls = []
    def resolver(db, *, case_id, clip_id):
        calls.append((case_id, clip_id))
        return "clip", "path"
    monkeypatch.setattr(m4_video, "resolve_case_clip", resolver)
    result = m4d_case_guard.resolve_authorized_clip(None, case_id="case-a", clip_id="clip-a",
                                                    can_read_case=lambda case: case == "case-a")
    assert result == ("clip", "path")
    assert calls == [("case-a", "clip-a")]


def test_resolver_error_is_sanitized(monkeypatch):
    def resolver(db, *, case_id, clip_id):
        raise m4_video.KeyframeError("m4c_clip_not_found")
    monkeypatch.setattr(m4_video, "resolve_case_clip", resolver)
    with pytest.raises(m4d_case_guard.M4dAccessError, match="clip_unavailable"):
        m4d_case_guard.resolve_authorized_clip(None, case_id="case-a", clip_id="clip-b",
                                               can_read_case=lambda case: True)


def test_authorizer_exception_fails_closed(monkeypatch):
    def broken(case_id):
        raise RuntimeError("secret")
    with pytest.raises(m4d_case_guard.M4dAccessError, match="access_denied"):
        m4d_case_guard.resolve_authorized_clip(None, case_id="case-a", clip_id="clip-a",
                                               can_read_case=broken)
