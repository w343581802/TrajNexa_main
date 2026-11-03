"""S6-15V：真值登记与绑定复核包（B3）。

目的不是再造一个数据库，而是把"什么算已核实"写成可校验的数据：
  - 每条观测必须能追到录像、镜头、Tracklet、OSD 锚点与证据引用；
  - human_confirmed 必须有核验者与证据，否则拒绝入库；
  - 绑定未核实时整包标 exploratory；
  - 三镜头要被声明为"确认"，必须有各自独立证据，不能靠推导合成。
"""
from __future__ import annotations

import json

STATUSES = ("candidate", "human_confirmed", "human_rejected", "revoked")
BINDING_STATES = ("pending_human_review", "human_confirmed", "rejected", "unknown")


class TruthPackageError(ValueError):
    """稳定的错误码，便于测试与审计。"""


def observation(case_id, target_id, camera_uuid, map_camera_id, tracklet_id,
                osd_anchor_seconds, recording_id, osd_uncertainty_seconds=None,
                reviewer=None, evidence=None, status="candidate",
                binding_status="pending_human_review", clock_quality="uncalibrated",
                source="human_review", synthetic=False, note=None):
    return {"observation_id": "%s/%s/%s" % (case_id, camera_uuid, tracklet_id),
            "case_id": case_id, "target_id": target_id,
            "camera_uuid": camera_uuid, "map_camera_id": map_camera_id,
            "tracklet_id": str(tracklet_id),
            "recording_id": recording_id,
            "osd_anchor_seconds": osd_anchor_seconds,
            "osd_uncertainty_seconds": osd_uncertainty_seconds,
            "clock_quality": clock_quality,
            "binding_status": binding_status,
            "status": status,
            "reviewer": reviewer,
            "evidence": list(evidence or []),
            "source": source,
            "synthetic": bool(synthetic),
            "revoked_reason": None,
            "note": note}


def _require(record, cond, code):
    if not cond:
        raise TruthPackageError(code)


def add_observation(registry, record):
    _require(record, record["status"] in STATUSES, "invalid_status")
    _require(record, record["binding_status"] in BINDING_STATES, "invalid_binding_status")
    _require(record, record.get("osd_anchor_seconds") is not None, "missing_osd_anchor")
    _require(record, bool(record.get("recording_id")), "missing_recording_id")
    _require(record, bool(record.get("evidence")), "missing_evidence")
    if record["status"] == "human_confirmed":
        _require(record, bool(record.get("reviewer")), "confirmation_requires_reviewer")
        _require(record, not record.get("synthetic"), "synthetic_cannot_be_confirmed")
    for existing in registry["observations"]:
        if existing["observation_id"] == record["observation_id"]:
            raise TruthPackageError("duplicate_observation")
    registry["observations"].append(record)
    return record


def confirm(registry, observation_id, reviewer, evidence):
    target = _find(registry, observation_id)
    _require(target, target["status"] == "candidate", "only_candidate_can_be_confirmed")
    _require(target, not target.get("synthetic"), "synthetic_cannot_be_confirmed")
    _require(target, bool(reviewer), "confirmation_requires_reviewer")
    _require(target, bool(evidence), "confirmation_requires_evidence")
    target["status"] = "human_confirmed"
    target["reviewer"] = reviewer
    target["evidence"] = list(evidence)
    return target


def revoke(registry, observation_id, reason):
    target = _find(registry, observation_id)
    target["status"] = "revoked"
    target["revoked_reason"] = reason
    for record in registry["observations"]:
        if record.get("depends_on") == observation_id:
            record["status"] = "revoked"
            record["revoked_reason"] = "upstream_revoked"
    return target


def _find(registry, observation_id):
    for record in registry["observations"]:
        if record["observation_id"] == observation_id:
            return record
    raise TruthPackageError("observation_not_found")


def confirmed_chain(registry):
    chain = [r for r in registry["observations"]
             if r["status"] == "human_confirmed" and not r.get("synthetic")]
    return sorted(chain, key=lambda r: r["osd_anchor_seconds"])


def is_exploratory(registry):
    return any(r["binding_status"] != "human_confirmed" for r in registry["observations"])


def validate_package(registry):
    """返回问题列表；空列表才表示这份真值包可以用于宣称结论。"""
    problems = []
    for record in registry["observations"]:
        if record["status"] == "human_confirmed":
            if not record.get("reviewer"):
                problems.append({"code": "confirmed_without_reviewer",
                                 "observation_id": record["observation_id"]})
            if not record.get("evidence"):
                problems.append({"code": "confirmed_without_evidence",
                                 "observation_id": record["observation_id"]})
            if record.get("synthetic"):
                problems.append({"code": "synthetic_confirmed",
                                 "observation_id": record["observation_id"]})
    chain = confirmed_chain(registry)
    anchors = [r["osd_anchor_seconds"] for r in chain]
    if anchors != sorted(anchors):
        problems.append({"code": "chain_time_order_invalid"})
    cameras = {r["camera_uuid"] for r in chain}
    if len(cameras) >= 3:
        # 三镜头必须各自独立核实；任何一条缺证据都视为"合成结论"
        weak = [r["observation_id"] for r in chain if not r.get("evidence")]
        if weak:
            problems.append({"code": "third_hop_without_independent_truth", "observations": weak})
    duplicates = {}
    for record in chain:
        key = (record["camera_uuid"], record["osd_anchor_seconds"])
        duplicates[key] = duplicates.get(key, 0) + 1
    for key, count in duplicates.items():
        if count > 1:
            problems.append({"code": "duplicate_confirmed_anchor", "camera_uuid": key[0],
                             "osd_anchor_seconds": key[1]})
    return problems


def export_package(registry, transitions=None, bindings=None):
    return {"schema": "m6-truth-package/1",
            "case_id": registry["case_id"],
            "generated_from": registry.get("generated_from", "manual"),
            "exploratory": is_exploratory(registry),
            "observations": registry["observations"],
            "confirmed_chain": confirmed_chain(registry),
            "transitions": transitions or [],
            "bindings": bindings or [],
            "problems": validate_package(registry)}


def save_package(package, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(package, handle, ensure_ascii=False, indent=1)
    return path


def load_package(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
