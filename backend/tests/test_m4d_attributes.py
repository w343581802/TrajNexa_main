"""Strict VLM output contract tests. Fake responses, no model/network."""
import json
from types import SimpleNamespace

import pytest

from app.m4d_attributes import parse_attribute_candidates


def result(payload, status="completed"):
    return SimpleNamespace(status=status, text=payload if isinstance(payload, str) else json.dumps(payload))


def test_visible_and_unknown_remain_unverified():
    parsed = parse_attribute_candidates(result({"attributes": [
        {"name": "clothing_upper", "state": "visible", "value": "red jacket"},
        {"name": "carried_item", "state": "unknown", "value": None},
    ]}))
    assert parsed.status == "parsed"
    assert parsed.requires_human_review
    assert parsed.candidates[0].source == "vlm_unverified"
    assert parsed.candidates[1].value is None


@pytest.mark.parametrize("status", ["refused", "incomplete", "empty", "error"])
def test_noncompleted_response_never_yields_candidates(status):
    parsed = parse_attribute_candidates(result({"attributes": [
        {"name": "clothing_upper", "state": "visible", "value": "red"}
    ]}, status))
    assert parsed.status == "unavailable"
    assert not parsed.candidates


@pytest.mark.parametrize("payload", [
    "The person wears a red jacket.",
    '{"attributes": [}',
    {"identity": "same person", "attributes": []},
    {"attributes": [{"name": "face_identity", "state": "visible", "value": "Alice"}]},
    {"attributes": [{"name": "clothing_upper", "state": "unknown", "value": "red"}]},
    {"attributes": [{"name": "clothing_upper", "state": "visible", "value": ""}]},
    {"attributes": [{"name": "clothing_upper", "state": "visible", "value": "red"},
                    {"name": "clothing_upper", "state": "visible", "value": "blue"}]},
])
def test_invalid_payload_is_rejected(payload):
    parsed = parse_attribute_candidates(result(payload))
    assert parsed.status == "invalid"
    assert parsed.candidates == ()


def test_empty_list_is_not_positive_observation():
    parsed = parse_attribute_candidates(result({"attributes": []}))
    assert parsed.status == "parsed"
    assert parsed.candidates == ()
