"""B4.1-A：每路录像独立的时间映射。

一个任务里不同摄像头/录像可能有不同的 OSD 锚点、采样率与偏移，
不能用同一个 --anchor 套到所有镜头（2-1 锚点 53998，1-1/2-2 为 53997 就是一例）。
"""
from __future__ import annotations


class TimeMapError(ValueError):
    pass


def frame_time_map(recording_id, camera_uuid, anchor_seconds, fps,
                   start_index=0, frame_count=None, clock_quality="uncalibrated",
                   offset_seconds=0.0, revision=1):
    """anchor_seconds : 第 start_index 帧对应的绝对 OSD 秒；None 表示无绝对时间。"""
    if fps <= 0:
        raise TimeMapError("invalid_fps")
    if anchor_seconds is not None and offset_seconds is None:
        raise TimeMapError("invalid_offset")
    return {"recording_id": recording_id, "camera_uuid": camera_uuid,
            "anchor_seconds": anchor_seconds, "fps": float(fps),
            "start_index": int(start_index), "frame_count": frame_count,
            "clock_quality": clock_quality, "offset_seconds": float(offset_seconds or 0.0),
            "revision": revision,
            "comparable_across_cameras": bool(anchor_seconds is not None
                                              and clock_quality == "calibrated")}


def time_at(time_map, frame_index):
    """帧号 → 绝对秒；无绝对锚点时返回 None，绝不猜。"""
    anchor = time_map.get("anchor_seconds")
    if anchor is None:
        return None
    relative = (int(frame_index) - int(time_map["start_index"])) / time_map["fps"]
    return round(anchor + relative + time_map.get("offset_seconds", 0.0), 3)


def frame_at(time_map, seconds):
    anchor = time_map.get("anchor_seconds")
    if anchor is None:
        raise TimeMapError("no_absolute_time")
    return int(round(int(time_map["start_index"])
                     + (seconds - anchor - time_map.get("offset_seconds", 0.0)) * time_map["fps"]))


def span_for_window(time_map, start_seconds, end_seconds):
    """时间窗 → 帧区间（闭区间），并回读真实起止时间用于核对。"""
    first = frame_at(time_map, start_seconds)
    last = frame_at(time_map, end_seconds)
    if time_map.get("frame_count") is not None:
        last = min(last, int(time_map["start_index"]) + int(time_map["frame_count"]) - 1)
    return {"start_index": max(first, int(time_map["start_index"])), "end_index": last,
            "start_seconds": time_at(time_map, max(first, int(time_map["start_index"]))),
            "end_seconds": time_at(time_map, last)}


def validate_map(time_map):
    problems = []
    if time_map.get("anchor_seconds") is None:
        problems.append("no_absolute_anchor_relative_only")
    if time_map.get("clock_quality") not in ("calibrated", "uncalibrated", "unknown"):
        problems.append("invalid_clock_quality")
    if not time_map.get("recording_id"):
        problems.append("missing_recording_id")
    return problems
