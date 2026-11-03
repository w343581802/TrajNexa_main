"""S6-15R2：摄像头转移关系图（只记录被观察到并核实的先后关系）。

语义分层（design/47 §2 P0-C）：
  reviewable()            记录本身可被人工查看
  is_human_confirmed()    有可追溯的人工确认来源
  eligible_for_statistics() 才能进 support_count / 时间统计
  synthetic=True          仅为测试/fixture，永不进统计

其它硬约束：
  - 去重按独立确认事件 association_event_id，不按局部 track_id；
  - 撤销沿 depends_on 递归传播，带环检测与幂等；
  - 名义 OSD 间隔只在 clock_quality 明确时给出，且标注未校准。
"""
from __future__ import annotations

import json
import uuid

REVIEW_STATES = ("pending_review", "human_confirmed", "human_rejected", "revoked")
MIN_SUPPORT_FOR_DISTRIBUTION = 5


def new_transition(source_camera, target_camera, source_track=None, target_track=None,
                   observed_gap_seconds=None, clock_quality="uncalibrated",
                   movement_mode="unknown", exit_side=None, review_state="pending_review",
                   evidence=None, spatial_revision=None, target_id=None,
                   association_event_id=None, reviewer=None, synthetic=False):
    return {"transition_id": uuid.uuid4().hex[:12],
            "target_id": target_id,
            "source_camera": source_camera,
            "target_camera": target_camera,
            "source_track": source_track,
            "target_track": target_track,
            "association_event_id": association_event_id,
            "observed_gap_seconds": observed_gap_seconds,
            "clock_quality": clock_quality,
            "movement_mode": movement_mode,
            "exit_side": exit_side,
            "review_state": review_state,
            "reviewer": reviewer,
            "spatial_revision": spatial_revision,
            "evidence": list(evidence or []),
            "synthetic": bool(synthetic),
            "revoked_reason": None,
            "depends_on": None}


def reviewable(record):
    return record.get("review_state") in REVIEW_STATES


def is_human_confirmed(record):
    return (record.get("review_state") == "human_confirmed"
            and not record.get("synthetic")
            and bool(record.get("reviewer")))


def eligible_for_statistics(record):
    """只有已确认、非合成、带确认来源与证据的记录可进入统计。"""
    return is_human_confirmed(record) and bool(record.get("evidence"))


def record_transition(graph, record):
    if record["review_state"] not in REVIEW_STATES:
        raise ValueError("invalid_review_state")
    if record["review_state"] == "human_confirmed" and not record.get("reviewer"):
        raise ValueError("confirmation_requires_reviewer")
    graph.setdefault("transitions", []).append(record)
    return record


def revoke(graph, transition_id, reason, _seen=None):
    """递归撤销，含环检测与幂等；审计记录保留在 revoked_reason/log。"""
    seen = _seen or set()
    if transition_id in seen:
        return None
    seen.add(transition_id)
    hit = None
    for record in graph["transitions"]:
        if record["transition_id"] == transition_id:
            if record["review_state"] != "revoked":
                record["review_state"] = "revoked"
                record["revoked_reason"] = reason
                graph.setdefault("audit", []).append(
                    {"transition_id": transition_id, "action": "revoked", "reason": reason})
            hit = record
    if hit is None:
        raise ValueError("transition_not_found")
    for record in graph["transitions"]:
        if record.get("depends_on") == transition_id:
            revoke(graph, record["transition_id"], "upstream_revoked", seen)
    return hit


def transitions_between(graph, source_camera, target_camera, include_revoked=False):
    return [r for r in graph["transitions"]
            if r["source_camera"] == source_camera and r["target_camera"] == target_camera
            and (include_revoked or r["review_state"] != "revoked")]


def support_count(graph, source_camera, target_camera):
    """独立确认事件数：按 association_event_id 去重，同一目标重复帧只算一次。"""
    events = set()
    for record in transitions_between(graph, source_camera, target_camera):
        if not eligible_for_statistics(record):
            continue
        events.add(record.get("association_event_id") or record["transition_id"])
    return len(events)


def transition_time_summary(graph, source_camera, target_camera):
    records = [r for r in transitions_between(graph, source_camera, target_camera)
               if eligible_for_statistics(r) and r.get("observed_gap_seconds") is not None]
    gaps = [r["observed_gap_seconds"] for r in records]
    if len(gaps) < MIN_SUPPORT_FOR_DISTRIBUTION:
        return {"support_count": len(gaps), "status": "insufficient_support",
                "min_required": MIN_SUPPORT_FOR_DISTRIBUTION}
    gaps = sorted(gaps)
    return {"support_count": len(gaps), "status": "observed_range_only",
            "min_gap_seconds": gaps[0], "max_gap_seconds": gaps[-1],
            "median_gap_seconds": gaps[len(gaps) // 2],
            "clock_quality": sorted({r.get("clock_quality", "unknown") for r in records}),
            "note": "名义 OSD 间隔，未经跨机钟差校准，不代表物理通行时间"}


def load_graph(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_graph(graph, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(graph, handle, ensure_ascii=False, indent=1)
    return path
