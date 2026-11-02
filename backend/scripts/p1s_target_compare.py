"""M6-S1-3：按"真实目标"比较两个引擎的连续性与遮挡恢复。

做法：用基线（ByteTrack）里最长的一批轨迹当参考目标（人工可核验），
再看另一个引擎在这些目标上覆盖了多少帧、用几个 ID 覆盖、断了几次。
参考目标来自基线 ≠ 真值，所以必须配人工看图确认，不能单独当结论。
"""
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


def _index(track):
    return {o["frame_id"]: o for o in track["observations"]}


def compare(reference_tracks, other_tracks, min_frames, coverage_iou=0.3, share=0.3):
    rows = []
    for ref in reference_tracks:
        if len(ref["observations"]) < min_frames:
            continue
        ref_frames = _index(ref)
        covering = {}
        for track in other_tracks:
            hits = 0
            for frame_id, obs in ref_frames.items():
                mine = _index(track).get(frame_id)
                if mine is not None and _iou(mine["bbox"], obs["bbox"]) >= coverage_iou:
                    hits += 1
            if hits:
                covering[track["track_id"]] = hits
        total = len(ref_frames)
        main = max(covering.values()) if covering else 0
        significant = [tid for tid, hits in covering.items() if hits >= max(2, share * total)]
        rows.append({
            "reference_track": ref["track_id"],
            "reference_frames": total,
            "start_seconds": ref["start_seconds"],
            "end_seconds": ref["end_seconds"],
            "covered_frames": main,
            "coverage": round(main / total, 2),
            "ids_total": len(covering),
            "ids_significant": len(significant),
        })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="p1s_track_compare.py 的输出")
    parser.add_argument("--base", default="bytetrack")
    parser.add_argument("--other", default="botsort")
    parser.add_argument("--min-frames", type=int, default=20)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    base = report["results"][args.base]["tracks"]
    other = report["results"][args.other]["tracks"]
    rows = compare(base, other, args.min_frames)
    rows.sort(key=lambda r: -r["reference_frames"])
    for row in rows:
        print("ref#%-4d %5.1f-%5.1fs frames=%-3d coverage=%.2f ids_total=%-2d ids_significant=%d" % (
            row["reference_track"], row["start_seconds"], row["end_seconds"],
            row["reference_frames"], row["coverage"], row["ids_total"], row["ids_significant"]))
    mean_cov = sum(r["coverage"] for r in rows) / len(rows) if rows else 0.0
    mean_ids = sum(r["ids_significant"] for r in rows) / len(rows) if rows else 0.0
    print("targets=%d mean_coverage=%.2f mean_ids_significant=%.2f" % (len(rows), mean_cov, mean_ids))
    if args.out:
        Path(args.out).write_text(json.dumps({"rows": rows, "mean_coverage": mean_cov,
                                              "mean_ids": mean_ids}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
