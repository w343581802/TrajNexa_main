"""M6 轨迹质量约束（P1-B-1C-1）。

为什么需要：菜市入口机位实测，"人车分离"候选里 18/21 条车辆轨迹出现框面积突变
（最大 6.6 倍）、归一化中心跳变（>1.0）、宽高比异常（0.36~2.09）。人工抽查确认
多数是**检测框本身错误**（同一个框从 31858 px² 缩到 130 px²），少数是 ByteTrack
把相邻车辆关联成一条。

设计原则（对应验收边界）：
1. 单帧抖动只丢观测，不断轨——小目标框本来就抖 1.8 倍，逐帧切断会把一条真实
   车辆切成十几个碎片，等于用"过滤真实车辆"换指标；
2. 只有**连续若干帧稳定地**落到另一组统计量上（真的换了目标）才断轨；
3. 切断只说明"这条轨迹不可信"，不产生任何下车结论。

本模块只做后处理，不改跟踪器，也不改检测器。
"""
from __future__ import annotations

import math

DEFAULT_PARAMS = {
    # 稳定窗口：用前后各 window 帧的中位数比较，单帧异常不影响判定
    "stable_window": 3,
    # 稳定窗口之间的面积比上限
    "area_max_ratio": 1.8,
    # 稳定窗口之间的归一化中心位移上限（位移 / 对角线 / sqrt(dt)）
    "center_jump_max": 1.2,
    # 两轮车正视/侧视的宽高比范围
    "aspect_min": 0.30,
    "aspect_max": 2.60,
    # 单帧去抖：与邻域中位数相差超过该倍数就算异常观测
    "despike_ratio": 2.5,
    # 切断后少于这么多帧的碎片单独成轨没有意义（只标记，不删除）
    "split_min_frames": 4,
}


def _geometry(bbox):
    width = max(1e-6, float(bbox[2]) - float(bbox[0]))
    height = max(1e-6, float(bbox[3]) - float(bbox[1]))
    return {
        "area": width * height,
        "aspect": width / height,
        "diagonal": math.hypot(width, height),
        "center": ((float(bbox[0]) + float(bbox[2])) / 2.0, (float(bbox[1]) + float(bbox[3])) / 2.0),
    }


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _stats(observations):
    areas = [_geometry(o["bbox"])["area"] for o in observations]
    centers = [_geometry(o["bbox"])["center"] for o in observations]
    diagonals = [_geometry(o["bbox"])["diagonal"] for o in observations]
    return {
        "area": _median(areas),
        "center": (_median([c[0] for c in centers]), _median([c[1] for c in centers])),
        "diagonal": _median(diagonals),
        "start": float(observations[0]["timestamp_seconds"]),
        "end": float(observations[-1]["timestamp_seconds"]),
    }


def audit_track(observations, params=None):
    """逐帧质量指标（只做诊断，判定是否断轨用 `split_track`）。"""
    params = dict(DEFAULT_PARAMS, **(params or {}))
    observations = list(observations)
    if len(observations) < 2:
        return {"frames": len(observations), "area_max_ratio": 1.0, "max_jump_norm": 0.0,
                "aspect_min": None, "aspect_max": None, "area_jump": 0, "center_jump": 0,
                "aspect_out_of_range": 0, "violations": 0}
    areas = [_geometry(o["bbox"])["area"] for o in observations]
    aspects = [_geometry(o["bbox"])["aspect"] for o in observations]
    ratio = max(max(x / y, y / x) for x, y in zip(areas, areas[1:]))
    worst = 0.0
    counts = {"area_jump": 0, "center_jump": 0, "aspect_out_of_range": 0}
    for previous, current in zip(observations, observations[1:]):
        a, b = _geometry(previous["bbox"]), _geometry(current["bbox"])
        if max(a["area"] / b["area"], b["area"] / a["area"]) > params["area_max_ratio"]:
            counts["area_jump"] += 1
        dt = max(1e-3, float(current["timestamp_seconds"]) - float(previous["timestamp_seconds"]))
        move = math.hypot(b["center"][0] - a["center"][0], b["center"][1] - a["center"][1])
        normalized = move / (a["diagonal"] * math.sqrt(dt))
        worst = max(worst, normalized)
        if normalized > params["center_jump_max"]:
            counts["center_jump"] += 1
        if not params["aspect_min"] <= b["aspect"] <= params["aspect_max"]:
            counts["aspect_out_of_range"] += 1
    return {"frames": len(observations), "area_max_ratio": round(ratio, 2),
            "max_jump_norm": round(worst, 2), "aspect_min": round(min(aspects), 2),
            "aspect_max": round(max(aspects), 2),
            "area_jump": counts["area_jump"], "center_jump": counts["center_jump"],
            "aspect_out_of_range": counts["aspect_out_of_range"],
            "violations": sum(counts.values())}


def despike(observations, params=None):
    """丢掉单帧离群观测（框突然变成两倍大又立刻恢复），轨迹保持连续。"""
    params = dict(DEFAULT_PARAMS, **(params or {}))
    observations = list(observations)
    if len(observations) < 5:
        return observations, []
    kept, dropped = [], []
    for index, observation in enumerate(observations):
        left = observations[max(0, index - 2):index]
        right = observations[index + 1:index + 3]
        neighbours = left + right
        if len(neighbours) < 3:
            kept.append(observation)
            continue
        area = _geometry(observation["bbox"])["area"]
        reference = _median([_geometry(o["bbox"])["area"] for o in neighbours])
        ratio = max(area / max(1e-6, reference), reference / max(1e-6, area))
        if ratio > params["despike_ratio"]:
            dropped.append({"timestamp_seconds": round(float(observation["timestamp_seconds"]), 3),
                            "bbox": [round(float(v), 2) for v in observation["bbox"]],
                            "area_ratio": round(ratio, 2)})
        else:
            kept.append(observation)
    return kept, dropped


def split_track(observations, params=None):
    """用"前后稳定窗口对比"找真实换轨点，返回 (子段列表, 切断点列表)。"""
    params = dict(DEFAULT_PARAMS, **(params or {}))
    observations = list(observations)
    window = max(1, int(params["stable_window"]))
    if len(observations) < 2 * window:
        return [observations] if observations else [], []
    segments, breaks, start = [], [], 0
    index = window
    while index < len(observations):
        left = observations[max(0, index - window):index]
        right = observations[index:index + window]
        if len(left) < window or len(right) < 2:
            break
        a, b = _stats(left), _stats(right)
        ratio = max(a["area"] / b["area"], b["area"] / a["area"])
        dt = max(1e-3, b["start"] - a["end"])
        move = math.hypot(b["center"][0] - a["center"][0], b["center"][1] - a["center"][1])
        normalized = move / (max(1e-6, a["diagonal"]) * math.sqrt(dt))
        reasons = []
        if ratio > params["area_max_ratio"]:
            reasons.append("area_jump")
        if normalized > params["center_jump_max"]:
            reasons.append("center_jump")
        left_aspect = _median([_geometry(o["bbox"])["aspect"] for o in left])
        right_aspect = _median([_geometry(o["bbox"])["aspect"] for o in right])
        shape_change = max(left_aspect / max(1e-6, right_aspect), right_aspect / max(1e-6, left_aspect))
        if not params["aspect_min"] <= right_aspect <= params["aspect_max"] and shape_change > 1.3:
            reasons.append("aspect_out_of_range")
        if reasons:
            segments.append(observations[start:index])
            breaks.append({"timestamp_seconds": round(b["start"], 3), "reasons": reasons,
                           "area_ratio": round(ratio, 2), "jump_norm": round(normalized, 2)})
            start = index
            index += window
        else:
            index += 1
    segments.append(observations[start:])
    return [s for s in segments if s], breaks


def refine_tracks(tracks, params=None):
    """先去抖再断轨，并把结果重新编号。

    返回 {"tracks": [...], "splits": [...], "fragments": n, "despiked": n}：
    新轨迹带 `parent_track_id` / `segment_index`；短碎片标 `fragment=True`，**不删除**。
    """
    params = dict(DEFAULT_PARAMS, **(params or {}))
    refined, splits, fragments, despiked = [], [], 0, 0
    for track in tracks:
        observations = track.get("frames") or track.get("observations") or []
        cleaned, dropped = despike(observations, params)
        despiked += len(dropped)
        segments, breaks = split_track(cleaned, params)
        if len(segments) == 1 and not breaks and not dropped:
            refined.append(dict(track))
            continue
        for index, segment in enumerate(segments):
            shorter = len(segment) < params["split_min_frames"]
            if shorter:
                fragments += 1
            refined.append({
                "track_id": track["track_id"] * 100 + index,
                "parent_track_id": track["track_id"],
                "segment_index": index,
                "fragment": shorter,
                "despiked": len(dropped) if index == 0 else 0,
                "start_seconds": round(float(segment[0]["timestamp_seconds"]), 2),
                "end_seconds": round(float(segment[-1]["timestamp_seconds"]), 2),
                "observations": len(segment),
                "frames": segment,
            })
        for item in breaks:
            splits.append(dict(item, parent_track_id=track["track_id"]))
    return {"tracks": refined, "splits": splits, "fragments": fragments, "despiked": despiked}


QUALITY_STATUSES = ("reliable", "suspicious", "unknown")


def quality_of(observations, params=None):
    """给一条轨迹打质量标签：reliable / suspicious / unknown。

    只做标注，不改轨迹、不删轨迹——上游证据可疑时，下游不给确定结论。
    """
    params = dict(DEFAULT_PARAMS, **(params or {}))
    metrics = audit_track(observations, params)
    reasons = []
    if metrics["frames"] < params["split_min_frames"]:
        reasons.append("too_few_frames")
    if metrics["area_jump"]:
        reasons.append("area_jump")
    if metrics["center_jump"]:
        reasons.append("center_jump")
    if metrics["aspect_out_of_range"]:
        reasons.append("aspect_out_of_range")
    if not reasons:
        return {"quality_status": "reliable", "quality_reasons": [], "evidence_frames": metrics["frames"]}
    status = "unknown" if metrics["frames"] < params["split_min_frames"] else "suspicious"
    return {"quality_status": status, "quality_reasons": reasons, "evidence_frames": metrics["frames"]}


def assess_tracks(tracks, params=None):
    """给一组车辆轨迹补 `quality_status` / `quality_reasons` / `evidence_frames`。

    返回 (tracks, {"reliable": n, "suspicious": n, "unknown": n})。
    """
    params = dict(DEFAULT_PARAMS, **(params or {}))
    counts = {name: 0 for name in QUALITY_STATUSES}
    enriched = []
    for track in tracks:
        observations = track.get("frames") or track.get("observations") or []
        quality = quality_of(observations, params)
        counts[quality["quality_status"]] += 1
        enriched.append(dict(track, **quality))
    return enriched, counts
