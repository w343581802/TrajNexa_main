"""M6 空间候选规划（S6-15C）。

核心原则：
1. 用"目标可能经过区域 ↔ 摄像头视域"判断可达性，**不用摄像头安装点间距**判速度；
2. 同杆反向镜头单独成组，转移时间不得按距离推成 0；
3. 不因 FOV 未经验证而静默排除任何摄像头——只降优先级并记录原因。
"""
from __future__ import annotations

import math

from app.m6_target_spatial import SAME_SITE_TRANSFER_SECONDS, SPEED_RANGES, haversine_m

DEFAULT_SPATIAL_PARAMS = {
    "max_range_m": 500.0,
    "max_seconds": 90.0,
    "same_site_max_m": 5.0,
    "time_uncertainty_seconds": 1.0,
}


def _local(point, origin):
    x = (point["lon"] - origin["lon"]) * 111320.0 * math.cos(math.radians(origin["lat"]))
    y = (point["lat"] - origin["lat"]) * 110540.0
    return (x, y)


def _rings(geometry):
    if not geometry:
        return []
    polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                else geometry["coordinates"])
    return [polygon[0] for polygon in polygons if polygon]


def _point_in_ring(pt, ring):
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i + 1][0], ring[i + 1][1]
        if (y1 > pt[1]) != (y2 > pt[1]):
            xin = (x2 - x1) * (pt[1] - y1) / (y2 - y1 + 1e-15) + x1
            if pt[0] < xin:
                inside = not inside
    return inside


def _segment_distance(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _segments_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2 = orient(a, b, c), orient(a, b, d)
    o3, o4 = orient(c, d, a), orient(c, d, b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def fov_relation(geometry_a, geometry_b, origin=None):
    """两个 FOV 多边形的关系。返回 relation 与最近距离（米）。"""
    rings_a, rings_b = _rings(geometry_a), _rings(geometry_b)
    if not rings_a or not rings_b:
        return {"relation": "unknown", "distance_m": None}
    origin = origin or {"lon": rings_a[0][0][0], "lat": rings_a[0][0][1]}
    ra = [[_local({"lon": p[0], "lat": p[1]}, origin) for p in ring] for ring in rings_a]
    rb = [[_local({"lon": p[0], "lat": p[1]}, origin) for p in ring] for ring in rings_b]
    for ring_a in ra:
        for ring_b in rb:
            if any(_point_in_ring(pt, ring_b) for pt in ring_a):
                return {"relation": "overlap", "distance_m": 0.0}
            if any(_point_in_ring(pt, ring_a) for pt in ring_b):
                return {"relation": "overlap", "distance_m": 0.0}
            for i in range(len(ring_a) - 1):
                for j in range(len(ring_b) - 1):
                    if _segments_cross(ring_a[i], ring_a[i + 1], ring_b[j], ring_b[j + 1]):
                        return {"relation": "overlap", "distance_m": 0.0}
    best = float("inf")
    for ring_a in ra:
        for ring_b in rb:
            for pt in ring_a:
                for j in range(len(ring_b) - 1):
                    best = min(best, _segment_distance(pt, ring_b[j], ring_b[j + 1]))
            for pt in ring_b:
                for j in range(len(ring_a) - 1):
                    best = min(best, _segment_distance(pt, ring_a[j], ring_a[j + 1]))
    return {"relation": "disjoint", "distance_m": round(best, 2)}


def transit_window(distance_m, movement_mode, same_site=False, overlap=False):
    """到达时间区间。距离取"区域到区域"，不是安装点间距。"""
    speed_min, speed_max = SPEED_RANGES.get(movement_mode, SPEED_RANGES["unknown"])
    if overlap:
        # 视域重叠只说明"可能同处一片区域"，不说明时刻；窗口必须放宽，
        # 否则会把真实观测裁掉（1-1 与 2-2 的 FOV 就重叠）。
        return {"transit_min": 0.0, "transit_max": 20.0,
                "basis": "fov_overlap_window", "uncertainty": "overlap_duration_unverified"}
    if same_site:
        return {"transit_min": SAME_SITE_TRANSFER_SECONDS[0],
                "transit_max": SAME_SITE_TRANSFER_SECONDS[1],
                "basis": "same_site_pair", "uncertainty": "same_site_transfer_unverified"}
    if distance_m is None:
        return {"transit_min": 0.0, "transit_max": 90.0,
                "basis": "unknown_geometry", "uncertainty": "distance_unknown"}
    return {"transit_min": round(distance_m / speed_max, 2),
            "transit_max": round(distance_m / speed_min, 2),
            "basis": "region_to_region_distance", "uncertainty": "speed_range_only"}


def plan_spatial_candidates(state, cameras, params=None, manual_links=()):
    """从目标空间状态生成候选摄像头；不依赖人工 camera_link，也不因此排除任何摄像头。"""
    fixed = {**DEFAULT_SPATIAL_PARAMS, **(params or {})}
    source_id = state.get("map_camera_id")
    source_point = next((c for c in cameras if c["map_camera_id"] == source_id), None)
    candidates = []
    for camera in cameras:
        if camera["map_camera_id"] == source_id:
            continue
        reason, uncertainty = [], []
        relation = "unknown"
        distance = None
        if state.get("possible_region") and (camera.get("fov") or {}).get("observation"):
            rel = fov_relation(state["possible_region"], camera["fov"]["observation"])
            relation, distance = rel["relation"], rel["distance_m"]
        elif source_point and camera.get("location"):
            distance = round(haversine_m(source_point["location"], camera["location"]), 2)
            relation = "camera_point_distance"
        if relation == "overlap":
            reason.append("fov_overlap")
            uncertainty.append("clock_offset_unknown_for_overlap")
        elif relation == "disjoint":
            reason.append("reachable_non_overlap")
        else:
            reason.append("spatial_relation_unknown")
            uncertainty.append("insufficient_geometry")
        # 同点位判定只看安装点间距；FOV 已重叠时不套用同杆转移时间
        same_site = (distance is not None and distance <= fixed["same_site_max_m"]
                     and relation != "overlap")
        window = transit_window(distance, state.get("movement_mode", "unknown"),
                                same_site, overlap=(relation == "overlap"))
        if same_site:
            reason.append("same_site_opposite_camera")
        if distance is not None and distance > fixed["max_range_m"]:
            uncertainty.append("beyond_configured_range")
        if (camera.get("fov") or {}).get("quality") != "calibrated":
            uncertainty.append("fov_not_field_verified")
        if camera.get("spatial_revision") != state.get("spatial_revision"):
            uncertainty.append("spatial_revision_mismatch")
        if camera["map_camera_id"] in manual_links:
            reason.append("manual_link_confirmed")
        travel_max = window["transit_max"]
        if relation == "overlap" or (distance is not None and distance <= 60
                                     and travel_max <= 20):
            priority = "high"
        elif distance is not None and distance <= 200:
            priority = "medium"
        else:
            priority = "low"
        if camera["map_camera_id"] in manual_links and priority == "medium":
            priority = "high"
        candidates.append({
            "candidate_camera": camera["map_camera_id"],
            "display_name": camera.get("display_name"),
            "priority": priority,
            "spatial_relation": relation,
            "distance_m": distance,
            "same_site": same_site,
            "transit_min": window["transit_min"],
            "transit_max": window["transit_max"],
            "transit_basis": window["basis"],
            "reasons": reason,
            "uncertainties": uncertainty,
            "spatial_revision": camera.get("spatial_revision"),
            "status": "candidate_camera",
        })
    order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (order[c["priority"]],
                                   c["distance_m"] if c["distance_m"] is not None else 1e9))
    return candidates


def speed_consistency(distance_m, seconds, movement_mode):
    """安装点距离 ÷ 观测时刻差 只能作为一致性提示，不能判定"不可能"。

    距离不是目标实际行进距离，观测时刻也不是经过安装点的时刻，
    且跨镜头时钟尚未用共同物理事件校准。
    """
    if not seconds or distance_m is None:
        return {"implied_mps": None, "verdict": "insufficient_data"}
    implied = round(distance_m / seconds, 2)
    low, high = SPEED_RANGES.get(movement_mode, SPEED_RANGES["unknown"])
    within = low <= implied <= high
    return {
        "implied_mps": implied,
        "verdict": "consistent_with_mode" if within else "spatiotemporal_consistency_unverified",
        "note": "距离为安装点距离、时刻为镜头内观测时刻，超出区间不等于不可能",
    }


def mark_exploratory(candidates, binding_status, note="camera_binding_pending_human_review"):
    """绑定未核实时的结果只能算算法探索，不得当作正式业务验收。"""
    flag = {"exploratory": True, "binding_status": binding_status, "note": note}
    return [{**c, **flag} for c in candidates]


def windows_for_candidates(state, candidates, params=None, direction="forward"):
    """每台摄像头独立时间窗口：t_A ± transit，前向与反向分开。"""
    if direction not in ("forward", "backward"):
        raise ValueError("invalid_direction")
    fixed = {**DEFAULT_SPATIAL_PARAMS, **(params or {})}
    t0 = state["observed_at"]
    u = fixed["time_uncertainty_seconds"]
    sign = 1 if direction == "forward" else -1
    plans = []
    for cand in candidates:
        plans.append({
            "camera": cand["candidate_camera"],
            "mode": direction,
            "window": (t0 + sign * cand["transit_min"] - u,
                       t0 + sign * cand["transit_max"] + u) if sign > 0 else
                      (t0 - cand["transit_max"] - u, t0 - cand["transit_min"] + u),
            "priority": cand["priority"],
            "reason": ",".join(cand["reasons"]),
            "uncertainties": cand["uncertainties"],
        })
    return plans
