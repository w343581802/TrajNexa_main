"""S6-15M：窗口分析薄适配（复用现有 D-FINE + ByteTrack，不复制算法）。

修正要点（对应 design/47 §2 P0-B）：
  1. 每个成功解码的帧都推进跟踪器，包括没有目标的空帧；
  2. 缺少 OSD 锚点时明确返回 time_status="relative_only"，不做跨镜头时间比较；
  3. 成本分列：requested / read / decoded / failed / detector_frames / wall_time；
  4. Tracklet 编号带窗口前缀，避免把每段的 track_id=37 当成同一条轨迹；
  5. 只用公开的 tracks_json()，不读私有 _tracks。
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2

from app import detectors, model_capabilities
from app.m6_track import ByteTrackAdapter, observations_from_detections


def analyze_window(frames_dir, start_index=0, end_index=None, fps=5.0,
                   osd_anchor_seconds=None, min_confidence=0.25,
                   max_gap_seconds=4.0, max_missing_frames=20, camera="unknown",
                   window_id="w0", detector=None, task=None):
    """分析一段已抽取的帧窗口，返回 Tracklet、时间状态与实测成本。"""
    started = time.time()
    # 模型切换只在窗口边界生效：整窗固定一个检测器。
    detector_id = detector or model_capabilities.detector_id(task)
    detectors_used = set()
    cost = {"frames_requested": 0, "frames_read": 0, "frames_decoded": 0,
            "frames_with_person": 0, "frames_failed_decode": 0, "detector_frames": 0,
            "reid_calls": 0, "vlm_calls": 0, "wall_time_ms": 0}
    root = Path(frames_dir)
    if not root.is_dir():
        return {"status": "no_frames", "tracks": [], "camera": camera, "window_id": window_id,
                "reason": "frames_dir_missing", "cost": cost}
    files = sorted(root.glob("*.jpg"))
    if not files:
        return {"status": "no_frames", "tracks": [], "camera": camera, "window_id": window_id,
                "reason": "frames_empty", "cost": cost}
    end_index = len(files) - 1 if end_index is None else min(end_index, len(files) - 1)
    window = files[start_index:end_index + 1]
    if not window:
        return {"status": "no_frames", "tracks": [], "camera": camera, "window_id": window_id,
                "reason": "empty_window", "cost": cost}
    cost["frames_requested"] = len(window)

    adapter = ByteTrackAdapter(
        sample_interval_seconds=1.0 / fps,
        params={"max_gap_seconds": max_gap_seconds,
                "max_missing_frames": max_missing_frames})
    for offset, path in enumerate(window):
        frame = cv2.imread(str(path))
        if frame is None:
            cost["frames_failed_decode"] += 1
            continue
        cost["frames_read"] += 1
        cost["frames_decoded"] += 1
        timestamp = (start_index + offset) / fps
        if detector:
            result = {"candidates": detectors.detect_flat(frame, [detector], min_confidence)["candidates"]}
        else:
            # B7-B：走能力层，模型故障时按配置回退，实际执行的模型写进 executions()。
            result = model_capabilities.invoke(
                "object_detection", {"image": frame, "min_confidence": min_confidence},
                task=task, model=detector_id)
        detectors_used.add(result.get("model"))
        cost["detector_frames"] += 1
        persons = [c for c in result["candidates"] if c["label"] == "人"]
        if persons:
            cost["frames_with_person"] += 1
            adapter.update(observations_from_detections(
                str(start_index + offset), timestamp, persons))
        else:
            adapter.advance(timestamp)

    raw = adapter.tracks_json()["tracks"]
    tracks = []
    for track in raw:
        if not track.get("observations"):
            continue
        tracks.append({"track_id": "%s:%s" % (window_id, track["track_id"]),
                       "local_track_id": track["track_id"],
                       "state": track.get("state"),
                       "observations": [{"frame_id": int(o["frame_id"]),
                                         "timestamp_seconds": o["timestamp_seconds"],
                                         "bbox": o["bbox"]} for o in track["observations"]]})

    cost["wall_time_ms"] = int((time.time() - started) * 1000)
    status = "ok" if tracks else "detection_missed"
    absolute = osd_anchor_seconds is not None
    return {"status": status, "tracks": tracks, "camera": camera, "window_id": window_id,
            "frames_with_person": cost["frames_with_person"],
            "frame_index_range": [start_index, end_index], "fps": fps,
            "time_status": "absolute" if absolute else "relative_only",
            "cross_camera_comparable": absolute,
            "osd_anchor_seconds": osd_anchor_seconds,
            "continuation": [{"track_id": t["track_id"],
                              "last_timestamp_seconds": t["observations"][-1]["timestamp_seconds"]}
                             for t in tracks],
            "cost": cost,
            "uncertainties": [] if absolute else ["time_mapping_missing"]}
