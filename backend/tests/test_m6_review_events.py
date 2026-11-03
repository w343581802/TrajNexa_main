import pytest

from app.m6_review_events import ReviewEventError, apply_events, review_event, validate_event
from app.m6_truth_registry import add_observation, observation


def _registry():
    reg = {"case_id": "purple", "observations": []}
    record = observation("purple", "purple", "uuid-2-1", "9000000000000058", "37", 54012.6,
                         recording_id="rec-2-1", evidence=["frame:2-1/0079.jpg"])
    add_observation(reg, record)
    return reg, record["observation_id"]


def _event(kind="confirm", source="human_review", reviewer="case-owner", evidence=None,
           obs_id=None):
    if obs_id is None:
        obs_id = _registry()[1]
    return review_event(kind, "purple", "purple", obs_id, reviewer, "2026-11-03T12:00:00Z",
                        evidence=evidence if evidence is not None else ["frame:2-1/0079.jpg"],
                        source=source, reason="looks_like_target")


def test_formal_confirm_requires_human_source():
    event = _event(source="simulated")
    with pytest.raises(ReviewEventError):
        validate_event(event, require_human=True)


def test_simulated_event_never_becomes_human_confirmed():
    reg, obs_id = _registry()
    results = apply_events(reg, [_event(source="simulated", obs_id=obs_id)])
    assert results[0]["status"] == "simulated_only"
    record = reg["observations"][0]
    assert record["status"] == "candidate"
    assert record.get("simulated_status") == "simulated_confirmed"


def test_human_confirm_advances_registry():
    reg, obs_id = _registry()
    results = apply_events(reg, [_event(obs_id=obs_id)])
    assert results[0]["status"] == "confirmed"
    assert reg["observations"][0]["status"] == "human_confirmed"
    assert reg["observations"][0]["reviewer"] == "case-owner"


def test_duplicate_event_is_idempotent():
    reg, obs_id = _registry()
    event = _event(obs_id=obs_id)
    apply_events(reg, [event])
    assert apply_events(reg, [event])[0]["status"] == "idempotent_skip"


def test_cross_target_event_is_rejected():
    reg, obs_id = _registry()
    event = review_event("confirm", "purple", "other-target", obs_id, "case-owner",
                         "2026-11-03T12:00:00Z", evidence=["frame:2-1/0079.jpg"])
    results = apply_events(reg, [event])
    assert results[0]["code"] == "target_or_case_mismatch"
    assert reg["observations"][0]["status"] == "candidate"


def test_unresolvable_evidence_blocks_confirmation(tmp_path):
    reg, obs_id = _registry()
    results = apply_events(reg, [_event(obs_id=obs_id, evidence=["frame:missing.jpg"])],
                           evidence_root=str(tmp_path))
    assert results[0]["code"] == "evidence_unresolvable"
    assert reg["observations"][0]["status"] == "candidate"


def test_revoke_cascades():
    reg, obs_id = _registry()
    apply_events(reg, [_event(obs_id=obs_id)])
    revoke = review_event("revoke", "purple", "purple", obs_id, "case-owner",
                          "2026-11-03T13:00:00Z", reason="wrong_person")
    results = apply_events(reg, [revoke])
    assert results[0]["status"] == "revoked"
    assert reg["observations"][0]["status"] == "revoked"
