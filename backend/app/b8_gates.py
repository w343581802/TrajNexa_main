"""B8 治理开关（V1.2 §9.6）：实验估计层、自动标定任务、自动地理发布。

关闭时实验端点直接 404，不返回半套数据；正式 L2 读取路径不受影响。
"""
from __future__ import annotations

from fastapi import HTTPException

from app.config import settings


def experimental_geo_enabled() -> bool:
    return bool(settings.b8_experimental_geo_display)


def _hidden(code: str, message: str):
    # 与全站错误信封保持一致：{"error": {"code", "message", "details"}}
    raise HTTPException(status_code=404, detail={"code": code, "message": message, "details": None})


def require_experimental_geo_display() -> None:
    if not experimental_geo_enabled():
        # 404 而不是 403：关闭时这些路由看起来就是不存在，避免前端把它当成权限问题去重试。
        _hidden("experimental_geo_display_disabled",
                "实验估计图层未启用（B8_EXPERIMENTAL_GEO_DISPLAY=false）。")


def require_auto_calibration() -> None:
    if not settings.b8_auto_calibration_enabled:
        _hidden("auto_calibration_disabled", "自动标定任务未启用（B8_AUTO_CALIBRATION_ENABLED=false）。")


def auto_geo_publish_enabled() -> bool:
    return bool(settings.b8_auto_geo_publish_enabled)