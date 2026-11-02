"""P1-B-1A：两轮车检测与独立 Tracklet 验证。

只回答问题"能不能稳定检测并单独跟踪两轮车"，不做人车关系、不改现有流程。
COCO 只有 bicycle / motorcycle 两个两轮类，**电动车没有独立类别**，
所以类别名只作为线索，不能当作"这是电动车"的证据。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.m6_track import ByteTrackAdapter, observations_from_detections  # noqa: E402

VEHICLE_LABELS = ("自行车", "摩托车")


def _percentiles(values, points=(50, 90, 99)):
    if not values:
        return {}
    ordered = sorted(values)
    out = {}
    for p in points:
        idx = min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1))))
        out["p%d" % p] = round(ordered[idx], 4)
    return out


def _containment(small, big):
    x1, y1 = max(small[0], big[0]), max(small[1], big[1])
    x2, y2 = min(small[2], big[2]), min(small[3], big[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    area = (small[2] - small[0]) * (small[3] - small[1])
    return ((x2 - x1) * (y2 - y1)) / area if area > 0 else 0.0


def _majority_label(track):
    counts = {}
    for o in track.observations:
        counts[o.label] = counts.get(o.label, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def main(argv=None):
    parser = argparse.ArgumentParser(description="两轮车检测与轨迹验证（P1-B-1A）")
    parser.add_argument("--detections", required=True)
    parser.add_argument("--sample-interval", type=float, default=0.2)
    parser.add_argument("--min-detection-conf", type=float, default=0.15)
    parser.add_argument("--max-gap-seconds", type=float, default=1.5)
    parser.add_argument("--max-missing-frames", type=int, default=8)
    parser.add_argument("--high-conf", type=float, default=0.25)
    parser.add_argument("--activation", type=float, default=0.20)
    parser.add_argument("--classes", default=",".join(VEHICLE_LABELS))
    parser.add_argument("--dedupe-iou", type=float, default=0.5,
                        help="同帧跨类去重阈值：D-FINE 常把同一辆车同时标成自行车与摩托车")
    parser.add_argument("--reject-contained-in-person", type=float, default=1.5,
                        help="车框完全落在人体框内、且人体高度超过车高 N 倍时判为误检（行人）")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    wanted = tuple(x.strip() for x in args.classes.split(",") if x.strip())
    data = json.load(open(args.detections, "r", encoding="utf-8"))
    frames = sorted(data["frames"], key=lambda f: f["timestamp_seconds"])

    adapter = ByteTrackAdapter(sample_interval_seconds=args.sample_interval,
                               params={"max_gap_seconds": args.max_gap_seconds,
                                       "max_missing_frames": args.max_missing_frames,
                                       "high_conf_threshold": args.high_conf,
                                       "activation_threshold": args.activation})
    per_class = {}
    frame_counts = []
    deduped = 0
    rejected_person = 0
    for frame in frames:
        persons = [d["bbox"] for d in frame["detections"] if d.get("label") == "人"]
        dets = [d for d in frame["detections"]
                if d.get("label") in wanted and float(d.get("confidence", 0)) >= args.min_detection_conf]
        if args.reject_contained_in_person > 0:
            kept_person = []
            for d in dets:
                h = d["bbox"][3] - d["bbox"][1]
                inside = False
                for pbox in persons:
                    if _containment(d["bbox"], pbox) > 0.8 and (pbox[3] - pbox[1]) > args.reject_contained_in_person * max(1.0, h):
                        inside = True
                        break
                if inside:
                    rejected_person += 1
                    continue
                kept_person.append(d)
            dets = kept_person
        if args.dedupe_iou > 0:
            kept = []
            for d in sorted(dets, key=lambda x: float(x.get("confidence", 0)), reverse=True):
                if any(_iou(d["bbox"], k["bbox"]) >= args.dedupe_iou for k in kept):
                    continue
                kept.append(d)
            deduped += len(dets) - len(kept)
            dets = kept
        for d in dets:
            per_class.setdefault(d["label"], []).append(float(d["confidence"]))
        frame_counts.append(len(dets))
        adapter.update(observations_from_detections(frame["frame_id"], frame["timestamp_seconds"], dets))

    tracks = [t for t in adapter._tracks.values() if t.observations]  # noqa: SLF001
    durations = [t.last_seen_seconds - t.first_seen_seconds for t in tracks]
    expected = []
    continuity = []
    for t in tracks:
        span = t.last_seen_seconds - t.first_seen_seconds
        slots = max(1, int(round(span / args.sample_interval)) + 1)
        continuity.append(len(t.observations) / slots)
        expected.append(span)

    # 同一帧里两个 id 的重叠框 = 重复检测；相邻帧最佳匹配却换 id = 交接嫌疑。
    # 每个当前框只记一次，避免两两配对把数字放大。
    by_frame = {}
    for t in tracks:
        for o in t.observations:
            by_frame.setdefault(o.frame_id, []).append((t.track_id, o.bbox))
    order = [f["frame_id"] for f in frames]
    duplicates = 0
    for fid in order:
        cur = by_frame.get(fid, [])
        for i in range(len(cur)):
            for j in range(i + 1, len(cur)):
                if cur[i][0] != cur[j][0] and _iou(cur[i][1], cur[j][1]) > 0.5:
                    duplicates += 1
    # 真正的交接：上一帧的轨迹到上一帧为止结束，下一帧由一个**新** id 接住同一个框。
    last_frame = {}
    for t in tracks:
        for o in t.observations:
            last_frame[t.track_id] = max(last_frame.get(t.track_id, -1), order.index(o.frame_id))
    handovers = 0
    for idx in range(1, len(order)):
        cur = by_frame.get(order[idx], [])
        prev = by_frame.get(order[idx - 1], [])
        for tid, box in cur:
            best, best_iou = None, 0.0
            for ptid, pbox in prev:
                value = _iou(box, pbox)
                if value > best_iou:
                    best, best_iou = ptid, value
            if best is None or best_iou <= 0.6 or best == tid:
                continue
            if last_frame.get(best) == idx - 1:      # 旧轨迹在此中断，新轨迹立刻接住
                handovers += 1

    summary = {
        "frames": len(frames),
        "sample_interval_seconds": args.sample_interval,
        "min_detection_confidence": args.min_detection_conf,
        "classes": list(wanted),
        "detections_per_class": {k: len(v) for k, v in per_class.items()},
        "confidence": {k: _percentiles(v) for k, v in per_class.items()},
        "frames_with_vehicle": sum(1 for c in frame_counts if c > 0),
        "vehicle_tracks": len(tracks),
        "track_duration_seconds": _percentiles(durations),
        "track_observations": _percentiles([len(t.observations) for t in tracks]),
        "continuity": _percentiles(continuity),
        "dedupe_iou": args.dedupe_iou,
        "dedupe_dropped": deduped,
        "rejected_pedestrian_like": rejected_person,
        "same_frame_duplicate_pairs": duplicates,
        "handover_events": handovers,
        "label_caveat": "COCO 只有 bicycle/motorcycle，电动车无独立类别，不能凭标签判定车型",
    }
    out = {"summary": summary,
           "tracks": [{"track_id": t.track_id,
                       "start_seconds": round(t.first_seen_seconds, 2),
                       "end_seconds": round(t.last_seen_seconds, 2),
                       "observations": len(t.observations),
                       "label": _majority_label(t),
                       "frames": [{"frame_id": o.frame_id,
                                   "timestamp_seconds": round(o.timestamp_seconds, 3),
                                   "bbox": [round(float(v), 2) for v in o.bbox],
                                   "confidence": round(float(o.confidence), 4)}
                                  for o in t.observations]}
                      for t in tracks]}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
