"""S6-15R2：摄像头转移关系图（只记被观察到的先后关系，不估精确概率）。

防偏差：
  - support_count=1 只表示"观察到过一次"，不产生概率；
  - 名义 OSD 间隔不等于物理通行时间，记录里保留 clock_quality；
  - 未查询的镜头不是负样本（不参与统计）；
  - 撤销后依赖它的记录降级/失效，审计原文保留。
"""
from __future__ import annotations

import json
import uuid

REVIEW_STATES = ("pending_review", "human_confirmed", "human_rejected", "revoked")
MIN_SUPPORT_FOR_DISTRIBUTION = 5


def new_transition(source_camera, target_camera, source_track=None, target_track=None,
                   observed_gap_seconds=None, clock_quality="uncalibrated",
                   movement_mode="unknown", exit_side=None, review_state="pending_review",
                   evidence=None, spatial_revision=None):
    return {"transition_id": uuid.uuid4().hex[:12],
            "source_camera": source_camera,
            "target_camera": target_camera,
            "source_track": source_track,
            "target_track": target_track,
            "observed_gap_seconds": observed_gap_seconds,
            "clock_quality": clock_quality,
            "movement_mode": movement_mode,
            "exit_side": exit_side,
            "review_state": review_state,
            "spatial_revision": spatial_revision,
            "evidence": list(evidence or []),
            "revoked_reason": None,
            "depends_on": None}


def usable(record):
    return record["review_state"] not in ("revoked", "human_rejected")


def record_transition(graph, record):
    if record["review_state"] not in REVIEW_STATES:
        raise ValueError("invalid_review_state")
    graph.setdefault("transitions", []).append(record)
    return record


def revoke(graph, transition_id, reason):
    """撤销方向：依赖该关联的派生记录一并失效，原始记录保留。"""
    hit = None
    for record in graph["transitions"]:
        if record["transition_id"] == transition_id:
            record["review_state"] = "revoked"
            record["revoked_reason"] = reason
            hit = record
    if hit is None:
        raise ValueError("transition_not_found")
    for record in graph["transitions"]:
        if record.get("depends_on") == transition_id:
            record["review_state"] = "revoked"
            record["revoked_reason"] = "upstream_revoked"
    return hit


def transitions_between(graph, source_camera, target_camera, include_revoked=False):
    return [r for r in graph["transitions"]
            if r["source_camera"] == source_camera and r["target_camera"] == target_camera
            and (include_revoked or usable(r))]


def support_count(graph, source_camera, target_camera):
    """独立样本数：同一目标重复帧不重复计（按 source_track 去重）。"""
    seen = set()
    for record in transitions_between(graph, source_camera, target_camera):
        key = record.get("source_track") or record["transition_id"]
        seen.add(key)
    return len(seen)


def transition_time_summary(graph, source_camera, target_camera):
    """样本不足时明确返回 insufficient，不给伪精确概率。"""
    records = transitions_between(graph, source_camera, target_camera)
    gaps = [r["observed_gap_seconds"] for r in records
            if r.get("observed_gap_seconds") is not None]
    if len(gaps) < MIN_SUPPORT_FOR_DISTRIBUTION:
        return {"support_count": len(gaps), "status": "insufficient_support",
                "min_required": MIN_SUPPORT_FOR_DISTRIBUTION}
    gaps = sorted(gaps)
    return {"support_count": len(gaps), "status": "observed_range_only",
            "min_gap_seconds": gaps[0], "max_gap_seconds": gaps[-1],
            "median_gap_seconds": gaps[len(gaps) // 2],
            "note": "名义 OSD 间隔，未经跨机钟差校准",
            "clock_quality": sorted({r.get("clock_quality", "unknown") for r in records})}


def load_graph(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_graph(graph, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(graph, handle, ensure_ascii=False, indent=1)
    return path
