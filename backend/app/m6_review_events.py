"""B4.1-B/C：最小可审计的人工审核事件。

区分三种来源：
  human_review   真实审核，可推进正式主链
  simulated      研发回放模拟，只能写 simulation 结果，永不进入 human_confirmed
  system         规则自动判定（目前只用于记录，不产生确认）

事件不可变：以 event_id 去重，重复提交幂等。
"""
from __future__ import annotations

import uuid

EVENT_TYPES = ("confirm", "reject", "undecided", "revoke")
SOURCES = ("human_review", "simulated", "system")


class ReviewEventError(ValueError):
    """稳定错误码。"""


def review_event(event_type, case_id, target_id, candidate_id, reviewer, reviewed_at,
                 evidence=None, reason=None, source="human_review", event_id=None,
                 supersedes=None):
    if event_type not in EVENT_TYPES:
        raise ReviewEventError("invalid_event_type")
    if source not in SOURCES:
        raise ReviewEventError("invalid_source")
    return {"event_id": event_id or uuid.uuid4().hex[:12],
            "event_type": event_type,
            "case_id": case_id,
            "target_id": target_id,
            "candidate_id": candidate_id,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "evidence": list(evidence or []),
            "reason": reason,
            "source": source,
            "supersedes": supersedes}


def validate_event(event, require_human=True):
    """正式推进必须满足：真人、有证据、非模拟。"""
    if event.get("event_type") not in EVENT_TYPES:
        raise ReviewEventError("invalid_event_type")
    if not event.get("case_id") or not event.get("target_id"):
        raise ReviewEventError("missing_case_or_target")
    if not event.get("candidate_id"):
        raise ReviewEventError("missing_candidate_id")
    if not event.get("reviewed_at"):
        raise ReviewEventError("missing_reviewed_at")
    if require_human:
        if event.get("source") != "human_review":
            raise ReviewEventError("formal_review_requires_human_source")
        if not event.get("reviewer"):
            raise ReviewEventError("missing_reviewer")
        if event.get("event_type") in ("confirm", "reject") and not event.get("evidence"):
            raise ReviewEventError("missing_evidence")
    return True


def apply_events(registry, events, evidence_root=None):
    """把事件应用到真值登记；返回逐条结果，失败的不会改动状态。"""
    registry.setdefault("applied_events", [])
    applied_ids = {e["event_id"] for e in registry["applied_events"]}
    results = []
    for event in events:
        try:
            simulated = event.get("source") == "simulated"
            validate_event(event, require_human=not simulated)
            if simulated and not event.get("reviewer"):
                raise ReviewEventError("simulation_requires_label")
        except ReviewEventError as exc:
            results.append({"event_id": event.get("event_id"), "status": "rejected",
                            "code": str(exc)})
            continue
        if event["event_id"] in applied_ids:
            results.append({"event_id": event["event_id"], "status": "idempotent_skip"})
            continue
        target = None
        for record in registry["observations"]:
            if record["observation_id"] == event["candidate_id"]:
                target = record
                break
        if target is None:
            results.append({"event_id": event["event_id"], "status": "rejected",
                            "code": "candidate_not_found"})
            continue
        if target.get("target_id") != event["target_id"] or target.get("case_id") != event["case_id"]:
            results.append({"event_id": event["event_id"], "status": "rejected",
                            "code": "target_or_case_mismatch"})
            continue
        if evidence_root and event["event_type"] == "confirm":
            missing = [ref for ref in event["evidence"] if not _evidence_exists(ref, evidence_root)]
            if missing:
                results.append({"event_id": event["event_id"], "status": "rejected",
                                "code": "evidence_unresolvable", "missing": missing})
                continue
        state = _apply_one(registry, target, event)
        registry["applied_events"].append(event)
        applied_ids.add(event["event_id"])
        results.append({"event_id": event["event_id"], "status": state,
                        "observation_id": target["observation_id"]})
    return results


def _apply_one(registry, target, event):
    kind = event["event_type"]
    simulated = event.get("source") == "simulated"
    if kind == "confirm":
        if simulated:
            target["simulated_status"] = "simulated_confirmed"
            registry.setdefault("simulation", []).append(target["observation_id"])
            return "simulated_only"
        target["status"] = "human_confirmed"
        target["reviewer"] = event["reviewer"]
        target["evidence"] = list(event["evidence"])
        target["reviewed_at"] = event["reviewed_at"]
        return "confirmed"
    if kind == "reject":
        target["status"] = "human_rejected"
        target["reviewer"] = event["reviewer"]
        target["reject_reason"] = event.get("reason")
        return "rejected"
    if kind == "undecided":
        target["review_note"] = event.get("reason") or "undecided"
        return "kept_candidate"
    if kind == "revoke":
        target["status"] = "revoked"
        target["revoked_reason"] = event.get("reason")
        for record in registry["observations"]:
            if record.get("depends_on") == target["observation_id"]:
                record["status"] = "revoked"
                record["revoked_reason"] = "upstream_revoked"
        return "revoked"
    raise ReviewEventError("invalid_event_type")


def _evidence_exists(reference, root):
    """只校验形如 'file:rel/path' 或 'frame:rel/path' 的引用；其它类型视为元数据。"""
    if not isinstance(reference, str):
        return False
    kind, _, rel = reference.partition(":")
    if kind not in ("file", "frame"):
        return True
    from pathlib import Path
    return (Path(root) / rel).is_file()
