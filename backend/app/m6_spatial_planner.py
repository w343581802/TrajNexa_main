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
    """返回所有环（外环 + 内环）。孔洞用奇偶规则自然生效。"""
    if not geometry:
        return []
    polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                else geometry["coordinates"])
    rings = []
    for polygon in polygons:
        if not polygon:
            continue
        rings.extend(ring for ring in polygon if ring)
    return rings


def _point_in_rings(pt, rings):
    """奇偶规则：落在外环内、内环（孔洞）外才算内部。"""
    inside = False
    for ring in rings:
        if _point_in_ring(pt, ring):
            inside = not inside
    return inside


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


def _on_boundary(pt, rings, eps=1e-9):
    for ring in rings:
        for i in range(len(ring) - 1):
            if _segment_distance(pt, ring[i], ring[i + 1]) <= eps:
                return True
    return False


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
    touching = False
    for ring_a in ra:
        for ring_b in rb:
            if any(_point_in_rings(pt, rb) and not _on_boundary(pt, rb) for pt in ring_a):
                return {"relation": "overlap", "distance_m": 0.0}
            if any(_point_in_rings(pt, ra) and not _on_boundary(pt, ra) for pt in ring_b):
                return {"relation": "overlap", "distance_m": 0.0}
            for i in range(len(ring_a) - 1):
                for j in range(len(ring_b) - 1):
                    if _segments_cross(ring_a[i], ring_a[i + 1], ring_b[j], ring_b[j + 1]):
                        return {"relation": "overlap", "distance_m": 0.0}
                    if ring_a[i] == ring_b[j]:
                        touching = True
    if touching:
        return {"relation": "touching", "distance_m": 0.0}
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


def transit_window(gap_m, movement_mode, same_site=False, overlap=False, max_seconds=90.0):
    """到达时间：estimated 是优先搜索区间，search_max 是防漏检的搜索上界。

    注意 estimated_max 只是"按最低速度估算"的软上界，
    **不能**当作硬性截止：目标可能绕行、减速或停留。
    """
    speed_min, speed_max = SPEED_RANGES.get(movement_mode, SPEED_RANGES["unknown"])
    if overlap:
        # 视域重叠只说明"可能同处一片区域"，不说明时刻；窗口必须放宽，
        # 否则会把真实观测裁掉（1-1 与 2-2 的 FOV 就重叠）。
        return {"estimated_min": 0.0, "estimated_max": 20.0, "search_max": 20.0,
                "basis": "fov_overlap_window", "uncertainty": "overlap_duration_unverified"}
    if same_site:
        return {"estimated_min": SAME_SITE_TRANSFER_SECONDS[0],
                "estimated_max": SAME_SITE_TRANSFER_SECONDS[1],
                "search_max": SAME_SITE_TRANSFER_SECONDS[1],
                "basis": "same_site_pair", "uncertainty": "same_site_transfer_unverified"}
    if gap_m is None:
        return {"estimated_min": 0.0, "estimated_max": max_seconds,
                "search_max": max_seconds,
                "basis": "unknown_geometry", "uncertainty": "distance_unknown"}
    estimated_min = round(gap_m / speed_max, 2)
    estimated_max = round(gap_m / speed_min, 2)
    # 搜索上界不取 estimated_max：那会假设目标沿最短路线匀速直达
    search_max = min(max(estimated_max * 2.0, 30.0), max_seconds)
    return {"estimated_min": estimated_min, "estimated_max": estimated_max,
            "search_max": round(search_max, 2),
            "basis": "region_gap_plus_search_budget",
            "uncertainty": "detour_and_dwell_not_modelled"}


def plan_spatial_candidates(state, cameras, params=None, manual_links=(),
                            observed_cameras=()):
    """从目标空间状态生成候选摄像头；不依赖人工 camera_link，也不因此排除任何摄像头。"""
    fixed = {**DEFAULT_SPATIAL_PARAMS, **(params or {})}
    source_id = state.get("map_camera_id")
    source_point = next((c for c in cameras if c["map_camera_id"] == source_id), None)
    candidates = []
    for camera in cameras:
        if camera["map_camera_id"] == source_id:
            continue
        reason, uncertainty = [], []
        relation, fov_gap = "unknown", None
        if state.get("possible_region") and (camera.get("fov") or {}).get("observation"):
            rel = fov_relation(state["possible_region"], camera["fov"]["observation"])
            relation, fov_gap = rel["relation"], rel["distance_m"]
        if relation == "overlap":
            reason.append("fov_overlap")
            uncertainty.append("clock_offset_unknown_for_overlap")
        elif relation in ("disjoint", "touching"):
            reason.append("reachable_non_overlap")
        else:
            reason.append("spatial_relation_unknown")
            uncertainty.append("insufficient_geometry")
        # 同点位判断只用**安装点**距离；FOV 间隔只描述视域几何
        installation = None
        if source_point and source_point.get("location") and camera.get("location"):
            installation = round(haversine_m(source_point["location"], camera["location"]), 2)
        same_site = (installation is not None
                     and installation <= fixed["same_site_max_m"]
                     and relation != "overlap")
        window = transit_window(fov_gap, state.get("movement_mode", "unknown"),
                                same_site, overlap=(relation == "overlap"),
                                max_seconds=fixed["max_seconds"])
        if same_site:
            reason.append("same_site_opposite_camera")
        if installation is not None and installation > fixed["max_range_m"]:
            uncertainty.append("beyond_configured_range")
        if (camera.get("fov") or {}).get("quality") != "calibrated":
            uncertainty.append("fov_not_field_verified")
        if camera.get("spatial_revision") != state.get("spatial_revision"):
            uncertainty.append("spatial_revision_mismatch")
        if camera["map_camera_id"] in manual_links:
            reason.append("manual_link_confirmed")
        if relation == "overlap" or same_site or (
                fov_gap is not None and fov_gap <= 60 and window["estimated_min"] <= 10):
            priority = "high"
        elif fov_gap is not None and fov_gap <= 200:
            priority = "medium"
        else:
            priority = "low"
        if camera["map_camera_id"] in manual_links and priority == "medium":
            priority = "high"
        if camera["map_camera_id"] in observed_cameras:
            # 刚看过（含刚才所在镜头）：不是不可能，但优先度降低，避免原地打转
            reason.append("recently_observed_camera")
            priority = "low"
        candidates.append({
            "candidate_camera": camera["map_camera_id"],
            "display_name": camera.get("display_name"),
            "priority": priority,
            "spatial_relation": relation,
            "installation_distance_m": installation,
            "fov_gap_m": fov_gap,
            "same_site_suspected": same_site,
            "site_relation": "co_located_suspected" if same_site else "unknown",
            "transit_min": window["estimated_min"],
            "transit_max": window["estimated_max"],
            "search_max": window["search_max"],
            "transit_basis": window["basis"],
            "reasons": reason,
            "uncertainties": uncertainty,
            "spatial_revision": camera.get("spatial_revision"),
            "status": "candidate_camera",
        })
    order = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda c: (order[c["priority"]],
                                   c["fov_gap_m"] if c["fov_gap_m"] is not None else 1e9))
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
        estimate = (t0 + cand["transit_min"] - u, t0 + cand["transit_max"] + u)
        search = (t0 + cand["transit_min"] - u, t0 + cand["search_max"] + u)
        if direction == "backward":
            estimate = (t0 - cand["transit_max"] - u, t0 - cand["transit_min"] + u)
            search = (t0 - cand["search_max"] - u, t0 - cand["transit_min"] + u)
        plans.append({
            "camera": cand["candidate_camera"],
            "mode": direction,
            "window": search,
            "estimated_window": estimate,
            "priority": cand["priority"],
            "reason": ",".join(cand["reasons"]),
            "uncertainties": cand["uncertainties"],
        })
    return plans
