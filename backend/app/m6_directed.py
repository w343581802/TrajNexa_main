"""M6 定向连续追踪（directed tracking）编排。

与"全量检索 + OSNet 排序"的区别：以已确认观测为起点，
先用摄像头空间相邻关系与通行时间把搜索范围缩到"某台摄像头的某段窗口"，
再只在该窗口内做外观比较。OSNet 是窗口内的比较工具，不是决定追踪方向的角色。

**时间基准**：本模块所有时间都是"绝对 OSD 秒"（当天秒数）。
轨迹文件里的 timestamp_seconds 是**抽帧序列内的相对秒**，必须先用
`absolute_track()` 按该路首帧 OSD 锚点换算，才能进入窗口计算——
混用相对秒与绝对秒会让 4.0 s 的间隔算成 6.0 s。
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

# 小于该值的时间视为"相对秒"，不是当天绝对秒（当天秒数必然 > 30000）
_ABSOLUTE_FLOOR = 30000.0


def _require_absolute(value, what):
    if float(value) < _ABSOLUTE_FLOOR:
        raise ValueError("%s_not_absolute: %r 看起来是抽帧相对秒，先换算成绝对 OSD 秒" % (what, value))
    return float(value)


def absolute_track(track, osd_anchor_seconds, fps=5):
    """把轨迹的相对秒换算成绝对 OSD 秒；锚点必须来自该序列首帧 OSD（见 observed_at）。"""
    anchor = _require_absolute(osd_anchor_seconds, "osd_anchor")
    obs = [{**o, "absolute_seconds": round(anchor + float(o["timestamp_seconds"]), 3)}
           for o in track["observations"]]
    out = dict(track)
    out["observations"] = obs
    out["osd_anchor_seconds"] = anchor
    out["fps"] = fps
    return out


def camera_link(link_id, from_camera, to_camera, travel_seconds, exit_side=None,
                direction="forward", confidence="manual", note=""):
    """一条人工填写的"可能通道"：从某摄像头某出口方向到另一摄像头，通常要多久。

    direction=forward 表示目标已从 from_camera 离开后继续前进；
    direction=backward 表示反查"目标从哪里来"（时间在最早观测之前）。
    """
    low, high = float(travel_seconds[0]), float(travel_seconds[1])
    if low > high:
        raise ValueError("travel_seconds_range_inverted")
    if direction not in ("forward", "backward", "both"):
        raise ValueError("invalid_direction")
    return {"link_id": link_id, "from_camera": from_camera, "to_camera": to_camera,
            "travel_seconds": (low, high), "exit_side": exit_side,
            "direction": direction, "confidence": confidence, "note": note}


def target_profile(camera_id, track_id, observed_start, observed_end, exit_side=None,
                   features=None, observations=None):
    """目标档案：绝对 OSD 秒；每条特征都带来源与确认状态，未核验候选不得污染已确认档案。"""
    base = [{"camera": camera_id, "track_id": track_id,
             "start": _require_absolute(observed_start, "observed_start"),
             "end": _require_absolute(observed_end, "observed_end"),
             "exit_side": exit_side, "status": "confirmed"}]
    return {"observations": (observations or []) + base,
            "features": list(features or [])}


def plan_windows(profile, links, params=None):
    """已确认观测 → 下一批候选摄像头及其录像窗口（绝对 OSD 秒）。

    forward：窗口在最后一次观测之后（继续追）；
    backward：窗口在最早一次观测之前（反查来源）。
    """
    p = dict(DEFAULT_DIRECTED_PARAMS, **(params or {}))
    if not profile["observations"]:
        raise ValueError("profile_without_observation")
    last = profile["observations"][-1]
    first = profile["observations"][0]
    pad = p["exit_time_uncertainty_seconds"] + p["clock_uncertainty_seconds"]
    plans = []
    for link in links:
        if link["from_camera"] != last["camera"]:
            continue
        mode = link.get("direction", "forward")
        if mode == "both":
            modes = ("forward", "backward")
        else:
            modes = (mode,)
        if link.get("exit_side") and last.get("exit_side") and link["exit_side"] != last["exit_side"]:
            continue
        low, high = link["travel_seconds"]
        for direction in modes:
            if direction == "forward":
                start = last["end"] + low - pad
                end = last["end"] + high + pad
            else:
                start = first["start"] - high - pad
                end = first["start"] - low + pad
            plans.append({
                "camera": link["to_camera"],
                "mode": direction,
                "window": (round(start, 3), round(end, 3)),
                "priority": "high" if low <= p["priority_transit_seconds"] else "normal",
                "reason": "link %s（%s 出发 %s，%0.1f–%0.1f s）" % (
                    link["link_id"], last["camera"], direction, low, high),
            })
    plans.sort(key=lambda x: (x["priority"] != "high", x["window"][0]))
    return plans


def candidates_in_window(tracks, window):
    """只保留与该**绝对**窗口有交集的轨迹；相对秒轨迹会被拒绝。"""
    start = _require_absolute(window[0], "window_start")
    end = _require_absolute(window[1], "window_end")
    picked = []
    for track in tracks:
        obs = track["observations"]
        if not obs:
            continue
        if "absolute_seconds" not in obs[0]:
            raise ValueError("track_not_absolute: 先用 absolute_track() 换算")
        t0 = obs[0]["absolute_seconds"]
        t1 = obs[-1]["absolute_seconds"]
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
        "camera": camera_id, "track_id": track_id,
        "start": _require_absolute(start, "start"), "end": _require_absolute(end, "end"),
        "exit_side": exit_side, "status": status,
    })
    for feature in features or []:
        item = dict(feature)
        item.setdefault("source_camera", camera_id)
        item.setdefault("source_track", track_id)
        item.setdefault("verified", False)
        updated["features"].append(item)
    return updated
