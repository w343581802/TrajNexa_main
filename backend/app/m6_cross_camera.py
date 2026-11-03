"""M6-S6-2：跨摄像头时空判定（三态时间模型 + 同步帧配对）。

与 S6 首轮的差别：

1. **时间关系显式化**：分别给出 `overlap_seconds`（实际重叠时长）与
   `transition_gap_seconds`（先后出现的时间间隔），不再用"负 gap"当重叠秒数；
2. **未知不等于错误**：视野重叠未确认、时钟未校准，输出 pending 状态而不是排除；
3. **只有明确矛盾才排除**：确认两路视野不可能同时看到同一目标时才 excluded；
4. **同步帧一对一配对**：同一帧不能重复参与多对，避免虚增支撑证据。

身份结论只到候选级（`overlap_match_candidate` / `transition_candidate`），
不输出"同一人已确认"。
"""
from __future__ import annotations

DEFAULT_CROSS_PARAMS = {
    "min_supporting_frames": 2,
    "weak_cosine": 0.60,
    "min_travel_seconds": 2.0,
    "max_travel_seconds": 180.0,
    "sync_tolerance_seconds": 1.0,
    # 现场经验：骑车人经过相邻摄像头的通行时间常在 10 s 以内。
    # 只用于"优先核查"，不是身份排除阈值，也不适用于步行目标。
    "priority_transit_seconds": 10.0,
}


def transit_priority(relation, params=None):
    """给候选打优先级：通行时间符合现场规律的优先人工核查。

    注意：这是排序提示，不是通过/排除条件。步行目标不适用。
    """
    params = dict(DEFAULT_CROSS_PARAMS, **(params or {}))
    if relation["relation"] == "overlapping":
        return "high"                       # 重叠视野，直接核对同刻
    gap = relation.get("transition_gap_seconds")
    if gap is not None and gap <= params["priority_transit_seconds"]:
        return "high"
    return "normal"


def clock_record(camera_id, file_start, offset_seconds, offset_uncertainty_seconds,
                 clock_source="osd_manual", calibration_status="provisional"):
    """每路摄像头的校时记录：不填不确定度就明确写 null。"""
    return {"camera_id": camera_id, "file_start_seconds": file_start,
            "clock_source": clock_source, "offset_seconds": offset_seconds,
            "offset_uncertainty_seconds": offset_uncertainty_seconds,
            "calibration_status": calibration_status}


def alignment_offset(osd_wall_seconds, file_reference_seconds):
    """文件参考时间 → OSD 观测时间的校正量（正数表示 OSD 比文件名晚）。

    只回答"文件名与画面 OSD 差多少"，**不是**摄像头之间的时钟差。
    """
    return round(osd_wall_seconds - file_reference_seconds, 3)


def observed_at(osd_first_frame_seconds, frame_index, fps):
    """帧 → 观测时间：必须以**该序列首帧的 OSD 读数**为锚点，不能用请求的窗口起点。

    实测：三路都请求 15:02:00 起抽帧，1-1 实际落在 15:01:56（HEVC 预滚偏差），
    若用请求起点当锚点，跨镜头就会出现 4 s 的假偏移。
    """
    return round(osd_first_frame_seconds + frame_index / float(fps), 3)


def osd_clock(camera_id, osd_anchor_seconds, file_reference_seconds, uncertainty_seconds,
              calibration_status="osd_boundary", clock_source="osd_anchor",
              intercamera_clock_offset_seconds=None):
    """以 OSD 时间锚点为基准的观测时钟：observed_at = osd_anchor + 帧内秒数。

    摄像头之间的时钟差（intercamera_clock_offset_seconds）只有共同物理事件才能确定，
    没有证据时保持 None，不得用文件偏差推算。
    """
    return {"camera_id": camera_id,
            "file_reference_seconds": file_reference_seconds,
            "file_osd_alignment_offset_seconds": alignment_offset(osd_anchor_seconds,
                                                                 file_reference_seconds),
            "intercamera_clock_offset_seconds": intercamera_clock_offset_seconds,
            "file_start_seconds": osd_anchor_seconds,
            "offset_seconds": 0.0,
            "clock_source": clock_source,
            "offset_uncertainty_seconds": uncertainty_seconds,
            "calibration_status": calibration_status}


def to_wall(clock, seconds_in_track):
    return clock["file_start_seconds"] + clock["offset_seconds"] + seconds_in_track


def temporal_relation(anchor_span, candidate_span):
    """返回时间关系的显式分解，不用负号混淆"重叠"与"间隔"。"""
    a_start, a_end = anchor_span
    c_start, c_end = candidate_span
    overlap = min(a_end, c_end) - max(a_start, c_start)
    if overlap > 0:
        return {"relation": "overlapping",
                "overlap_seconds": round(overlap, 2),
                "transition_gap_seconds": None}
    gap = max(c_start - a_end, a_start - c_end)
    direction = "anchor_first" if c_start >= a_end else "candidate_first"
    return {"relation": "sequential",
            "overlap_seconds": 0.0,
            "transition_gap_seconds": round(gap, 2),
            "direction": direction}


def cross_status(relation, appearance, spatial=None, params=None):
    """把时间关系、外观证据、空间先验合成状态；未知一律 pending，不排除。"""
    params = dict(DEFAULT_CROSS_PARAMS, **(params or {}))
    spatial = spatial or {}
    appearance_ok = (appearance.get("score", 0.0) >= params["weak_cosine"]
                     and appearance.get("supporting_candidate_frames", 0)
                     >= params["min_supporting_frames"])

    sufficiency = appearance.get("time_sufficiency", "verified")
    if sufficiency == "uncalibrated":
        status = "pending_time_calibration"
    elif relation["relation"] == "overlapping" and sufficiency != "verified":
        # 重叠视野下要判断"同一时刻"，临时 OSD 校时不足以支撑严格同步结论
        status = "pending_time_calibration"
    elif relation["relation"] == "overlapping":
        if spatial.get("overlap_confirmed") is False:
            status = "excluded_by_spatiotemporal_evidence"
        elif spatial.get("overlap_confirmed") is None:
            status = "pending_spatial_verification"
        elif not appearance_ok:
            status = "weak_appearance"
        else:
            status = "overlap_match_candidate"
    else:
        gap = relation["transition_gap_seconds"]
        if gap is None or gap < params["min_travel_seconds"]:
            status = "pending_transition_check"
        elif gap > params["max_travel_seconds"]:
            status = "rejected_by_travel_time"
        elif not appearance_ok:
            status = "weak_appearance"
        else:
            status = "transition_candidate"
    return {"status": status, "appearance_ok": appearance_ok}


def time_sufficiency(*clocks):
    """两台摄像头的校时是否足以支撑严格同步判断。

    - verified：已完成满足当前任务精度的校时（含不确定度）
    - provisional：临时 OSD 校时，只能做粗时间筛选
    - uncalibrated：未校时
    """
    statuses = [c.get("calibration_status", "uncalibrated") for c in clocks]
    if all(s == "verified_for_sync" for s in statuses):
        return "verified"
    if all(s == "uncalibrated" for s in statuses):
        return "uncalibrated"
    return "provisional"


def pair_by_time(anchor_items, candidate_items, tolerance_seconds=1.0):
    """按墙钟时间做一对一配对：每帧最多用一次，避免重复计入证据。

    items: [(wall_seconds, payload)]
    """
    used = set()
    pairs = []
    for a_time, a_payload in sorted(anchor_items):
        best = None
        for index, (c_time, c_payload) in enumerate(candidate_items):
            if index in used:
                continue
            delta = abs(a_time - c_time)
            if delta > tolerance_seconds:
                continue
            if best is None or delta < best[0]:
                best = (delta, index, c_time, c_payload)
        if best is not None:
            used.add(best[1])
            pairs.append({"anchor_time": a_time, "candidate_time": best[2],
                          "delta_seconds": round(best[0], 3),
                          "anchor": a_payload, "candidate": best[3]})
    return pairs


def summarise_pairs(pairs, cosines):
    """同步帧相似度分布，保留原始帧证据。"""
    if not cosines:
        return {"paired_frames": len(pairs), "median_cosine": None,
                "min_cosine": None, "max_cosine": None}
    ordered = sorted(cosines)
    return {"paired_frames": len(pairs),
            "median_cosine": round(ordered[len(ordered) // 2], 3),
            "min_cosine": round(ordered[0], 3),
            "max_cosine": round(ordered[-1], 3)}
