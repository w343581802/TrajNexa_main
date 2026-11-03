"""S6-15R1-A：不确定活动区域（区域扩展基线）。

目标不是画唯一路线，而是给出"在 t 秒内目标可能到达的区域"及其不确定性。
当前实现刻意保持简单：
  - 以源 FOV 的可能的离开区域为起点，按移动方式的速度上界向外扩展；
  - 输出用圆近似 + 原始 FOV 顶点，承认这是经验假设，不是校准概率；
  - L0 状态不伪造地面坐标，只做粗粒度可达判定。
"""
from __future__ import annotations

import math

from app.m6_target_spatial import SPEED_RANGES, haversine_m

DEFAULT_R1_PARAMS = {"horizons_seconds": (5.0, 10.0, 20.0, 40.0), "inflation_factor": 1.0}


def _centroid(geometry):
    """FOV 多边形顶点均值，仅作区域中心近似。"""
    if not geometry:
        return None
    polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                else geometry["coordinates"])
    pts = [pt for polygon in polygons for pt in polygon[0]]
    if not pts:
        return None
    return {"lon": sum(p[0] for p in pts) / len(pts),
            "lat": sum(p[1] for p in pts) / len(pts)}


def reachable_region(state, seconds, movement_mode="unknown", params=None):
    """t 秒内可能到达的区域（圆近似）。"""
    fixed = {**DEFAULT_R1_PARAMS, **(params or {})}
    center = _centroid(state.get("possible_region"))
    if center is None:
        return {"center": None, "radius_m": None, "level": "L0",
                "uncertainty": "no_region_only_camera"}
    speed_min, speed_max = SPEED_RANGES.get(movement_mode, SPEED_RANGES["unknown"])
    radius = speed_max * seconds * fixed["inflation_factor"]
    return {"center": center,
            "radius_m": round(radius, 1),
            "speed_range": [speed_min, speed_max],
            "level": state.get("level", "L1"),
            "uncertainty": "expansion_is_empirical_not_calibrated",
            "spatial_revision": state.get("spatial_revision")}


def plausible_by_region(state, camera, seconds, movement_mode="unknown", params=None):
    """候选摄像头 FOV 是否与 t 秒可达区域相交。"""
    region = reachable_region(state, seconds, movement_mode, params)
    if region["center"] is None or not camera.get("location"):
        return {"plausible": None, "reason": "insufficient_geometry"}
    d = haversine_m(region["center"], camera["location"])
    return {"plausible": d <= region["radius_m"] + _fov_radius(camera),
            "distance_to_center_m": round(d, 1),
            "radius_m": region["radius_m"],
            "reason": "within_reachable_radius" if d <= region["radius_m"] + _fov_radius(camera)
                      else "beyond_reachable_radius"}


def _fov_radius(camera):
    """用 FOV 外接半径做粗略补偿，避免小视域被低估。"""
    geometry = (camera.get("fov") or {}).get("observation")
    if not geometry or not camera.get("location"):
        return 0.0
    polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                else geometry["coordinates"])
    pts = [pt for polygon in polygons for pt in polygon[0]]
    if not pts:
        return 0.0
    return max(haversine_m(camera["location"], {"lon": p[0], "lat": p[1]}) for p in pts)


def arrival_band(state, camera, movement_mode="unknown", max_seconds=90.0):
    """到达时间带：与 FOV 规划器同源，用于 R1 与基线对照时保持口径一致。"""
    center = _centroid(state.get("possible_region"))
    if center is None or not camera.get("location"):
        return {"min_s": 0.0, "max_s": max_seconds, "basis": "unknown_geometry"}
    speed_min, speed_max = SPEED_RANGES.get(movement_mode, SPEED_RANGES["unknown"])
    gap = max(0.0, haversine_m(center, camera["location"]) - _fov_radius(camera))
    return {"min_s": round(gap / speed_max, 2),
            "max_s": min(round(gap / speed_min, 2), max_seconds),
            "basis": "centroid_to_fov_radius"}
