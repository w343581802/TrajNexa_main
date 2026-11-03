"""M6 定向连续追踪（directed tracking）编排。

与"全量检索 + OSNet 排序"的区别：以已确认观测为起点，
先用摄像头空间相邻关系与通行时间把搜索范围缩到"某台摄像头的某段窗口"，
再只在该窗口内做外观比较。OSNet 是窗口内的比较工具，不是决定追踪方向的角色。
"""
from __future__ import annotations

DEFAULT_DIRECTED_PARAMS = {
    "travel_seconds_min": 2.0,
    "travel_seconds_max": 20.0,
    "priority_transit_seconds": 10.0,
    # 目标离开时刻本身的误差：OSD 秒级 + 跨镜头时钟未校准
    "exit_time_uncertainty_seconds": 1.0,
    "clock_uncertainty_seconds": 1.0,
}


def camera_link(link_id, from_camera, to_camera, travel_seconds, exit_side=None,
                confidence="manual", note=""):
    """一条人工填写的"可能通道"：从某摄像头某出口方向到另一摄像头，通常要多久。

    地图与视野数据不完整时，通道关系由人工维护；缺方向不代表不可能经过。
    """
    low, high = float(travel_seconds[0]), float(travel_seconds[1])
    if low > high:
        raise ValueError("travel_seconds_range_inverted")
    return {"link_id": link_id, "from_camera": from_camera, "to_camera": to_camera,
            "travel_seconds": (low, high), "exit_side": exit_side,
            "confidence": confidence, "note": note}


def target_profile(camera_id, track_id, observed_start, observed_end, exit_side=None,
                   features=None, observations=None):
    """目标档案：每条特征都带来源与确认状态，未核验的候选不得污染已确认档案。"""
    base = [{"camera": camera_id, "track_id": track_id,
             "start": float(observed_start), "end": float(observed_end),
             "exit_side": exit_side, "status": "confirmed"}]
    return {"observations": (observations or []) + base,
            "features": list(features or [])}


def plan_windows(profile, links, params=None):
    """已确认观测 → 下一批候选摄像头及其录像窗口（按优先级排序）。

    窗口 = 目标离开时刻 + 通行时间范围 ± 误差，用于"只看这一段录像"。
    """
    p = dict(DEFAULT_DIRECTED_PARAMS, **(params or {}))
    if not profile["observations"]:
        raise ValueError("profile_without_observation")
    last = profile["observations"][-1]
    pad = p["exit_time_uncertainty_seconds"] + p["clock_uncertainty_seconds"]
    plans = []
    for link in links:
        if link["from_camera"] != last["camera"]:
            continue
        if link.get("exit_side") and last.get("exit_side") and link["exit_side"] != last["exit_side"]:
            continue
        low, high = link["travel_seconds"]
        start = last["end"] + low - pad
        end = last["end"] + high + pad
        plans.append({
            "camera": link["to_camera"],
            "window": (round(start, 3), round(end, 3)),
            "priority": "high" if low <= p["priority_transit_seconds"] else "normal",
            "reason": "link %s（%s 出发，%0.1f–%0.1f s）" % (
                link["link_id"], last["camera"], low, high),
        })
    plans.sort(key=lambda x: (x["priority"] != "high", x["window"][0]))
    return plans


def candidates_in_window(tracks, window):
    """只保留与该窗口有交集的轨迹——这是"定向"带来的候选收缩。"""
    start, end = window
    picked = []
    for track in tracks:
        obs = track["observations"]
        if not obs:
            continue
        t0 = obs[0]["timestamp_seconds"]
        t1 = obs[-1]["timestamp_seconds"]
        if t1 < start or t0 > end:
            continue
        picked.append({"track": track, "enter": t0, "exit": t1,
                       "offset_seconds": round(t0 - start, 3)})
    picked.sort(key=lambda c: c["enter"])
    return picked


def update_profile(profile, camera_id, track_id, start, end, status="candidate",
                   features=None, exit_side=None):
    """把新观测并入档案；status=candidate 的观测可参与下一步规划，但不被当作已确认身份。"""
    updated = {"observations": list(profile["observations"]),
               "features": list(profile["features"])}
    updated["observations"].append({
        "camera": camera_id, "track_id": track_id, "start": float(start),
        "end": float(end), "exit_side": exit_side, "status": status,
    })
    for feature in features or []:
        item = dict(feature)
        item.setdefault("source_camera", camera_id)
        item.setdefault("source_track", track_id)
        item.setdefault("verified", False)
        updated["features"].append(item)
    return updated
