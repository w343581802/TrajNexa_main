"""B8-SA5：逐帧轨迹样本 → 实验空间轨迹段（读取时现算，不写正式观测）。

设计约束（对应实施计划 V1.2.1 §3.2 / §7 SA5 / §12.4 UI-R6）：

1. 只消费 `track_samples` 里的视频侧事实（帧号、窗口内秒数、像素框、画幅），
   地面坐标一律按**当前** Map 相机参数现算，所以 Map 姿态或标定更新后不会留旧坐标；
2. 输出恒为 `experimental_estimate` + `persisted=False`，绝不写
   `target_spatial_observations`，也不把结果当成身份证据；
3. 按时间间隔与位移断线，不补帧、不外推；接地点被画幅裁掉或靠近地平线时如实降级；
4. 参数不足时**不猜**：画幅缺失 → `frame_size_missing`，相机姿态/高度/FOV 不全 →
   `camera_parameters_incomplete`，镜头在 Map 里找不到 → `camera_not_resolved`。
"""
from __future__ import annotations

from app import m6_target_binding
from app import m6_track_spatial

MAX_SEGMENTS = 12
DEFAULT_MIN_POINTS = 2


def camera_params(camera: dict | None, frame_size) -> tuple[dict | None, str | None]:
    """把 Map 快照镜头 + 画幅拼成射线投影参数；缺项返回 (None, reason)。"""
    if not camera:
        return None, "camera_not_resolved"
    try:
        width, height = int(frame_size[0]), int(frame_size[1])
    except (IndexError, TypeError, ValueError):
        return None, "frame_size_missing"
    if width <= 0 or height <= 0:
        return None, "frame_size_missing"
    if not camera.get("ray_projection_ready"):
        return None, "camera_parameters_incomplete"
    location = camera.get("location") or {}
    lon, lat = location.get("lon"), location.get("lat")
    if lon is None or lat is None:
        return None, "camera_position_missing"
    params = {
        "width": width,
        "height": height,
        "hfov_deg": camera.get("horizontal_fov_deg"),
        "pitch_deg": camera.get("tilt_deg"),
        "yaw_deg": camera.get("bearing_deg"),
        "height_m": camera.get("mount_height_m"),
        "lon": float(lon),
        "lat": float(lat),
    }
    if any(params[key] is None for key in
           ("hfov_deg", "pitch_deg", "yaw_deg", "height_m")):
        return None, "camera_parameters_incomplete"
    return params, None


def _frame_of(row) -> dict:
    return {"t": float(row.timestamp_seconds or 0.0),
            "frame": int(row.frame_id or 0),
            "box": [float(v) for v in (row.bbox_json or [])]}


def _normalize_point(point: dict) -> dict:
    """投影模块用 x=lon / y=lat 存 WGS84；前端地图要 lat/lon，这里补别名字段。"""
    ground = point.get("ground_point")
    if isinstance(ground, dict) and "x" in ground and "y" in ground:
        ground.setdefault("lon", ground["x"])
        ground.setdefault("lat", ground["y"])
        ground.setdefault("crs", "EPSG:4326")
    return point


def build_segments(camera: dict | None, rows, *, frame_size=None, kind="person",
                   playback=None, max_gap_s=m6_target_binding.MAX_GAP_S,
                   min_points=DEFAULT_MIN_POINTS, evidence=None) -> dict:
    """一目标一轨迹的样本 → 分段实验轨迹。返回结构化结果，不抛参数错误。"""
    playback = dict(playback or {})
    frames = [_frame_of(row) for row in rows or []]
    frames = [f for f in frames if len(f["box"]) == 4]
    if not frames:
        return _empty("no_samples")

    size = frame_size
    if size in (None, [0, 0]):
        first = rows[0]
        size = [getattr(first, "frame_width", 0), getattr(first, "frame_height", 0)]
    params, reason = camera_params(camera, size)

    segments = []
    projected = degraded = points_total = 0
    reasons: dict[str, int] = {}
    dropped = 0
    for chunk, break_reason in m6_target_binding.segment_frames(frames, max_gap_s=max_gap_s):
        if len(chunk) < min_points:
            dropped += 1
            continue
        if len(segments) >= MAX_SEGMENTS:
            break
        points = []
        for frame in chunk:
            points_total += 1
            point_evidence = {
                **(evidence or {}),
                "track_id": (rows[0].track_id if rows else None),
                "frame": frame["frame"],
                "clip_id": playback.get("clip_id"),
                "time_basis": playback.get("time_basis"),
                "time_quality": playback.get("time_quality"),
                "day_seconds": frame["t"],
            }
            if params is None:
                # 参数不足也保留视频侧轨迹：只给像素框与降级原因，不给假坐标。
                points.append({"t": frame["t"], "frame": frame["frame"],
                               "box": frame["box"], "spatial_level": "L0",
                               "degrade_reason": reason, "evidence": point_evidence})
                degraded += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            report = m6_track_spatial.project_track(
                params, [{"t": frame["t"], "box": frame["box"]}],
                kind=kind, evidence=point_evidence)
            point = dict(report["points"][0])
            point["frame"] = frame["frame"]
            _normalize_point(point)
            points.append(point)
            if point.get("spatial_level") == "L2-candidate":
                projected += 1
            else:
                degraded += 1
                code = str(point.get("degrade_reason") or "projection_degraded")
                reasons[code] = reasons.get(code, 0) + 1
        first_t, last_t = chunk[0]["t"], chunk[-1]["t"]
        segments.append({
            "track_id": (rows[0].track_id if rows else None),
            "break_reason": break_reason,
            "start_seconds": round(first_t, 3),
            "end_seconds": round(last_t, 3),
            "duration_seconds": round(max(0.0, last_t - first_t), 3),
            "spatial_level": "L2-candidate" if params else "L0",
            "quality": "experimental_estimate" if params else "camera_only",
            "points": points,
        })

    return {
        "layer": "experimental_estimate",
        "persisted": False,
        "identity_status": "candidate_unverified",
        "note": "逐帧框推得的实验估计轨迹；未经标定审核与独立真值验证，不写正式 L2/L3",
        "camera_ready": params is not None,
        "degrade_reason": reason,
        "segments": segments,
        "summary": {"segments": len(segments), "points": points_total,
                    "projected": projected, "degraded": degraded,
                    "dropped_short_segments": dropped,
                    "degrade_reasons": reasons},
    }


def _empty(reason: str) -> dict:
    return {"layer": "experimental_estimate", "persisted": False,
            "identity_status": "candidate_unverified",
            "note": "没有可用的逐帧轨迹样本；请先跑完一次研判窗口",
            "camera_ready": False, "degrade_reason": reason, "segments": [],
            "summary": {"segments": 0, "points": 0, "projected": 0, "degraded": 0,
                        "dropped_short_segments": 0, "degrade_reasons": {}}}