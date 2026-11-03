"""M6 目标空间状态（S6-15B）。

定位等级：
  L0  只知道目标所在摄像头
  L1  目标位于该摄像头可能覆盖的空间区域（用 Map FOV 粗估）
  L2  目标地面坐标（需画面-地面标定，本轮不做）

所有输出都携带质量与不确定性，不得把 modelled_unverified 的视域当成实测覆盖。
"""
from __future__ import annotations

import math

MOVEMENT_MODES = ("walking", "electric_bicycle", "car", "unknown")
SPEED_RANGES = {          # m/s，区间而非单点
    "walking": (1.0, 1.7),
    "electric_bicycle": (3.5, 7.0),
    "car": (8.0, 16.0),
    "unknown": (0.8, 18.0),
}
SAME_SITE_TRANSFER_SECONDS = (1.0, 20.0)   # 同杆反向镜头之间的转移时间，不得取 0


def haversine_m(a, b):
    lon1, lat1 = math.radians(a["lon"]), math.radians(a["lat"])
    lon2, lat2 = math.radians(b["lon"]), math.radians(b["lat"])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def target_spatial_state(camera, observed_at, movement_mode="electric_bicycle",
                         exit_side=None, track_id=None):
    """把"某摄像头某时刻的最后观测"变成带区域与不确定性的空间状态。"""
    if movement_mode not in MOVEMENT_MODES:
        raise ValueError("invalid_movement_mode")
    if not camera:
        raise ValueError("camera_required")
    if camera.get("location") is None:
        level, region = "L0", None
    else:
        level = "L1"
        region = (camera.get("fov") or {}).get("observation")
    uncertainties = ["intercamera_clock_offset_unknown"]
    if region is None:
        uncertainties.append("fov_missing_region_unknown")
    else:
        uncertainties.append("fov_modelled_unverified")
    return {
        "camera_uuid": camera.get("camera_uuid"),
        "map_camera_id": camera.get("map_camera_id"),
        "display_name": camera.get("display_name"),
        "observed_at": observed_at,
        "track_id": track_id,
        "level": level,
        "possible_region": region,
        "location_quality": "region_only_unverified" if region else "camera_only",
        "movement_mode": movement_mode,
        "image_motion": exit_side,
        "world_motion": None,
        "direction_quality": "unknown",
        "spatial_revision": camera.get("spatial_revision"),
        "uncertainties": uncertainties,
    }


def camera_site_groups(cameras, max_distance_m=5.0):
    """把安装点几乎重合的摄像头归为一组（同杆），但**不**认定现场关系。

    同组只说明安装点相近；朝向是否互补、是否真正同杆仍需人工核实。
    """
    groups, used = [], set()
    ordered = sorted(cameras, key=lambda c: c["map_camera_id"])
    for index, cam in enumerate(ordered):
        if index in used:
            continue
        members = [cam]
        used.add(index)
        for other_index in range(index + 1, len(ordered)):
            if other_index in used:
                continue
            other = ordered[other_index]
            if haversine_m(cam["location"], other["location"]) <= max_distance_m:
                members.append(other)
                used.add(other_index)
        if len(members) > 1:
            facing = {m["map_camera_id"]: m.get("bearing_deg") for m in members}
            bearings = [b for b in facing.values() if b is not None]
            delta = None
            if len(bearings) >= 2:
                delta = round(min(abs(bearings[0] - bearings[1]),
                                  360 - abs(bearings[0] - bearings[1])), 1)
            groups.append({
                "site_key": "site:" + ",".join(sorted(m["map_camera_id"] for m in members)),
                "map_camera_ids": sorted(m["map_camera_id"] for m in members),
                "max_pair_distance_m": round(max(
                    haversine_m(a["location"], b["location"])
                    for a in members for b in members), 2),
                "bearing_delta_deg": delta,
                "site_relation": "co_located_suspected",
                "site_relation_verified": False,
                "transfer_seconds_hint": SAME_SITE_TRANSFER_SECONDS,
                "note": "安装点相近不代表同杆或同一观测点；转移时间不得按距离推算为 0",
            })
    return groups
