"""M4d in-process, read-only experiment orchestrator.

No HTTP endpoint, database writes, trajectory changes or identity assertions.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.m4d_attributes import AttributeParseResult, parse_attribute_candidates
from app.m4d_vlm import VLMResult, review_jpeg


class ExperimentSourceError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentSource:
    """Source metadata for a case-associated demo media item."""

    case_id: str
    media_id: str
    frame_offset_ms: int
    image_sha256: str


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
    """Validate source metadata and image integrity before model inference."""
    if not source.case_id or not source.case_id.strip():
        raise ExperimentSourceError("case_required")
    if not source.media_id or not source.media_id.strip():
        raise ExperimentSourceError("media_required")
    if isinstance(source.frame_offset_ms, bool) or not isinstance(source.frame_offset_ms, int) or source.frame_offset_ms < 0:
        raise ExperimentSourceError("invalid_frame_offset")
    if not isinstance(image_bytes, bytes) or sha256(image_bytes).hexdigest() != source.image_sha256:
        raise ExperimentSourceError("source_hash_mismatch")
    vlm = review_jpeg(image_bytes, prompt=prompt, model=model, client=client)
    return ExperimentReview(source=source, vlm=vlm, attributes=parse_attribute_candidates(vlm))
