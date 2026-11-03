"""B5-Q0：同一观察区域的摄像头分组与择优。

关键约束：
  1. 分组依据是"能否观察到目标可能经过的区域"，不是安装点距离；
  2. 相邻但朝向相反的镜头不会因为靠得近被合组；
  3. 相距较远但都能覆盖同一路口的镜头会被合组；
  4. 没有 FOV 的镜头单独成组并标 fov_missing，绝不按距离猜。
"""
from __future__ import annotations

import hashlib
import math

from .m6_spatial_planner import _local, _point_in_rings, _rings, fov_relation
from .m6_view_quality import DEFAULT_PARAMS as QUALITY_DEFAULTS, rank_cameras

RELATION_RANK = {"contains_region": 4, "partial_overlap": 3, "inside_region": 2,
                 "disjoint": 1, "unknown": 0}


def region_from_state(state, radius_m=25.0):
    """目标可能区域：有 polygon 用 polygon，否则按到达点生成方框（粗糙但可解释）。"""
    polygon = (state or {}).get("possible_region")
    if polygon:
        return {"geometry": polygon, "source": "possible_region"}
    point = (state or {}).get("last_seen") or {}
    lon, lat = point.get("lon"), point.get("lat")
    if lon is None or lat is None:
        return {"geometry": None, "source": "no_region"}
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(0.2, abs(math.cos(math.radians(lat)))))
    ring = [[lon - dlon, lat - dlat], [lon + dlon, lat - dlat],
            [lon + dlon, lat + dlat], [lon - dlon, lat + dlat],
            [lon - dlon, lat - dlat]]
    return {"geometry": {"type": "Polygon", "coordinates": [ring]},
            "source": "arrival_point_buffer", "radius_m": radius_m}


def region_id(geometry, bucket_seconds=None):
    if geometry is None:
        return "region_unknown"
    payload = repr(geometry) + ("@%s" % bucket_seconds if bucket_seconds is not None else "")
    return "region:" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def coverage(camera, region_geometry):
    """该镜头能观察到这个区域吗；fov 缺失时返回 None 而不是猜。

    判定用顶点参与率，比多边形关系函数更保守、也更好解释：
      region_in_fov：区域顶点落在 FOV 内的比例；
      fov_in_region：FOV 顶点落在区域内的比例（FOV 比区域小的情况）。
    任何一个 > 0 才算可能观察到，relation 给出方向。
    """
    fov = (camera.get("fov") or {}).get("observation")
    if not fov:
        return {"covers": None, "relation": "unknown", "region_in_fov": None,
                "fov_in_region": None, "reason": "fov_missing"}
    if region_geometry is None:
        return {"covers": None, "relation": "unknown", "region_in_fov": None,
                "fov_in_region": None, "reason": "region_unknown"}
    region_rings = _rings(region_geometry)
    fov_rings = _rings(fov)
    region_vertices = [pt for ring in region_rings for pt in ring]
    fov_vertices = [pt for ring in fov_rings for pt in ring]
    origin = {"lon": region_vertices[0][0], "lat": region_vertices[0][1]}
    fov_local = [[_local({"lon": pt[0], "lat": pt[1]}, origin) for pt in ring]
                 for ring in fov_rings]
    region_local = [[_local({"lon": pt[0], "lat": pt[1]}, origin) for pt in ring]
                    for ring in region_rings]
    in_fov = sum(1 for pt in region_vertices
                 if _point_in_rings(_local({"lon": pt[0], "lat": pt[1]}, origin), fov_local))
    in_region = sum(1 for pt in fov_vertices
                    if _point_in_rings(_local({"lon": pt[0], "lat": pt[1]}, origin), region_local))
    region_in_fov = round(in_fov / float(len(region_vertices)), 3)
    fov_in_region = round(in_region / float(len(fov_vertices)), 3)
    covers = region_in_fov > 0 or fov_in_region > 0
    if region_in_fov >= 0.999:
        relation = "contains_region"
    elif fov_in_region >= 0.999:
        relation = "inside_region"
    elif covers:
        relation = "partial_overlap"
    else:
        relation = "disjoint"
    return {"covers": covers, "relation": relation,
            "region_in_fov": region_in_fov, "fov_in_region": fov_in_region,
            "reason": None}

def group_observers(region, cameras, windows=None, max_observers=3, quality_prior=None,
                    quality_by_camera=None, quality_params=None):
    """把候选镜头分成"能看同一区域"的组；同组内按覆盖程度排序取前 N。"""
    windows = windows or {}
    quality_prior = quality_prior or {}
    members, excluded = [], []
    for camera in cameras:
        camera_id = camera.get("map_camera_id")
        cover = coverage(camera, region["geometry"])
        if cover["covers"] is None:
            excluded.append({"camera": camera_id, "reason": cover["reason"]})
            continue
        if not cover["covers"]:
            excluded.append({"camera": camera_id, "reason": "fov_does_not_cover_region",
                             "relation": cover["relation"]})
            continue
        window = windows.get(camera_id)
        members.append({"camera": camera_id, "camera_uuid": camera.get("camera_uuid"),
                        "window": window, "coverage": cover,
                        "static_prior": quality_prior.get(camera_id)})
    members.sort(key=lambda m: (m["coverage"]["region_in_fov"] or 0,
                                RELATION_RANK.get(m["coverage"]["relation"], 0),
                                (m["static_prior"] or 0)), reverse=True)
    quality_params = {**QUALITY_DEFAULTS, **(quality_params or {})}
    if quality_by_camera is not None:
        rank_cameras(members, quality_by_camera, quality_params)
        if quality_params["mode"] == "verify_all":
            max_observers = len(members)
        elif quality_params["mode"] == "prioritize":
            quality_order = {"high": 3, "medium": 2, "unknown": 1, "low": 0}
            members.sort(key=lambda m: (quality_order.get(m["quality"]["band"], 1),
                                        m["coverage"]["region_in_fov"] or 0), reverse=True)
    for index, member in enumerate(members):
        member["rank"] = index + 1
        member["selected"] = index < max_observers
    return {"region_id": region_id(region["geometry"]),
            "region_source": region["source"], "limit": max_observers,
            "members": members, "excluded": excluded,
            "note": "static_prior 只是未经现场核实的先验；动态画质在 B5-Q1 评价"}
