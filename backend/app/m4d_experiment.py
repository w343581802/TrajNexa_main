"""M4d in-process, read-only experiment orchestrator.

No HTTP endpoint, database writes, trajectory changes or identity assertions.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.m4d_attributes import AttributeParseResult, parse_attribute_candidates
from app.m4d_vlm import VLMResult, review_jpeg


class ExperimentAuthorizationError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentSource:
    """Verified source metadata supplied by a trusted case-authorization layer."""

    case_id: str
    media_id: str
    frame_offset_ms: int
    image_sha256: str
    authorization_checked: bool = False


@dataclass(frozen=True)
class ExperimentReview:
    source: ExperimentSource
    vlm: VLMResult
    attributes: AttributeParseResult
    requires_human_review: bool = True


def run_readonly_experiment(
    image_bytes: bytes,
    *,
    source: ExperimentSource,
    prompt: str,
    model: str,
    client: Any,
) -> ExperimentReview:
    """Requires verified authorization and source hash before any model request.

    authorization_checked is a trusted server-side assertion, not user-provided input.
    Caller must enforce case ownership, model data residency and privacy policy.
    """
    if not source.authorization_checked:
        raise ExperimentAuthorizationError("authorization_required")
    if not source.case_id or not source.case_id.strip():
        raise ExperimentAuthorizationError("case_required")
    if not source.media_id or not source.media_id.strip():
        raise ExperimentAuthorizationError("media_required")
    if isinstance(source.frame_offset_ms, bool) or not isinstance(source.frame_offset_ms, int) or source.frame_offset_ms < 0:
        raise ExperimentAuthorizationError("invalid_frame_offset")
    if not isinstance(image_bytes, bytes) or sha256(image_bytes).hexdigest() != source.image_sha256:
        raise ExperimentAuthorizationError("source_hash_mismatch")
    vlm = review_jpeg(image_bytes, prompt=prompt, model=model, client=client)
    return ExperimentReview(source=source, vlm=vlm, attributes=parse_attribute_candidates(vlm))
