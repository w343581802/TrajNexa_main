"""区域聚合用的 Map 相机坐标（只读）。

坐标只用于判断“计划检查的设备是否在小范围内相邻”，不确认镜头身份，
也不代表目标到达。读取失败时返回空表，调用方回退到“完全同名镜头”分组，
绝不按名称相似度或道路方向猜相邻关系。
"""
from __future__ import annotations

import time

from app.camera_catalog import canonical_name
from app.config import settings
from app.map_db_spatial import SpatialReadError, read_map_spatial

_TTL_SECONDS = 60.0
_cache: dict = {"at": 0.0, "positions": {}}


def camera_positions(spatial_reader=None) -> dict[str, dict]:
    """canonical_name -> {camera_uuid, lat, lon}；读取失败保留上次成功结果。"""
    now = time.monotonic()
    cached = _cache["positions"]
    if cached and now - _cache["at"] < _TTL_SECONDS:
        return cached
    dsn = settings.map_db_readonly_url.strip()
    if not dsn:
        return cached
    try:
        payload = (spatial_reader or read_map_spatial)(dsn=dsn)
    except (SpatialReadError, OSError, ValueError):
        return cached
    positions: dict[str, dict] = {}
    for camera in (payload or {}).get("cameras") or []:
        name = camera.get("display_name")
        location = camera.get("location") or {}
        try:
            lat = float(location.get("lat"))
            lon = float(location.get("lon"))
        except (TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        positions[canonical_name(name)] = {
            "camera_uuid": camera.get("camera_uuid"),
            "lat": lat,
            "lon": lon,
        }
    _cache.update(at=now, positions=positions)
    return positions


def reset_cache() -> None:
    """测试用：清空进程内坐标缓存。"""
    _cache.update(at=0.0, positions={})