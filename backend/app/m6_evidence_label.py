"""M6-S4-4B：人脸/车牌关键画面的标注规范与评测指标。

目标不是自动识别人脸/车牌，而是验证"系统挑出的画面是否真的值得人工研判"。
本模块只做三件事：

1. 定义标注字段并校验（人脸与车牌分开，`visible` 与 `usable` 分开）；
2. 计算两个核心指标：Top-K 可用帧命中率、关键证据发现率；
3. 把"目标关联状态"与"画面质量"分开表达——两者都成立才算高价值证据。
"""
from __future__ import annotations

TASKS = ("face", "plate")
VISIBILITY = ("visible", "partial", "invisible")
QUALITY = ("sharp", "fair", "blurry")
OCCLUSION = ("none", "partial", "severe")
ANNOTATION_STATUS = ("verified", "pending")
USABLE = ("yes", "marginal", "no")

REQUIRED_FIELDS = (
    "frame_id", "video_relative_seconds", "track_id", "task",
    "visibility", "sharpness", "occlusion", "truncated",
    "usable", "unusable_reason", "annotation_status",
)


def validate_labels(labels, task=None):
    """返回错误列表；空列表表示标注可用。"""
    errors = []
    seen = set()
    for index, row in enumerate(labels):
        missing = [f for f in REQUIRED_FIELDS if f not in row]
        if missing:
            errors.append("第 %d 条缺字段：%s" % (index, ",".join(missing)))
            continue
        if str(row["frame_id"]) in seen:
            errors.append("frame_id 重复：%s" % row["frame_id"])
        seen.add(str(row["frame_id"]))
        if row["task"] not in TASKS:
            errors.append("%s: task 非法" % row["frame_id"])
        if task and row["task"] != task:
            errors.append("%s: task 与评测目标不一致" % row["frame_id"])
        if row["visibility"] not in VISIBILITY:
            errors.append("%s: visibility 非法" % row["frame_id"])
        if row["sharpness"] not in QUALITY:
            errors.append("%s: sharpness 非法" % row["frame_id"])
        if row["occlusion"] not in OCCLUSION:
            errors.append("%s: occlusion 非法" % row["frame_id"])
        if row["usable"] not in USABLE:
            errors.append("%s: usable 非法" % row["frame_id"])
        if row["annotation_status"] not in ANNOTATION_STATUS:
            errors.append("%s: annotation_status 非法" % row["frame_id"])
        if row["usable"] == "no" and not str(row["unusable_reason"] or "").strip():
            errors.append("%s: 不可用必须写原因" % row["frame_id"])
        if row["task"] == "face":
            # 看得见脸 != 足以比对身份，两个字段都要在
            for field in ("face_visible", "face_usable"):
                if field not in row:
                    errors.append("%s: 人脸标注缺 %s" % (row["frame_id"], field))
        if row["task"] == "plate":
            for field in ("plate_visible", "plate_usable"):
                if field not in row:
                    errors.append("%s: 车牌标注缺 %s" % (row["frame_id"], field))
    return errors


def by_frame(labels):
    return {str(row["frame_id"]): row for row in labels}


def top_k_hit_rate(labels, picks):
    """推荐帧里有多少可直接用于人工核验（usable=yes；marginal 单独统计）。"""
    index = by_frame(labels)
    usable = marginal = missing = 0
    for pick in picks:
        row = index.get(str(pick))
        if row is None:
            missing += 1
        elif row["usable"] == "yes":
            usable += 1
        elif row["usable"] == "marginal":
            marginal += 1
    total = len(picks) or 1
    return {"picks": len(picks), "usable": usable, "marginal": marginal,
            "unlabeled": missing,
            "usable_rate": round(usable / total, 3),
            "usable_or_marginal_rate": round((usable + marginal) / total, 3)}


def key_evidence_found(labels, picks, key_frames):
    """关键证据的覆盖情况。

    区分两种"命中"：
    - `any_hit`：至少找到一张合格关键画面（很多研判任务到此就够用）；
    - `all_hit`：覆盖人工标注的全部关键画面（用于判断是否存在漏检）；
    - `key_recall`：找到数量占关键画面的比例。

    `key_frames` 必须**独立标注**（例如全片段扫一遍挑出所有清晰正脸），
    不能拿某个策略的推荐结果反过来定义关键帧，否则评测自带偏差。
    """
    picked = {str(p) for p in picks}
    keys = [str(k) for k in key_frames]
    found = [k for k in keys if k in picked]
    missed = [k for k in keys if k not in picked]
    return {"key_frames": keys, "found": found, "missed": missed,
            "any_hit": bool(found),
            "all_hit": bool(keys) and not missed,
            "key_recall": round(len(found) / len(keys), 3) if keys else 0.0}


def evidence_value(association_status, quality_status):
    """目标关联状态与画面质量必须分开表达，两者都成立才算高价值证据。

    **candidate 不等于身份已确认**：普通候选轨迹的清晰画面只能是"待核验的高价值证据"，
    只有走过人工/规则核实（`confirmed`）的才能标成"已核实的高价值证据"。
    """
    confirmed = association_status == "confirmed"
    association_ok = association_status in ("candidate", "confirmed",
                                            "supported_by_independent_candidates")
    quality_ok = quality_status in ("usable", "yes")
    if confirmed and quality_ok:
        verdict = "high_value_evidence_verified"
    elif association_ok and quality_ok:
        verdict = "high_value_evidence_pending_review"
    elif association_ok and not quality_ok:
        verdict = "trace_ok_but_picture_unusable"
    elif not association_ok and quality_ok:
        verdict = "clear_picture_but_identity_unconfirmed"
    else:
        verdict = "not_evidence"
    return {"association_ok": association_ok, "association_confirmed": confirmed,
            "quality_ok": quality_ok, "verdict": verdict}
