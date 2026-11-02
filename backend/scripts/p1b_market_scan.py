"""P1-B-1C：整段录像的人车关系扫描（流水线解码，不落帧）。

用途：在一整段 30 分钟录像里定位"骑乘 → 停车 → 下车"的片段。
做法：按固定帧率抽帧 → D-FINE 检测 → 人员/车辆各自 ByteTrack → 时空规则生成关系。
只做候选生成，不写库、不更新特征。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import detectors  # noqa: E402
from app.m6_person_vehicle_relation import DEFAULT_PARAMS, build_relations  # noqa: E402
from app.m6_track import ByteTrackAdapter, observations_from_detections  # noqa: E402

VEHICLE_LABELS = ("自行车", "摩托车")


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _dedupe(dets, threshold):
    kept = []
    for det in sorted(dets, key=lambda d: float(d.get("score", d.get("confidence", 0))), reverse=True):
        if any(_iou(det["box"], k["box"]) >= threshold for k in kept):
            continue
        kept.append(det)
    return kept


def main(argv=None):
    parser = argparse.ArgumentParser(description="整段录像人车关系扫描（P1-B-1C）")
    parser.add_argument("--video", required=True)
    parser.add_argument("--start", type=float, default=0.0, help="起始秒")
    parser.add_argument("--duration", type=float, default=0.0, help="处理秒数，0 表示到结尾")
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--min-confidence", type=float, default=0.20)
    parser.add_argument("--sample-interval", type=float, default=0.2)
    parser.add_argument("--max-gap-seconds", type=float, default=4.0)
    parser.add_argument("--max-missing-frames", type=int, default=20)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)

    capture = cv2.VideoCapture(args.video)
    if not capture.isOpened():
        raise SystemExit("无法打开视频: %s" % args.video)

    source_fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, int(round(source_fps / args.fps)))
    adapter_p = ByteTrackAdapter(sample_interval_seconds=args.sample_interval,
                                 params={"max_gap_seconds": args.max_gap_seconds,
                                         "max_missing_frames": args.max_missing_frames})
    adapter_v = ByteTrackAdapter(sample_interval_seconds=args.sample_interval,
                                 params={"max_gap_seconds": args.max_gap_seconds,
                                         "max_missing_frames": args.max_missing_frames})

    index = -1
    sampled = 0
    started = time.time()
    end_frame = None
    if args.duration > 0:
        end_frame = int((args.start + args.duration) * source_fps)
    start_frame = int(args.start * source_fps)

    while True:
        ok, frame = capture.read()
        if not ok:
            break
        index += 1
        if index < start_frame:
            continue
        if end_frame is not None and index > end_frame:
            break
        if (index - start_frame) % step != 0:
            continue

        height, width = frame.shape[:2]
        if width != args.width:
            frame = cv2.resize(frame, (args.width, int(height * args.width / width)))
        timestamp = round(args.start + sampled / args.fps, 3)
        frame_id = "f%06d" % sampled
        sampled += 1

        result = detectors.detect_flat(frame, ["dfine-n"], args.min_confidence)
        candidates = result["candidates"]
        persons = [c for c in candidates if c["label"] == "人"]
        vehicles = _dedupe([c for c in candidates if c["label"] in VEHICLE_LABELS], 0.5)

        if persons:
            adapter_p.update(observations_from_detections(frame_id, timestamp, persons))
        if vehicles:
            adapter_v.update(observations_from_detections(frame_id, timestamp, vehicles))
        if sampled % 250 == 0:
            print("frame %d t=%.1fs persons=%d vehicles=%d %.1fs"
                  % (sampled, timestamp, len(persons), len(vehicles), time.time() - started), flush=True)

    capture.release()

    def dump(adapter):
        out = []
        for track in adapter._tracks.values():  # noqa: SLF001
            if not track.observations:
                continue
            out.append({"track_id": track.track_id,
                        "start_seconds": round(track.first_seen_seconds, 2),
                        "end_seconds": round(track.last_seen_seconds, 2),
                        "observations": len(track.observations),
                        "frames": [{"frame_id": o.frame_id,
                                    "timestamp_seconds": round(o.timestamp_seconds, 3),
                                    "bbox": [round(float(v), 2) for v in o.bbox],
                                    "confidence": round(float(o.confidence), 4)}
                                   for o in track.observations]})
        return out

    persons = dump(adapter_p)
    vehicles = dump(adapter_v)
    result = build_relations(persons, vehicles, DEFAULT_PARAMS)

    os.makedirs(args.out_dir, exist_ok=True)
    json.dump({"video": args.video, "fps": args.fps, "start": args.start, "sampled_frames": sampled,
               "person_tracks": persons, "vehicle_tracks": vehicles},
              open(os.path.join(args.out_dir, "tracks.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump({"params": DEFAULT_PARAMS, "candidates": result["candidates"]},
              open(os.path.join(args.out_dir, "relation_candidates.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"events": result["events"]},
              open(os.path.join(args.out_dir, "relation_events.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"errors": result["errors"]},
              open(os.path.join(args.out_dir, "relation_errors.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    kinds = {}
    for item in result["candidates"]:
        kinds[item["relation"]] = kinds.get(item["relation"], 0) + 1
    print(json.dumps({"sampled_frames": sampled, "person_tracks": len(persons),
                      "vehicle_tracks": len(vehicles), "by_relation": kinds,
                      "seconds": round(time.time() - started, 1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
