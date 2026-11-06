"""区域卡设备解析：镜头名 → Map camera_uuid 的精确同名映射。

只认台账 display_name 或显式 aliases 的完全同名（NFKC + 去空白 + casefold），
不做距离、方位或相似度猜测；目录不可用时返回空表，区域卡保持 cameraUuid=null。
名字匹配只说明“计划检查的设备与台账同名”，不构成设备身份确认。
"""
from __future__ import annotations

import time

from app.camera_catalog import canonical_name, load_catalog

_TTL_SECONDS = 60.0
_cache: dict = {"at": 0.0, "directory": {}}


def camera_directory() -> dict[str, str]:
    """canonical_name -> camera_uuid；读取失败时保留上一次成功结果（可能为空）。"""
    now = time.monotonic()
    cached = _cache["directory"]
    if cached and now - _cache["at"] < _TTL_SECONDS:
        return cached
    try:
        catalog = load_catalog()
    except Exception:
        return cached
    directory: dict[str, str] = {}
    for camera in catalog.cameras:
        uuid = camera.get("camera_uuid")
        if not uuid:
            continue
        names = [camera.get("display_name"), *(camera.get("aliases") or [])]
        for name in names:
            if isinstance(name, str) and name.strip():
                directory.setdefault(canonical_name(name), uuid)
    _cache.update(at=now, directory=directory)
    return directory


def reset_cache() -> None:
    """测试用：清空进程内目录缓存。"""
    _cache.update(at=0.0, directory={})
