"""Strict experimental VLM attribute parsing; never establishes identity."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

_ALLOWED = frozenset({"clothing_upper", "clothing_lower", "carried_item", "visible_motion"})
_STATES = frozenset({"visible", "unknown", "not_visible", "uncertain"})


@dataclass(frozen=True)
class AttributeCandidate:
    name: str
    state: Literal["visible", "unknown", "not_visible", "uncertain"]
    value: str | None
    source: str = "vlm_unverified"


@dataclass(frozen=True)
class AttributeParseResult:
    status: str  # parsed | invalid | unavailable
    candidates: tuple[AttributeCandidate, ...] = ()
    error_code: str | None = None
    requires_human_review: bool = True


def parse_attribute_candidates(vlm_result) -> AttributeParseResult:
    """Accept a narrowly defined JSON object only from completed model calls.

    No free-text inference, identity matching, evidence persistence or auto-confirmation.
    """
    if getattr(vlm_result, "status", None) != "completed":
        return AttributeParseResult("unavailable", error_code="model_not_completed")
    text = getattr(vlm_result, "text", None)
    if not isinstance(text, str) or len(text) > 8192:
        return AttributeParseResult("invalid", error_code="invalid_payload")
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return AttributeParseResult("invalid", error_code="invalid_json")
    if not isinstance(payload, dict) or set(payload) != {"attributes"}:
        return AttributeParseResult("invalid", error_code="invalid_schema")
    entries = payload["attributes"]
    if not isinstance(entries, list) or len(entries) > len(_ALLOWED):
        return AttributeParseResult("invalid", error_code="invalid_schema")
    parsed = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"name", "state", "value"}:
            return AttributeParseResult("invalid", error_code="invalid_schema")
        name, state, value = item["name"], item["state"], item["value"]
        if not isinstance(name, str) or name not in _ALLOWED or name in seen:
            return AttributeParseResult("invalid", error_code="invalid_attribute")
        if not isinstance(state, str) or state not in _STATES:
            return AttributeParseResult("invalid", error_code="invalid_state")
        if state == "visible":
            if not isinstance(value, str) or not value.strip() or len(value) > 120:
                return AttributeParseResult("invalid", error_code="invalid_value")
            value = value.strip()
        elif value is not None:
            return AttributeParseResult("invalid", error_code="unknown_must_be_null")
        parsed.append(AttributeCandidate(name, state, value))
        seen.add(name)
    return AttributeParseResult("parsed", tuple(parsed))
