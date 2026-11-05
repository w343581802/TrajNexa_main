"""B8-S4：把多条 L2 地面观测组合为可审计的局部轨迹片段。

只做证据整理，不认定身份；单点不推方向，时钟未校准不推速度。
"""
from __future__ import annotations

import math

EARTH_R = 6371000.0


def haversine_m(a, b):
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def bearing_deg(a, b):
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return round((math.degrees(math.atan2(y, x)) + 360.0) % 360.0, 1)


def _point(row):
    ground = row.ground_point or {}
    if row.spatial_level != "L2" or ground.get("crs") != "EPSG:4326":
        return None
    try:
        lon, lat = float(ground["x"]), float(ground["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"lon": lon, "lat": lat,
            "observation_id": row.observation_id,
            "uncertainty_radius_m": row.uncertainty_radius_m,
            "at": row.created_at}


def build_segments(rows, gap_seconds=900.0):
    """按时间排序并切段；返回片段与无法使用的记录数。"""
    points, skipped = [], 0
    for row in rows:
        point = _point(row)
        if point is None or point["at"] is None:
            skipped += 1
            continue
        points.append(point)
    points.sort(key=lambda p: p["at"])
    segments, current = [], []
    for point in points:
        if current and (point["at"] - current[-1]["at"]).total_seconds() > gap_seconds:
            segments.append(current)
            current = []
        current.append(point)
    if current:
        segments.append(current)
    output = []
    for index, segment in enumerate(segments, start=1):
        length = sum(haversine_m((a["lon"], a["lat"]), (b["lon"], b["lat"]))
                     for a, b in zip(segment, segment[1:]))
        first, last = segment[0], segment[-1]
        duration = (last["at"] - first["at"]).total_seconds()
        quality = "trajectory_estimated_review_required" if len(segment) >= 2 else "single_point"
        output.append({
            "segment": index,
            "points": [{"lon": p["lon"], "lat": p["lat"],
                        "observation_id": p["observation_id"],
                        "uncertainty_radius_m": p["uncertainty_radius_m"],
                        "at": p["at"].isoformat()} for p in segment],
            "point_count": len(segment),
            "start_at": first["at"].isoformat(),
            "end_at": last["at"].isoformat(),
            "length_m": round(length, 1),
            "direction_deg": (bearing_deg((first["lon"], first["lat"]), (last["lon"], last["lat"]))
                              if len(segment) >= 2 else None),
            "quality": quality,
            "identity_status": "candidate_unverified",
            "uncertainties": ["position_estimated_review_required",
                              "osd_clock_uncalibrated"] + (
                                  ["single_point_no_direction"] if len(segment) < 2 else []),
            "duration_seconds": round(duration, 1) if duration >= 0 else None,
        })
    return {"segments": output, "skipped_rows": skipped}


def spatial_priority(point, camera_lon, camera_lat, base_m=300.0):
    """软优先级：越接近估计点越高（0..1），只用于排序，绝不作为排除依据。"""
    if point is None:
        return 0.0
    distance = haversine_m((point[0], point[1]), (camera_lon, camera_lat))
    return round(max(0.0, 1.0 - distance / float(base_m)), 3)
