"""S6-15M：窗口分析薄适配（复用现有 D-FINE + ByteTrack，不复制算法）。

调度器只通过这个入口取"实际观察到了什么"；失败状态必须区分：
  no_frames            帧目录为空/不存在
  time_mapping_missing 缺少 OSD 锚点，无法给出绝对时间
  detection_missed     解码成功但没有任何目标框（≠ 目标没经过）
  ok                   有 Tracklet
"""
from __future__ import annotations

from pathlib import Path

import cv2

from app import detectors
from app.m6_track import ByteTrackAdapter, observations_from_detections


class WindowAnalysisError(ValueError):
    """可对外返回的错误码。"""


def analyze_window(frames_dir, start_index=0, end_index=None, fps=5.0,
                   osd_anchor_seconds=None, min_confidence=0.25,
                   max_gap_seconds=4.0, max_missing_frames=20, camera="unknown"):
    """分析一段已抽取的帧窗口，返回 Tracklet 与执行状态。"""
    root = Path(frames_dir)
    if not root.is_dir():
        return {"status": "no_frames", "tracks": [], "frames_decoded": 0,
                "camera": camera, "reason": "frames_dir_missing"}
    files = sorted(root.glob("*.jpg"))
    if not files:
        return {"status": "no_frames", "tracks": [], "frames_decoded": 0,
                "camera": camera, "reason": "frames_empty"}
    end_index = len(files) - 1 if end_index is None else min(end_index, len(files) - 1)
    window = files[start_index:end_index + 1]
    if not window:
        return {"status": "no_frames", "tracks": [], "frames_decoded": 0,
                "camera": camera, "reason": "empty_window"}

    adapter = ByteTrackAdapter(
        sample_interval_seconds=1.0 / fps,
        params={"max_gap_seconds": max_gap_seconds,
                "max_missing_frames": max_missing_frames})
    decoded = 0
    with_person = 0
    for offset, path in enumerate(window):
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        result = detectors.detect_flat(frame, ["dfine-n"], min_confidence)
        persons = [c for c in result["candidates"] if c["label"] == "人"]
        decoded += 1
        if persons:
            with_person += 1
            frame_index = start_index + offset
            adapter.update(observations_from_detections(
                str(frame_index), frame_index / fps, persons))

    tracks = []
    for track in adapter._tracks.values():  # noqa: SLF001
        if not track.observations:
            continue
        tracks.append({"track_id": track.track_id,
                       "observations": [{"frame_id": int(o.frame_id),
                                         "timestamp_seconds": o.timestamp_seconds,
                                         "bbox": list(o.bbox)} for o in track.observations]})

    status = "ok" if tracks else "detection_missed"
    payload = {"status": status, "tracks": tracks, "camera": camera,
               "frames_decoded": decoded, "frames_with_person": with_person,
               "frame_index_range": [start_index, end_index],
               "fps": fps}
    if osd_anchor_seconds is None:
        payload["time_mapping"] = "missing"
        payload["uncertainties"] = ["time_mapping_missing"]
    else:
        payload["time_mapping"] = "osd_anchor"
        payload["osd_anchor_seconds"] = osd_anchor_seconds
    return payload
