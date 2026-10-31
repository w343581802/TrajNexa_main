"""M4d experimental VLM adapter; no business writes or identity decisions.

Design reference: Rebucca app/utils/LLMUtils.py (MIT).
This module is a new implementation, not a copied source file.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class VLMResult:
    status: str  # completed | refused | incomplete | empty | error
    model: str
    image_sha256: str
    text: str | None = None
    error_code: str | None = None
    request_id: str | None = None
    usage: dict[str, Any] | None = None
    prompt_sha256: str | None = None
    finish_reason: str | None = None


class VLMInputError(ValueError):
    pass


def review_jpeg(
    image_bytes: bytes,
    *,
    prompt: str,
    model: str,
    client: Any,
    max_image_bytes: int = 8 * 1024 * 1024,
) -> VLMResult:
    """Call an injected OpenAI-compatible client; never persist data or infer identity.

    Caller must verify case ownership, processing authorization, and source metadata.
    This function is deliberately not an HTTP endpoint.
    """
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise VLMInputError("image_required")
    if len(image_bytes) > max_image_bytes:
        raise VLMInputError("image_too_large")
    if not (image_bytes.startswith(b"\xff\xd8\xff") and image_bytes.endswith(b"\xff\xd9")):
        raise VLMInputError("jpeg_required")
    if not isinstance(prompt, str) or not prompt.strip():
        raise VLMInputError("prompt_required")
    if not isinstance(model, str) or not model.strip():
        raise VLMInputError("model_required")
    digest = sha256(image_bytes).hexdigest()
    prompt_digest = sha256(prompt.encode('utf-8')).hexdigest()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded}},
                {"type": "text", "text": prompt},
            ]}],
        )
        choice = response.choices[0]
        content = choice.message.content
        finish_reason = getattr(choice, 'finish_reason', None)
        request_id = getattr(response, "id", None)
        raw_usage = getattr(response, "usage", None)
        usage = raw_usage.model_dump() if hasattr(raw_usage, "model_dump") else None
        metadata = dict(request_id=request_id, usage=usage, prompt_sha256=prompt_digest, finish_reason=finish_reason)
        if finish_reason == 'content_filter' or getattr(choice.message, 'refusal', None):
            return VLMResult('refused', model, digest, **metadata)
        if finish_reason != 'stop':
            return VLMResult('incomplete', model, digest, **metadata)
        if not isinstance(content, str) or not content.strip():
            return VLMResult('empty', model, digest, **metadata)
        return VLMResult('completed', model, digest, text=content, **metadata)
    except Exception as exc:
        # Never persist raw exception text: SDK errors may contain sensitive request details.
        return VLMResult("error", model, digest, error_code=type(exc).__name__, prompt_sha256=prompt_digest)
