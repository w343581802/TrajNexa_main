"""M6-S6-1：单摄像头轨迹生成（帧序列 → D-FINE → ByteTrack）。

输入是已经按固定帧率抽好的 JPEG 序列（用 ffmpeg 预抽取，避免 HEVC 快速 seek 抽到灰帧），
输出沿用 S5 的 tracks 报告结构，便于直接喂给 `app.m6_pipeline`：

    {"meta": {...}, "results": {"bytetrack": {"tracks": [...]}}}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from app import detectors  # noqa: E402
from app.m6_track import ByteTrackAdapter, observations_from_detections  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", required=True)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--camera", default="unknown")
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--max-gap-seconds", type=float, default=4.0)
    parser.add_argument("--max-missing-frames", type=int, default=20)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    files = sorted(Path(args.frames).glob("*.jpg"))
    if not files:
        raise SystemExit("没有帧：%s" % args.frames)

    adapter = ByteTrackAdapter(
        sample_interval_seconds=1.0 / args.fps,
        params={"max_gap_seconds": args.max_gap_seconds,
                "max_missing_frames": args.max_missing_frames})

    sampled = 0
    for index, path in enumerate(files):
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        result = detectors.detect_flat(frame, ["dfine-n"], args.min_confidence)
        persons = [c for c in result["candidates"] if c["label"] == "人"]
        if persons:
            adapter.update(observations_from_detections(str(index), index / args.fps, persons))
        sampled += 1
        if sampled % 200 == 0:
            print("frame %d/%d" % (sampled, len(files)), flush=True)

    tracks = []
    for track in adapter._tracks.values():  # noqa: SLF001
        if not track.observations:
            continue
        tracks.append({
            "track_id": track.track_id,
            "observations": [{"frame_id": o.frame_id,
                              "timestamp_seconds": round(float(o.timestamp_seconds), 3),
                              "bbox": [round(float(v), 2) for v in o.bbox],
                              "confidence": round(float(o.confidence), 4)}
                             for o in track.observations]})
    tracks.sort(key=lambda t: t["track_id"])

    payload = {"meta": {"camera": args.camera, "fps": args.fps,
                        "frames": len(files), "min_confidence": args.min_confidence,
                        "max_gap_seconds": args.max_gap_seconds,
                        "max_missing_frames": args.max_missing_frames},
               "results": {"bytetrack": {"tracks": tracks}}}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    lengths = [len(t["observations"]) for t in tracks]
    print("%s：%d 帧 → %d 条轨迹，长度中位 %d，最长 %d"
          % (args.camera, len(files), len(tracks),
             sorted(lengths)[len(lengths) // 2] if lengths else 0, max(lengths or [0])))


if __name__ == "__main__":
    main()
