"""B8-S2 标定探针：控制点 → 地面单应矩阵 + 留出点米制误差。

只做计算与报告，不写数据库、不改追踪模型。用法：
  python scripts/s2_calibration_probe.py points.json
points.json:
  {"reference_width":1920,"reference_height":1080,"world_crs":"local_enu",
   "fit":[{"image":[x,y],"ground":[X,Y]}, ...],
   "holdout":[{"image":[x,y],"ground":[X,Y]}, ...]}
"""
from __future__ import annotations

import json
import math
import sys

import cv2
import numpy as np


def _arrays(points):
    image = np.array([p["image"] for p in points], dtype="float64")
    ground = np.array([p["ground"] for p in points], dtype="float64")
    return image, ground


def _meter_scales(ground, world_crs):
    if str(world_crs or '').upper() in ('EPSG:4326', 'WGS84', 'CRS:84'):
        lat = float(np.mean(ground[:, 1]))
        return 111320.0 * math.cos(math.radians(lat)), 110540.0
    return 1.0, 1.0


def _errors_m(delta, scales):
    return np.hypot(delta[:, 0] * scales[0], delta[:, 1] * scales[1])


def _stats(values):
    values = np.array(values, dtype="float64")
    return {"count": int(values.size),
            "median": round(float(np.median(values)), 3),
            "p90": round(float(np.percentile(values, 90)), 3),
            "max": round(float(values.max()), 3)}


def calibrate(payload):
    fit = payload.get("fit") or []
    if len(fit) < 4:
        raise SystemExit("need_at_least_4_fit_points")
    holdout = payload.get("holdout") or []
    image, ground = _arrays(fit)
    homography, mask = cv2.findHomography(image, ground, cv2.RANSAC, 3.0)
    if homography is None:
        raise SystemExit("homography_failed")
    mask = mask.ravel().astype(bool)
    projected_fit = cv2.perspectiveTransform(image.reshape(-1, 1, 2), homography).reshape(-1, 2)
    scales = _meter_scales(ground, payload.get("world_crs"))
    fit_errors = _errors_m(projected_fit - ground, scales)
    report = {"homography_3x3": homography.tolist(),
              "residual_unit": "m",
              "fit_points": len(fit),
              "inliers": int(mask.sum()),
              "outliers": [int(i) for i, ok in enumerate(mask) if not ok],
              "fit_residual_m": _stats(fit_errors)}
    if holdout:
        h_image, h_ground = _arrays(holdout)
        projected = cv2.perspectiveTransform(h_image.reshape(-1, 1, 2), homography).reshape(-1, 2)
        errors = _errors_m(projected - h_ground, scales)
        report["holdout_residual_m"] = _stats(errors)
        report["holdout_errors_m"] = [round(float(v), 3) for v in errors]
    return report


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: s2_calibration_probe.py points.json")
    with open(argv[1], "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    report = calibrate(payload)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if "holdout_residual_m" in report:
        gate = report["holdout_residual_m"]["p90"]
        print("\nHOLDOUT_P90_M=%.3f" % gate)


if __name__ == "__main__":
    main(sys.argv)
