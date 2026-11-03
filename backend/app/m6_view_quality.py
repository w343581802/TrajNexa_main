"""B5-Q1：目标视角质量评价（可开关、可调参、可全量核验）。

三条硬约束：
  1. 画质只影响**优先顺序**，从不把镜头从候选里删掉；
  2. 实测（动态）优先于未经现场核实的静态先验；
  3. 复杂场景可切到 verify_all —— 只排序、不裁剪，所有摄像头都会被核验。
"""
from __future__ import annotations

import numpy as np

DEFAULT_PARAMS = {"enabled": True,
                  "mode": "prioritize",          # off / observe_only / prioritize / verify_all
                  "min_target_height_px": 48.0,
                  "min_sharpness": 12.0,
                  "min_contrast": 8.0,
                  "edge_cut_penalty": 0.5,
                  "max_samples": 5}
BANDS = ("high", "medium", "low", "unknown")


def _gray(image):
    array = np.asarray(image, dtype="float32")
    if array.ndim == 3:
        array = array[..., :3].mean(axis=2)
    return array


def sharpness(image):
    """拉普拉斯方差：对运动模糊与失焦敏感，且不需要训练模型。"""
    gray = _gray(image)
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    lap = (gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
           - 4.0 * gray[1:-1, 1:-1])
    return float(lap.var())


def evaluate_crop(image, bbox, params=None):
    """单帧裁剪的可解释指标：目标尺寸、清晰度、对比度、贴边（部分可见）。"""
    params = {**DEFAULT_PARAMS, **(params or {})}
    height, width = np.asarray(image).shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox]
    x1, y1 = max(0.0, x1), max(0.0, y1)
    x2, y2 = min(float(width), x2), min(float(height), y2)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return {"usable": False, "reason": "crop_too_small"}
    crop = np.asarray(image)[int(y1):int(y2), int(x1):int(x2)]
    gray = _gray(crop)
    touches = (x1 <= 1 or y1 <= 1 or x2 >= width - 1 or y2 >= height - 1)
    return {"usable": True, "height_px": round(y2 - y1, 1), "area_px": round((x2 - x1) * (y2 - y1), 1),
            "sharpness": round(sharpness(crop), 2), "contrast": round(float(gray.std()), 2),
            "edge_cut": bool(touches)}


def _score(sample, params):
    size = min(1.0, sample["height_px"] / max(1.0, params["min_target_height_px"] * 2))
    sharp = min(1.0, sample["sharpness"] / max(1e-6, params["min_sharpness"] * 2))
    contrast = min(1.0, sample["contrast"] / max(1e-6, params["min_contrast"] * 2))
    score = 0.5 * size + 0.35 * sharp + 0.15 * contrast
    if sample.get("edge_cut"):
        score -= params["edge_cut_penalty"] * 0.2
    return max(0.0, min(1.0, score))


def evaluate_target(samples, params=None):
    """samples: [{"image": array, "bbox": [x1,y1,x2,y2]}]，来自实际录像的目标裁剪。"""
    params = {**DEFAULT_PARAMS, **(params or {})}
    if not params["enabled"] or params["mode"] == "off":
        return {"enabled": False, "band": "unknown", "score": None, "basis": "disabled"}
    metrics, failures = [], []
    for sample in list(samples)[:params["max_samples"]]:
        result = evaluate_crop(sample["image"], sample["bbox"], params)
        if not result["usable"]:
            failures.append({"reason": result["reason"]})
            continue
        result["score"] = round(_score(result, params), 3)
        metrics.append(result)
    if not metrics:
        return {"enabled": True, "band": "unknown", "score": None,
                "basis": "no_usable_sample", "failures": failures}
    score = float(np.mean([m["score"] for m in metrics]))
    median_size = float(np.median([m["height_px"] for m in metrics]))
    median_sharp = float(np.median([m["sharpness"] for m in metrics]))
    if score >= 0.6 and median_size >= params["min_target_height_px"]:
        band = "high"
    elif score >= 0.35:
        band = "medium"
    else:
        band = "low"
    return {"enabled": True, "band": band, "score": round(score, 3), "basis": "measured",
            "median_height_px": round(median_size, 1),
            "median_sharpness": round(median_sharp, 2),
            "samples": len(metrics), "failures": failures}


def rank_cameras(members, quality_by_camera, params=None):
    """把实测画质并入排序：disabled 时原样返回；verify_all 时只排序不裁剪。"""
    params = {**DEFAULT_PARAMS, **(params or {})}
    if not params["enabled"] or params["mode"] == "off":
        for member in members:
            member["quality"] = {"band": "unknown", "basis": "disabled"}
        return members
    order = {"high": 3, "medium": 2, "unknown": 1, "low": 0}
    for member in members:
        measured = quality_by_camera.get(member["camera"])
        if measured and measured.get("basis") == "measured":
            member["quality"] = measured
        else:
            prior = member.get("static_prior")
            member["quality"] = {"band": "unknown" if prior is None else "medium",
                                 "basis": "static_prior_unverified", "static_prior": prior}
    if params["mode"] in ("observe_only", "verify_all"):
        return members
    members.sort(key=lambda m: (order.get(m["quality"]["band"], 1),
                                m["coverage"]["region_in_fov"] or 0), reverse=True)
    return members
