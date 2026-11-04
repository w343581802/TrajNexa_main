"""B8-A: read-only projection of an M6 replay into a future workbench response.

This deliberately does NOT run inference, bind a recording, confirm a target,
or turn a historical research JSON into a production tracking session. The
next step is to expose this projection only from authenticated case-owned
persistent tracking jobs, with review events handled separately.
"""
from __future__ import annotations

from math import isfinite


class TrackingProjectionError(ValueError):
    """Stable validation error for malformed or cross-case M6 result."""


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _score(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise TrackingProjectionError("invalid_candidate_score")
    if not isfinite(float(value)):
        raise TrackingProjectionError("invalid_candidate_score")
    return float(value)


def _candidate(entry: dict) -> dict:
    if not isinstance(entry, dict) or not _text(entry.get("candidate_id")):
        raise TrackingProjectionError("invalid_candidate")
    evidence = entry.get("evidence_refs") or []
    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        raise TrackingProjectionError("invalid_candidate_evidence")
    return {
        "id": entry["candidate_id"],
        "camera_label": entry.get("camera_label"),
        "map_camera_id": entry.get("map_camera_id"),
        "track_id": entry.get("track_id"),
        "state": entry.get("state", "unknown"),
        "appearance_score": _score(entry.get("retrieval_score")),
        "appearance_margin": _score(entry.get("margin_top1_top2")),
        "observed_start": entry.get("observed_start"),
        "observed_end": entry.get("observed_end"),
        "source_window": list(entry.get("window") or []),
        "evidence_refs": list(evidence),
        "evidence_status": "references_unverified",
        "identity_status": "candidate_unverified",
    }


def project_tracking_result(payload: dict, *, case_id: str) -> dict:
    """Project stored M6 results; never promote candidates to confirmed identity.

    case_id must be determined by the server's authorized case record, NOT
    accepted from the browser or from an untrusted JSON file. This is only
    a schema adapter; it does not implement authentication or persistence.
    """
    if not isinstance(payload, dict) or payload.get("schema") != "m6-one-hop-chain/3":
        raise TrackingProjectionError("invalid_m6_result")
    session = payload.get("session")
    anchor = payload.get("anchor")
    if not isinstance(session, dict) or not isinstance(anchor, dict):
        raise TrackingProjectionError("invalid_m6_result")
    requested_case_id = _text(case_id)
    source_case_id = _text(session.get("case_id"))
    if not requested_case_id or source_case_id != requested_case_id:
        raise TrackingProjectionError("case_scope_mismatch")
    if _text(anchor.get("case_id")) != source_case_id:
        raise TrackingProjectionError("anchor_case_mismatch")
    if not _text(session.get("target_id")) or (
        _text(anchor.get("target_id")) != _text(session.get("target_id"))
    ):
        raise TrackingProjectionError("target_scope_mismatch")
    candidates = payload.get("candidate_pool") or []
    steps = payload.get("steps") or []
    if not isinstance(candidates, list) or not isinstance(steps, list):
        raise TrackingProjectionError("invalid_m6_result")

    result_candidates = [_candidate(c) for c in candidates]
    ids = [c["id"] for c in result_candidates]
    if len(ids) != len(set(ids)):
        raise TrackingProjectionError("duplicate_candidate_id")
    windows = []
    for step in steps:
        if not isinstance(step, dict):
            raise TrackingProjectionError("invalid_step")
        windows.append({
            "camera_label": step.get("camera"),
            "time_window": list(step.get("scheduled_window") or []),
            "state": step.get("state", "unknown"),
            "tracks_observed": step.get("tracks"),
        })
    main_chain = session.get("main_chain") or []
    if not isinstance(main_chain, list):
        raise TrackingProjectionError("invalid_main_chain")

    return {
        "schema": "m6-workbench-projection/1",
        "case_id": requested_case_id,
        "target_id": session["target_id"],
        "session_state": session.get("status", "unknown"),
        "session_reason": session.get("reason"),
        "main_chain": list(main_chain),
        "main_chain_source": "m6_session_only",
        "anchor": {
            "camera_label": anchor.get("camera_label"),
            "camera_uuid": anchor.get("camera_uuid"),
            "map_camera_id": anchor.get("map_camera_id"),
            "tracklet_id": anchor.get("tracklet_id"),
            "observed_start": anchor.get("observed_start"),
            "observed_end": anchor.get("observed_end"),
            "binding_status": anchor.get("binding_status"),
        },
        "candidate_pool": result_candidates,
        "windows": windows,
        "model_provenance": payload.get("model_provenance") or {},
        "clock_quality": payload.get("clock_quality", "unknown"),
        "binding_status": payload.get("binding_status", "unknown"),
        "exploratory": bool(payload.get("exploratory", True)),
        "identity_established_by_projection": False,
        "review_required": bool(result_candidates) or session.get("status") == "awaiting_review",
        "note": "Read-only result view. Review/confirmation must use authorized M6 review events.",
    }
