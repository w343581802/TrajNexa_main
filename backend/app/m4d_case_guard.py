"""Case/clip association and unchanged-file guard for the TrajNexa demo.

This is not a user authorization layer; no login, RBAC or consent checkbox.
"""
from __future__ import annotations

from app import m4_video


class M4dClipError(ValueError):
    pass


def resolve_case_clip(db, *, case_id: str, clip_id: str):
    """Reuse M4c's case ownership, path containment and file-change checks."""
    if not isinstance(case_id, str) or not case_id.strip():
        raise M4dClipError("case_required")
    if not isinstance(clip_id, str) or not clip_id.strip():
        raise M4dClipError("clip_required")
    try:
        return m4_video.resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
    except m4_video.KeyframeError:
        raise M4dClipError("clip_unavailable") from None
