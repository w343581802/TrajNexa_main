"""QWEN_CV 对照：Qwen 只给场景结构/候选线索，几何仍由 OpenCV 计算。

Qwen 输出不得包含或被解释为经纬度、焦距、俯角数值；解析后只保留区域与候选方向描述。
"""
from __future__ import annotations

import json

UNKNOWN = "unknown"
REGION_KEYS = ("road_region", "occlusion_regions")


class SceneGeometryError(ValueError):
    pass


def _norm_box(box, width, height):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if max(x1, x2) <= 1.5 and max(y1, y2) <= 1.5:
        x1, x2, y1, y2 = x1 * width, x2 * width, y1 * height, y2 * height
    x1, x2 = sorted((max(0.0, x1), min(width, x2)))
    y1, y2 = sorted((max(0.0, y1), min(height, y2)))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)]


def parse_scene_json(text, *, width, height):
    """解析 Qwen 输出；不可用字段一律降级为 unknown，绝不补齐猜测值。"""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise SceneGeometryError("scene_json_invalid") from None
    if not isinstance(data, dict):
        raise SceneGeometryError("scene_json_invalid")
    result = {"road_region": None, "occlusion_regions": [],
              "road_direction": UNKNOWN, "structures": [], "uncertainties": []}
    box = _norm_box(data.get("road_region"), width, height)
    result["road_region"] = box
    for item in data.get("occlusion_regions") or []:
        cleaned = _norm_box(item, width, height)
        if cleaned:
            result["occlusion_regions"].append(cleaned)
    direction = str(data.get("road_direction") or UNKNOWN).lower()
    result["road_direction"] = direction if direction in ("left", "center", "right") else UNKNOWN
    for item in (data.get("structures") or [])[:8]:
        if isinstance(item, str) and item.strip():
            result["structures"].append(item.strip()[:60])
    for item in (data.get("uncertainties") or [])[:8]:
        if isinstance(item, str) and item.strip():
            result["uncertainties"].append(item.strip()[:120])
    result["usable"] = result["road_region"] is not None
    result["source"] = "qwen_scene_structure"
    return result


def inside_region(segment, region):
    if region is None:
        return True
    x1, y1, x2, y2 = segment
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    return region[0] <= mx <= region[2] and region[1] <= my <= region[3]


def restrict_segments(segments, scene):
    """按 Qwen 路面区域与遮挡区过滤线段；Qwen 不可用时原样返回（保底）。"""
    if not scene or not scene.get("usable"):
        return {"segments": segments, "dropped": 0, "mode": "baseline_all"}
    kept = [s for s in segments if inside_region(s, scene["road_region"])]
    for box in scene.get("occlusion_regions") or []:
        kept = [s for s in kept if not inside_region(s, box)]
    return {"segments": kept, "dropped": len(segments) - len(kept), "mode": "qwen_region"}


def compare_reports(baseline, qwen_cv):
    """对照两种输入下的 CV 结果；只描述差异，不判定谁更“真”。"""
    def _vp(report):
        items = report.get("candidates") or []
        return items[0]["vanishing_point_px"] if items else None
    base_vp, qwen_vp = _vp(baseline), _vp(qwen_cv)
    result = {"baseline_status": baseline.get("status"), "qwen_cv_status": qwen_cv.get("status"),
              "baseline_vp": base_vp, "qwen_cv_vp": qwen_vp,
              "agreement": None, "note": "两种输入都未经独立地理真值验证"}
    if base_vp and qwen_vp:
        gap = ((base_vp[0] - qwen_vp[0]) ** 2 + (base_vp[1] - qwen_vp[1]) ** 2) ** 0.5
        result["agreement"] = "close" if gap <= 0.05 * report_width(baseline) else "divergent"
        result["gap_px"] = round(gap, 1)
    return result


def report_width(report):
    return float((report.get("frame") or {}).get("width") or 1920)
