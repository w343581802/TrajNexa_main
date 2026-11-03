"""M6 目标档案版本化管理（TargetProfile V1 → V2）。

两条**独立**的状态轴，不得互相污染：

1. 属性状态：model_proposed / human_verified / human_rejected / unknown
2. 跨镜头关联状态：candidate / human_confirmed / rejected

"某条属性被否决" ≠ "该候选人员被否决"；
"关联被人工确认" ≠ "候选上的每条模型属性都正确"。
"""
from __future__ import annotations

ATTRIBUTE_STATES = ("model_proposed", "human_verified", "human_rejected", "unknown")
ASSOCIATION_STATES = ("candidate", "human_confirmed", "rejected")


def attribute(name, value, state="model_proposed", visibility="unknown",
              evidence_frame=None, evidence_region=None, model=None,
              source_camera=None, source_track=None, note=""):
    """一条视觉属性。evidence_region 记录属性取自画面哪个区域（避免把背景当车身）。"""
    if state not in ATTRIBUTE_STATES:
        raise ValueError("invalid_attribute_state")
    return {"name": name, "value": value, "state": state, "visibility": visibility,
            "evidence_frame": evidence_frame, "evidence_region": evidence_region,
            "model": model, "source_camera": source_camera,
            "source_track": source_track, "note": note}


def new_profile(target_id, camera, track_id, start, end, attributes=None):
    return {"target_id": target_id, "version": "V1",
            "observations": [{"camera": camera, "track_id": track_id,
                              "start": start, "end": end, "state": "human_confirmed"}],
            "attributes": list(attributes or []),
            "candidates": [],
            "history": []}


def confirmed_attributes(profile):
    """有效目标档案里只有人工核实过的属性（V1 初始 + 后续被采纳的）。"""
    return [a for a in profile["attributes"] if a["state"] == "human_verified"]


def _index(attrs):
    return {a["name"]: a for a in attrs if a["state"] != "human_rejected"}


def compare_attributes(profile_attrs, candidate_attrs):
    """逐属性比较：支持 / 冲突 / 候选未知 / 候选新增。

    已被人工否决的属性不参与比较（保留在日志里，但不再影响排序或裁决）。
    """
    base = _index(profile_attrs)
    cand_all = {a["name"]: a for a in candidate_attrs}
    cand = {k: v for k, v in cand_all.items() if v["state"] != "human_rejected"}
    findings = []
    for name, a in base.items():
        b = cand.get(name)
        if b is None:
            rejected = cand_all.get(name)
            findings.append({"name": name, "a": a["value"],
                             "b": rejected["value"] if rejected else None,
                             "verdict": "rejected_by_human" if rejected else "not_reported",
                             "b_region": rejected.get("evidence_region") if rejected else None})
            continue
        elif b["state"] == "unknown" or b["value"] in (None, "", "unknown"):
            findings.append({"name": name, "a": a["value"], "b": "unknown",
                             "verdict": "unknown_in_candidate", "b_region": b.get("evidence_region")})
        elif str(b["value"]).strip() == str(a["value"]).strip():
            findings.append({"name": name, "a": a["value"], "b": b["value"],
                             "verdict": "support", "b_region": b.get("evidence_region")})
        else:
            findings.append({"name": name, "a": a["value"], "b": b["value"],
                             "verdict": "conflict", "b_region": b.get("evidence_region")})
    for name, b in cand.items():
        if name in base:
            continue
        verdict = "unknown_in_candidate" if b["state"] == "unknown" or b["value"] in (None, "", "unknown") else "new_in_candidate"
        findings.append({"name": name, "a": None, "b": b["value"], "verdict": verdict,
                         "b_region": b.get("evidence_region")})
    return findings


def attach_candidate(profile, camera, track_id, start, end, attributes, findings=None):
    """候选观测只进入 candidates，不进入已确认档案；未核实属性一律 model_proposed。"""
    candidate = {"candidate_id": "%s#%s" % (camera, track_id), "camera": camera,
                 "track_id": track_id, "start": start, "end": end,
                 "state": "candidate",
                 "attributes": [{**a, "state": "model_proposed"} if a["state"] == "human_verified" else a
                                for a in attributes],
                 "findings": list(findings or [])}
    updated = {**profile, "candidates": profile["candidates"] + [candidate]}
    return updated


def judge_attribute(candidate, name, verdict, reason="", human=True):
    """人工裁决一条属性：human=True 时采纳/否决，与关联状态互不影响。"""
    if verdict not in ("human_verified", "human_rejected"):
        raise ValueError("invalid_verdict")
    attrs = []
    for a in candidate["attributes"]:
        if a["name"] == name:
            attrs.append({**a, "state": verdict, "note": reason})
        else:
            attrs.append(a)
    return {**candidate, "attributes": attrs}


def promote_verified_only(candidate, profile):
    """人工确认关联后，只把**人工核实**的属性并入 V2；被否决与被标 unknown 的都不并入。"""
    promoted = [a for a in candidate["attributes"] if a["state"] == "human_verified"]
    merged = list(profile["attributes"])
    seen = {a["name"] for a in merged}
    for a in promoted:
        if a["name"] in seen:
            continue
        merged.append({**a, "source_camera": candidate["camera"],
                       "source_track": candidate["track_id"],
                       "note": a.get("note") or "V2 采纳自 %s" % candidate["camera"]})
    return merged


def confirm_association(profile, candidate_id):
    """人工确认关联 → V2。仅版本号与观测推进；属性是否采纳由 promote_verified_only 决定。"""
    candidate = next((c for c in profile["candidates"] if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise KeyError(candidate_id)
    if candidate["state"] != "candidate":
        raise ValueError("candidate_not_open")
    promoted = promote_verified_only(candidate, profile)
    updated = {
        "target_id": profile["target_id"],
        "version": "V2",
        "observations": profile["observations"] + [{
            "camera": candidate["camera"], "track_id": candidate["track_id"],
            "start": candidate["start"], "end": candidate["end"], "state": "human_confirmed"}],
        "attributes": promoted,
        "candidates": [{**c, "state": "human_confirmed" if c["candidate_id"] == candidate_id else c["state"]}
                       for c in profile["candidates"]],
        "history": profile["history"] + [{"action": "confirm_association",
                                          "candidate_id": candidate_id,
                                          "promoted": [a["name"] for a in promoted
                                                       if a not in profile["attributes"]],
                                          "rejected": [a["name"] for a in candidate["attributes"]
                                                       if a["state"] == "human_rejected"]}],
    }
    return updated


def revoke_association(profile, candidate_id):
    """撤销 → 回到 V1 状态：观测与由此采纳的属性一并撤回，原始记录仍留在 history。"""
    candidate = next((c for c in profile["candidates"] if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise KeyError(candidate_id)
    confirmed = [o for o in profile["observations"]
                 if not (o["camera"] == candidate["camera"]
                         and str(o["track_id"]) == str(candidate["track_id"]))]
    return {**profile, "version": "V1", "observations": confirmed,
            "candidates": [{**c, "state": "candidate" if c["candidate_id"] == candidate_id else c["state"]}
                           for c in profile["candidates"]],
            "history": profile["history"] + [{"action": "revoke_association",
                                              "candidate_id": candidate_id}]}
