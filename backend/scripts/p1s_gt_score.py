"""M6-S1-3：用人工真值给一组轨迹打分（覆盖 / ID 数 / 错误合并）。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _tracks_list(payload, engine="bytetrack"):
    if isinstance(payload, list):
        return payload
    if "results" in payload:
        return payload["results"][engine]["tracks"]
    if "tracks" in payload:
        return payload["tracks"]
    raise SystemExit("无法识别的轨迹文件结构")


def score(tracks, gt_tracks, window, coverage_iou=0.3, time_tol=0.15):
    by_frame = {}
    for track in tracks:
        for obs in track["observations"]:
            by_frame.setdefault(round(float(obs["timestamp_seconds"]), 3), []).append((track["track_id"], obs["bbox"]))
    rows = []
    for gt in gt_tracks:
        frames = [f for f in gt["frames"] if window[0] <= f["timestamp_seconds"] <= window[1]]
        if not frames:
            continue
        covering, hits = {}, 0
        for frame in frames:
            best = None
            for ts, items in by_frame.items():
                if abs(ts - frame["timestamp_seconds"]) > time_tol:
                    continue
                for track_id, bbox in items:
                    score_iou = _iou(bbox, frame["bbox"])
                    if score_iou >= coverage_iou:
                        best = track_id if best is None else best
                        covering[track_id] = covering.get(track_id, 0) + 1
                        hits += 1
                        break
                if best is not None:
                    break
        significant = [tid for tid, count in covering.items() if count >= max(2, 0.3 * len(frames))]
        rows.append({"gt_id": gt["gt_id"], "gt_frames": len(frames), "covered": hits,
                     "coverage": round(hits / len(frames), 2),
                     "ids": len(covering), "ids_significant": len(significant),
                     "track_ids": sorted(covering)})
    return rows


def cross_merges(tracks, gt_tracks, window, iou_threshold=0.3, time_tol=0.15):
    """同一 track 同时覆盖两个真值人物 = 错误合并。"""
    owners = {}
    for gt in gt_tracks:
        for frame in gt["frames"]:
            if not (window[0] <= frame["timestamp_seconds"] <= window[1]):
                continue
            for track in tracks:
                for obs in track["observations"]:
                    if abs(float(obs["timestamp_seconds"]) - frame["timestamp_seconds"]) > time_tol:
                        continue
                    if _iou(obs["bbox"], frame["bbox"]) >= iou_threshold:
                        owners.setdefault(track["track_id"], set()).add(gt["gt_id"])
    return {tid: sorted(gs) for tid, gs in owners.items() if len(gs) > 1}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--gt", required=True)
    parser.add_argument("--window", default="190,246")
    parser.add_argument("--label", default="")
    parser.add_argument("--engine", default="bytetrack")
    parser.add_argument("--gt-scale", type=float, default=1.0,
                        help="真值框所在坐标系到检测坐标系的缩放（1920→1280 传 0.6667）")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    window = tuple(float(v) for v in args.window.split(","))
    tracks = _tracks_list(json.loads(Path(args.tracks).read_text(encoding="utf-8")), args.engine)
    gt = json.loads(Path(args.gt).read_text(encoding="utf-8"))["tracks"]
    if args.gt_scale != 1.0:
        for track in gt:
            for frame in track["frames"]:
                frame["bbox"] = [round(v * args.gt_scale, 1) for v in frame["bbox"]]
    rows = score(tracks, gt, window)
    merges = cross_merges(tracks, gt, window)
    print("== %s ==" % (args.label or args.tracks))
    for row in rows:
        print("  %-11s frames=%-3d covered=%-3d cover=%.2f ids=%-2d ids_sig=%d %s" % (
            row["gt_id"], row["gt_frames"], row["covered"], row["coverage"],
            row["ids"], row["ids_significant"], row["track_ids"]))
    print("  cross_gt_merges:", merges)
    if args.out:
        Path(args.out).write_text(json.dumps({"rows": rows, "merges": merges}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
