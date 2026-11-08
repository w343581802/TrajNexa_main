"""M6 空间数据适配层（S6-15A）。

把 map_db_spatial 的只读快照转成追踪引擎可直接消费的摄像头空间对象，并强制三条边界：

1. 视频通道 ↔ Map 摄像头必须**显式绑定**，不允许按名称相似度自动确认；
2. 位姿质量（pose_quality）与视域质量（fov quality）分别保存，位姿已校准 ≠ 现场覆盖已验证；
3. has_video 只表示 Map 有参考帧，**不代表**某段时间有可播放录像。
"""
from __future__ import annotations

import json
import math

BINDING_VERIFIED = "human_verified"
BINDING_PENDING = "pending_human_review"
BINDING_NONE = "unbound"
FOV_QUALITIES = ("calibrated", "modelled_unverified", "unknown")


class SpatialBindingError(ValueError):
    """绑定歧义或数据缺失；错误码可直接对外。"""


def load_bindings(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data.get("channels"), dict):
        raise SpatialBindingError("bindings_missing_channels")
    return data


def name_match_guard(name, cameras):
    """禁止用名称相似度绑定：命中多台即报歧义，命中零台不猜。"""
    hits = [c for c in cameras if str(c.get("display_name") or "") == name]
    if len(hits) > 1:
        raise SpatialBindingError("camera_name_ambiguous:%d" % len(hits))
    return hits[0] if hits else None


def camera_spec(camera, spatial_revision, binding_status=BINDING_NONE):
    levels = {item["level"]: item for item in camera.get("fov_levels") or []}
    fov_quality = ("modelled_unverified" if levels
                   else "unknown")
    return {
        "camera_uuid": camera.get("camera_uuid"),
        "map_camera_id": camera["map_camera_id"],
        "display_name": camera.get("display_name"),
        "location": camera.get("location"),
        "bearing_deg": camera.get("bearing_deg"),
        "tilt_deg": camera.get("tilt_deg"),
        "horizontal_fov_deg": camera.get("horizontal_fov_deg"),
        "horizontal_fov_source": camera.get("horizontal_fov_source"),
        "range_m": camera.get("range_m"),
        "fov": {
            "observation": (levels.get("observation") or {}).get("geometry"),
            "recognition": (levels.get("recognition") or {}).get("geometry"),
            "identification": (levels.get("identification") or {}).get("geometry"),
            "quality": fov_quality,
        },
        "pose_quality": camera.get("pose_quality"),
        "position_quality": camera.get("position_quality"),
        "recording_availability": "unknown",
        "recording_note": "has_video 只表示存在参考帧，历史录像可用性须由录像索引确认",
        "spatial_revision": spatial_revision,
        "camera_binding_status": binding_status,
    }


def bind_channel(payload, bindings, channel):
    """解析一个视频通道路由；未绑定返回 unbound 对象而不是编造摄像头。"""
    entry = bindings["channels"].get(channel)
    if not entry:
        return {"channel": channel, "status": BINDING_NONE, "camera": None,
                "reason": "channel_not_in_binding_table"}
    status = entry.get("status")
    if status not in (BINDING_VERIFIED, BINDING_PENDING):
        raise SpatialBindingError("invalid_binding_status:%s" % status)
    uuid = entry.get("camera_uuid")
    match = [c for c in payload["cameras"] if c.get("camera_uuid") == uuid]
    if len(match) != 1:
        return {"channel": channel, "status": BINDING_NONE, "camera": None,
                "reason": "camera_uuid_not_in_map:%s" % uuid}
    spec = camera_spec(match[0], payload["spatial_revision"], status)
    if status == BINDING_PENDING:
        spec["camera_binding_note"] = "名称相似，尚未人工核实，仅作低置信度先验"
    return {"channel": channel, "status": status, "camera": spec,
            "reason": entry.get("evidence", "")}


def haversine_m(a, b):
    lon1, lat1 = math.radians(a["lon"]), math.radians(a["lat"])
    lon2, lat2 = math.radians(b["lon"]), math.radians(b["lat"])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def bearing_between(a, b):
    lon1, lat1 = math.radians(a["lon"]), math.radians(a["lat"])
    lon2, lat2 = math.radians(b["lon"]), math.radians(b["lat"])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def point_in_polygon(point, geometry):
    """GeoJSON Polygon/MultiPolygon 射线法；只用于粗略覆盖判断。"""
    if not geometry:
        return None
    polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                else geometry["coordinates"])
    lon, lat = point["lon"], point["lat"]
    inside = False
    for polygon in polygons:
        ring = polygon[0]
        for i in range(len(ring) - 1):
            x1, y1 = ring[i][0], ring[i][1]
            x2, y2 = ring[i + 1][0], ring[i + 1][1]
            if (y1 > lat) != (y2 > lat):
                xin = (x2 - x1) * (lat - y1) / (y2 - y1 + 1e-15) + x1
                if lon < xin:
                    inside = not inside
    return inside
