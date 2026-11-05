"""B8-SA1 路线 A：相机参数射线与地面平面交会（实验估计层）。

不读取人工控制点，不写正式 L2；缺内参/姿态/地面参数时明确降级。
坐标系：相机位置为原点，先算东-北-天(ENU)米制偏移，再按局部切平面转 WGS84。
"""
from __future__ import annotations

import math

import numpy as np

EARTH_M_PER_DEG = 111320.0
MIN_RANGE_M = 1.5
MAX_RANGE_M = 80.0


class ProjectionUnavailable(ValueError):
    """参数不足或几何病态，调用方必须降级而不是猜。"""


def _num(value, name, *, low=None, high=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ProjectionUnavailable(f"{name}_missing") from None
    if not math.isfinite(number):
        raise ProjectionUnavailable(f"{name}_invalid")
    if low is not None and number < low:
        raise ProjectionUnavailable(f"{name}_out_of_range")
    if high is not None and number > high:
        raise ProjectionUnavailable(f"{name}_out_of_range")
    return number


def focal_px(width, hfov_deg):
    hfov = _num(hfov_deg, "hfov_deg", low=5.0, high=170.0)
    return (float(width) / 2.0) / math.tan(math.radians(hfov) / 2.0)


def validate(camera):
    """返回归一化参数；缺一项就抛 ProjectionUnavailable。"""
    width = _num(camera.get("width"), "frame_width", low=64)
    height = _num(camera.get("height"), "frame_height", low=64)
    f = focal_px(width, camera.get("hfov_deg"))
    return {
        "width": width, "height": height, "f": f,
        "cx": float(camera.get("cx", width / 2.0)),
        "cy": float(camera.get("cy", height / 2.0)),
        "pitch_deg": _num(camera.get("pitch_deg"), "pitch_deg", low=-85, high=85),
        "yaw_deg": _num(camera.get("yaw_deg"), "yaw_deg", low=-360, high=360),
        "height_m": _num(camera.get("height_m"), "height_m", low=0.5, high=60),
        "lon": _num(camera.get("lon"), "lon", low=-180, high=180),
        "lat": _num(camera.get("lat"), "lat", low=-90, high=90),
    }


def pixel_to_enu(params, pixel):
    u, v = float(pixel[0]), float(pixel[1])
    if not (0 <= u <= params["width"] and 0 <= v <= params["height"]):
        raise ProjectionUnavailable("pixel_outside_frame")
    pitch = math.radians(params["pitch_deg"])
    yaw = math.radians(params["yaw_deg"])
    # 相机坐标：x 右, y 下, z 前
    x_c = (u - params["cx"]) / params["f"]
    y_c = (v - params["cy"]) / params["f"]
    z_c = 1.0
    # 俯仰后转水平坐标系（x 右, y 上, z 前）
    y_l = -math.cos(pitch) * y_c - math.sin(pitch) * z_c
    z_l = -math.sin(pitch) * y_c + math.cos(pitch) * z_c
    if y_l >= -1e-6:
        raise ProjectionUnavailable("ray_does_not_hit_ground")
    t = -params["height_m"] / y_l
    east_cam = t * x_c
    north_cam = t * z_l
    if north_cam <= 0 or t <= 0:
        raise ProjectionUnavailable("ray_behind_camera")
    # yaw：正北为 0，顺时针
    east = east_cam * math.cos(yaw) + north_cam * math.sin(yaw)
    north = -east_cam * math.sin(yaw) + north_cam * math.cos(yaw)
    return east, north


def to_wgs84(lon0, lat0, east, north):
    lat = lat0 + north / EARTH_M_PER_DEG
    lon = lon0 + east / (EARTH_M_PER_DEG * math.cos(math.radians(lat0)))
    return lon, lat


def project(camera, pixel, *, max_range_m=MAX_RANGE_M, min_range_m=MIN_RANGE_M):
    params = validate(camera)
    east, north = pixel_to_enu(params, pixel)
    ground_range = math.hypot(east, north)
    if ground_range < min_range_m:
        raise ProjectionUnavailable("ground_point_too_close")
    if ground_range > float(max_range_m):
        raise ProjectionUnavailable("ground_point_too_far")
    lon, lat = to_wgs84(params["lon"], params["lat"], east, north)
    return {
        "ground_point": {"x": round(lon, 7), "y": round(lat, 7), "crs": "EPSG:4326"},
        "enu_offset_m": {"east": round(east, 2), "north": round(north, 2)},
        "range_m": round(ground_range, 2),
        "spatial_level": "L2-candidate",
        "quality": "experimental_estimate",
        "identity_status": "candidate_unverified",
        "uncertainties": ["pose_reported_unverified", "intrinsics_assumed"],
    }


def uncertainty_radius(camera, pixel, *, sigma_pitch_deg=3.0, sigma_hfov_deg=10.0,
                       sigma_height_m=0.3, sigma_pos_m=3.0):
    """数值扰动估计半径；参数不全时返回 None，不虚构 ±N 米。"""
    try:
        base = project(camera, pixel)
    except ProjectionUnavailable:
        return None
    lon, lat = base["ground_point"]["x"], base["ground_point"]["y"]
    samples = []
    for key, delta, sigma in (("pitch_deg", sigma_pitch_deg, sigma_pitch_deg),
                              ("hfov_deg", sigma_hfov_deg, sigma_hfov_deg),
                              ("height_m", sigma_height_m, sigma_height_m)):
        if camera.get(key) is None:
            continue
        for sign in (1, -1):
            trial = dict(camera)
            try:
                trial[key] = float(camera[key]) + sign * delta
                result = project(trial, pixel)
            except (ProjectionUnavailable, TypeError, ValueError):
                continue
            samples.append((result["ground_point"]["x"], result["ground_point"]["y"]))
    if not samples:
        return round(sigma_pos_m, 2)
    east = [(s[0] - lon) * EARTH_M_PER_DEG * math.cos(math.radians(lat)) for s in samples]
    north = [(s[1] - lat) * EARTH_M_PER_DEG for s in samples]
    spread = max(math.hypot(e, n) for e, n in zip(east, north))
    return round(math.hypot(spread, sigma_pos_m), 2)
