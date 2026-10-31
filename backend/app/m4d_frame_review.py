"""M4c -> M4d in-process read-only bridge for the local demo.

No endpoint, persistence, automatic identity link or trajectory mutation.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

from app import m4_video
from app.m4d_case_guard import resolve_case_clip
from app.m4d_experiment import ExperimentSource, ExperimentReview, run_readonly_experiment


class FrameReviewError(ValueError):
    pass


def review_case_clip_frame(db, *, case_id: str, clip_id: str,
                           offset_seconds: float, prompt: str, model: str,
                           client: Any) -> ExperimentReview:
    """Read a verified case-associated clip and send one bounded JPEG to VLM.

    Offset is a requested seek, not a verified frame PTS.
    """
    clip, path = resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
    offset = m4_video.checked_offset(offset_seconds, clip)
    frame = m4_video.fetch_frame(path, clip, offset)
    if frame is None:
        raise FrameReviewError("frame_unavailable")
    jpeg = m4_video.jpeg_preview(frame)
    # Detect changes after decoding and before the model call.
    m4_video.ensure_same_recorded_file(path, clip)
    source = ExperimentSource(
        case_id=case_id,
        media_id=clip_id,
        frame_offset_ms=round(offset * 1000),
        image_sha256=sha256(jpeg).hexdigest(),
    )
    return run_readonly_experiment(jpeg, source=source, prompt=prompt,
                                   model=model, client=client)
