"""B8-S3：图像地面点 → 世界坐标的 L2 投影（仅使用已审核标定）。

约束：
- 没有经人工审核的标定，一律拒绝并让调用方退回 L1/L0。
- 像素必须能还原到标定参考帧（等比缩放）；裁剪/拉伸/旋转无法还原时拒绝。
- 接地点必须落在标定有效多边形内；外推点不接受。
- 误差未量化（缺少留出点残差）时不得给出 ±N 米，只报“未量化”。
"""
from __future__ import annotations

import math

APPROVED = ("approved", "human_verified")


class GroundCalibrationError(ValueError):
    """稳定错误码，调用方据此降级而不是抛 500。"""


def _float_matrix(value):
    if not isinstance(value, list) or len(value) != 3:
        raise GroundCalibrationError("calibration_homography_invalid")
    rows = []
    for row in value:
        if not isinstance(row, list) or len(row) != 3:
            raise GroundCalibrationError("calibration_homography_invalid")
        try:
            rows.append([float(v) for v in row])
        except (TypeError, ValueError):
            raise GroundCalibrationError("calibration_homography_invalid") from None
        if not all(math.isfinite(v) for v in rows[-1]):
            raise GroundCalibrationError("calibration_homography_invalid")
    return rows


def validate(calibration) -> dict:
    if not isinstance(calibration, dict):
        raise GroundCalibrationError("calibration_missing")
    if calibration.get("review_status") not in APPROVED:
        raise GroundCalibrationError("calibration_not_approved")
    for key in ("calibration_id", "camera_uuid", "world_crs"):
        if not isinstance(calibration.get(key), str) or not calibration.get(key).strip():
            raise GroundCalibrationError("calibration_" + key + "_missing")
    width = calibration.get("reference_width")
    height = calibration.get("reference_height")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
               for v in (width, height)):
        raise GroundCalibrationError("calibration_reference_size_invalid")
    polygon = calibration.get("ground_valid_polygon")
    if not isinstance(polygon, list) or len(polygon) < 3:
        raise GroundCalibrationError("calibration_valid_polygon_missing")
    points = []
    for item in polygon:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise GroundCalibrationError("calibration_valid_polygon_invalid")
        try:
            points.append([float(item[0]), float(item[1])])
        except (TypeError, ValueError):
            raise GroundCalibrationError("calibration_valid_polygon_invalid") from None
    return {"homography": _float_matrix(calibration.get("homography_3x3")),
            "width": float(width), "height": float(height),
            "polygon": points, "calibration": calibration}


def _scale_to_reference(pixel, frame_size, ref_width, ref_height):
    x, y = float(pixel[0]), float(pixel[1])
    if frame_size is None:
        return x, y, 1.0, []
    fw, fh = float(frame_size[0]), float(frame_size[1])
    if fw <= 0 or fh <= 0:
        raise GroundCalibrationError("frame_size_invalid")
    sx, sy = ref_width / fw, ref_height / fh
    if abs(sx - sy) / max(sx, sy) > 0.01:
        raise GroundCalibrationError("frame_geometry_mismatch")
    uncertainties = []
    if abs(sx - 1.0) > 1e-6:
        uncertainties.append("frame_rescaled_to_reference")
    return x * sx, y * sy, sx, uncertainties


def project(calibration, pixel, frame_size=None, contact_offset_m=None):
    """返回 {spatial_level, ground_point, uncertainty_radius_m, ...}；失败抛 GroundCalibrationError。"""
    checked = validate(calibration)
    x, y, _scale, uncertainties = _scale_to_reference(
        pixel, frame_size, checked["width"], checked["height"])
    if not (0 <= x <= checked["width"] and 0 <= y <= checked["height"]):
        raise GroundCalibrationError("ground_point_outside_reference_frame")
    import cv2
    import numpy as np
    inside = cv2.pointPolygonTest(np.array(checked["polygon"], dtype="float32"),
                                  (x, y), False)
    if inside < 0:
        raise GroundCalibrationError("ground_point_outside_valid_polygon")
    h = checked["homography"]
    denominator = h[2][0] * x + h[2][1] * y + h[2][2]
    if abs(denominator) < 1e-9:
        raise GroundCalibrationError("ground_homography_degenerate")
    wx = (h[0][0] * x + h[0][1] * y + h[0][2]) / denominator
    wy = (h[1][0] * x + h[1][1] * y + h[1][2]) / denominator
    if not (math.isfinite(wx) and math.isfinite(wy)):
        raise GroundCalibrationError("ground_homography_degenerate")
    residual = calibration.get("holdout_residual_m") if isinstance(
        calibration.get("holdout_residual_m"), dict) else {}
    p90 = residual.get("p90")
    radius = None
    if isinstance(p90, (int, float)) and not isinstance(p90, bool) and p90 >= 0:
        radius = round(float(p90) + float(contact_offset_m or 0.0), 2)
        if contact_offset_m:
            uncertainties = uncertainties + ["ground_contact_approximate"]
    else:
        uncertainties = uncertainties + ["residual_unquantified"]
    return {"schema": "b8s-ground-projection/1",
            "spatial_level": "L2",
            "ground_point": {"x": round(wx, 3), "y": round(wy, 3),
                             "crs": calibration["world_crs"]},
            "uncertainty_radius_m": radius,
            "within_valid_polygon": True,
            "calibration_id": calibration["calibration_id"],
            "calibration_revision": calibration.get("calibration_revision"),
            "camera_uuid": calibration["camera_uuid"],
            "quality": "estimated_review_required",
            "uncertainties": uncertainties}
