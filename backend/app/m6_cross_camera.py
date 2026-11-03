"""M6-S6：跨摄像头候选比对（不新增模型）。

职责边界：

- 只拿已抽好的帧与已生成的单摄像头轨迹做比对，不训练、不改库；
- 跨镜头结论必须同时满足**外观证据**与**时空证据**，两者缺一只能给候选；
- 时间对齐用每台摄像头标定的 OSD 偏移，不能用"文件名起始 + 帧号"直接当墙钟。

时空规则基于一个常识：同一个人在 T 秒内不可能同时出现在相距 50 m 的两个镜头里，
但也也不会瞬间移动——因此候选时间差必须落在 [min_travel, max_travel] 区间内。
"""
from __future__ import annotations

DEFAULT_CROSS_PARAMS = {
    "min_travel_seconds": 2.0,
    "max_travel_seconds": 180.0,
    "min_supporting_frames": 2,
    "weak_cosine": 0.60,
}


def wall_clock(track_start, offset_seconds, at_seconds):
    """轨迹内时刻 → 墙钟秒（相对当天 0 点），offset 为 OSD 与文件起始时间的标定差。"""
    return track_start + offset_seconds + at_seconds


def temporal_gate(anchor_span, candidate_span, params=None):
    """两个轨迹的时间区间是否与"同一人移动过去"相容。

    anchor_span / candidate_span: (start, end) 墙钟秒。
    返回 {compatible, gap_seconds, reason}：gap 为两区间最近边界之差（可正可负）。
    """
    params = dict(DEFAULT_CROSS_PARAMS, **(params or {}))
    a_start, a_end = anchor_span
    c_start, c_end = candidate_span
    gap = max(c_start - a_end, a_start - c_end)
    if gap < 0:
        # 时间重叠：可能同框也可能"同时在两个镜头"，需要更细的校验，先标需求解
        return {"compatible": False, "gap_seconds": round(gap, 2),
                "reason": "time_overlap_needs_frame_level_check"}
    if gap < params["min_travel_seconds"]:
        return {"compatible": False, "gap_seconds": round(gap, 2),
                "reason": "too_close_to_be_another_camera"}
    if gap > params["max_travel_seconds"]:
        return {"compatible": False, "gap_seconds": round(gap, 2),
                "reason": "too_far_to_be_same_person"}
    return {"compatible": True, "gap_seconds": round(gap, 2), "reason": "ok"}


def cross_camera_verdict(appearance, temporal, params=None):
    """把外观与时空证据合成一个明确状态，不替人下身份结论。"""
    params = dict(DEFAULT_CROSS_PARAMS, **(params or {}))
    appearance_ok = (appearance.get("score", 0.0) >= params["weak_cosine"]
                     and appearance.get("supporting_candidate_frames", 0)
                     >= params["min_supporting_frames"])
    if not temporal.get("compatible"):
        status = "rejected_by_time"
    elif not appearance_ok:
        status = "weak_appearance"
    elif appearance.get("conflict"):
        status = "excluded_by_evidence"
    else:
        status = "cross_camera_candidate"
    return {"status": status, "appearance_ok": appearance_ok,
            "temporal_ok": bool(temporal.get("compatible")),
            "gap_seconds": temporal.get("gap_seconds")}


def rank_cross_candidates(anchor_span, candidates, params=None):
    """候选按外观分排序，同时标注时空判定结果；不删除被时间否决的候选。"""
    params = dict(DEFAULT_CROSS_PARAMS, **(params or {}))
    ranked = []
    for item in candidates:
        gate = temporal_gate(anchor_span, item["span"], params)
        verdict = cross_camera_verdict(item["appearance"], gate, params)
        ranked.append({"camera": item["camera"], "track": item["track"],
                       "score": round(float(item["appearance"].get("score", 0.0)), 4),
                       "supporting_frames": item["appearance"].get("supporting_candidate_frames", 0),
                       "span": item["span"], **gate, **verdict})
    ranked.sort(key=lambda r: -r["score"])
    for index, item in enumerate(ranked):
        item["rank"] = index + 1
    return ranked
