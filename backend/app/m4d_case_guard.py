"""Trusted-server-side case/clip guard for future M4d integration.

Does not authenticate a user. The caller must provide a real authorization
function bound to the authenticated principal; never accept client booleans.
"""
from __future__ import annotations

from typing import Callable

from app import m4_video


class M4dAccessError(ValueError):
    pass


def resolve_authorized_clip(db, *, case_id: str, clip_id: str,
                            can_read_case: Callable[[str], bool]):
    """Check authorization before resolving a case-owned, unchanged clip.

    Return (clip, path); never expose existence of unauthorized clips.
    """
    if not callable(can_read_case) or not isinstance(case_id, str) or not case_id:
        raise M4dAccessError("access_denied")
    try:
        allowed = can_read_case(case_id)
    except Exception:
        raise M4dAccessError("access_denied") from None
    if allowed is not True:
        raise M4dAccessError("access_denied")
    try:
        return m4_video.resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
    except m4_video.KeyframeError:
        raise M4dAccessError("clip_unavailable") from None
