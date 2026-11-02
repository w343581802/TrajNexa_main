"""M6-S1-1：同一份检测结果上比较 ByteTrack / BoT-SORT（可选 + 外观）。

只做一件事：把同一批 `TrackObservation` 分别喂给两个适配层，各自输出轨迹，
再算一组可比的指标。检测器、采样率、去重策略全部固定，差异只来自跟踪算法。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m6_botsort import BoTSortAdapter  # noqa: E402
from app.m6_track import ByteTrackAdapter, TrackObservation  # noqa: E402

PERSON_LABELS = ("人", "person")


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def load_observations(path, labels, min_conf):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    frames = []
    for frame in data["frames"]:
        obs = []
        for det in frame.get("detections", []):
            if det.get("label") not in labels:
                continue
            if float(det.get("score", det.get("confidence", 0.0))) < min_conf:
                continue
            obs.append(TrackObservation(
                frame_id=frame["frame_id"],
                timestamp_seconds=float(frame["timestamp_seconds"]),
                bbox=tuple(float(v) for v in det["box"]),
                confidence=float(det.get("score", det.get("confidence", 0.0))),
                label=det.get("label", ""),
            ))
        frames.append((frame["frame_id"], float(frame["timestamp_seconds"]), obs))
    return frames


def run_engine(engine, frames, params):
    if engine == "bytetrack":
        adapter = ByteTrackAdapter(sample_interval_seconds=params["interval"], params=params)
    else:
        adapter = BoTSortAdapter(sample_interval_seconds=params["interval"], params=params)
    started = time.perf_counter()
    for _, _, obs in frames:
        adapter.update(obs)
    elapsed = time.perf_counter() - started
    tracks = list(adapter.tracks_json()["tracks"])
    return tracks, elapsed, adapter.describe() if hasattr(adapter, "describe") else {}


def summarise(tracks, frames, gap_seconds):
    durations = [t["duration_seconds"] for t in tracks]
    by_id = {t["track_id"]: t for t in tracks}
    handovers = []
    for a in tracks:
        last = a["observations"][-1] if a["observations"] else None
        if last is None:
            continue
        for b in tracks:
            if b["track_id"] == a["track_id"] or not b["observations"]:
                continue
            first = b["observations"][0]
            if b["start_seconds"] < a["end_seconds"]:
                continue
            if b["start_seconds"] - a["end_seconds"] > gap_seconds:
                continue
            if _iou(last["bbox"], first["bbox"]) >= 0.3:
                handovers.append({"from": a["track_id"], "to": b["track_id"],
                                  "gap": round(b["start_seconds"] - a["end_seconds"], 2),
                                  "iou": round(_iou(last["bbox"], first["bbox"]), 3)})
    short = [t for t in tracks if t["duration_seconds"] < 1.0]
    return {
        "tracks": len(tracks),
        "median_duration_s": round(statistics.median(durations), 2) if durations else 0.0,
        "max_duration_s": round(max(durations), 2) if durations else 0.0,
        "fragments_lt_1s": len(short),
        "handover_suspects": len(handovers),
        "handover_detail": handovers[:10],
        "total_observations": sum(len(t["observations"]) for t in tracks),
        "by_id": len(by_id),
        "frames": len(frames),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--detections", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--min-conf", type=float, default=0.20)
    parser.add_argument("--labels", default="person")
    parser.add_argument("--max-gap-seconds", type=float, default=3.0)
    parser.add_argument("--max-missing-frames", type=int, default=15)
    parser.add_argument("--engine", default="both", choices=["bytetrack", "botsort", "both"])
    parser.add_argument("--iou-first", type=float, default=0.2)
    parser.add_argument("--iou-second", type=float, default=0.5)
    parser.add_argument("--iou-unconfirmed", type=float, default=0.3)
    args = parser.parse_args()

    labels = PERSON_LABELS if args.labels == "person" else tuple(args.labels.split(","))
    frames = load_observations(args.detections, labels, args.min_conf)
    params = {
        "interval": args.interval,
        "max_gap_seconds": args.max_gap_seconds,
        "max_missing_frames": args.max_missing_frames,
        "activation_threshold": 0.20,
        "high_conf_threshold": 0.25,
        "minimum_consecutive_frames": 1,
        "iou_first_assoc": args.iou_first,
        "iou_second_assoc": args.iou_second,
        "iou_unconfirmed_assoc": args.iou_unconfirmed,
    }
    engines = ["bytetrack", "botsort"] if args.engine == "both" else [args.engine]
    report = {"detections": args.detections, "frames": len(frames),
              "person_detections": sum(len(f[2]) for f in frames), "results": {}}
    for engine in engines:
        tracks, elapsed, described = run_engine(engine, frames, params)
        summary = summarise(tracks, frames, args.max_gap_seconds)
        summary["seconds"] = round(elapsed, 2)
        summary["ms_per_frame"] = round(1000.0 * elapsed / max(1, len(frames)), 2)
        report["results"][engine] = {"summary": summary, "config": described,
                                     "tracks": tracks}
        print("%-10s tracks=%-4d median=%.1fs frag=%-3d handover=%-3d %.1f ms/frame" % (
            engine, summary["tracks"], summary["median_duration_s"],
            summary["fragments_lt_1s"], summary["handover_suspects"],
            summary["ms_per_frame"]))
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
