# -*- coding: utf-8 -*-
"""密集场景 ReID 对照: 同帧去重 + IoU 跟踪真值 + 拒识阈值扫描.

配套 reid_cross_frame_eval.py 产出的 gallery.json (字段: frame/i/box/h/conf/crop/vec).
真值不靠人工标框: 用 IoU 跟踪从查询框向后/向前找到同一目标的逐帧框;
在跟踪不到且距目标出现窗口足够远的帧上, 该帧最高分即"目标不在"的地板.

用法:
    python scripts/reid_dense_scene_eval.py --gallery /work/gfull/gallery.json \
        --query t_0100#0 --absent-after 600 --sheet /work/sheet.jpg

注意: 跟踪会在遮挡/离开画面处断开, 这里如实报告跟踪帧数, 不把它补成"全片".
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np


def load(gallery_path):
    with open(gallery_path, encoding="utf-8") as fh:
        rec = json.load(fh)
    by_frame = {}
    for row in rec:
        by_frame.setdefault(row["frame"], []).append(row)
    frames = sorted(by_frame, key=frame_seconds)
    return rec, by_frame, frames


def frame_seconds(frame, interval=2.0):
    """t_0001 视为 interval 秒, 与 0.5 fps 抽帧对应."""
    return (int(frame.split("_")[1]) - 1) * interval


def vec(row):
    return np.array(row["vec"], np.float32)


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def dedupe(cands, thr=0.5):
    kept = []
    for row in sorted(cands, key=lambda r: -r["conf"]):
        if all(iou(row["box"], k["box"]) < thr for k in kept):
            kept.append(row)
    return kept


def track(by_frame, frames, key, thr=0.25):
    qframe, qidx = key.split("#")
    query = next(r for r in by_frame[qframe] if r["i"] == int(qidx))
    start = frames.index(qframe)
    path = {qframe: query}
    for step in (1, -1):
        cur, i = query, start
        while True:
            i += step
            if i < 0 or i >= len(frames):
                break
            frame = frames[i]
            best, best_iou = None, 0.0
            for row in by_frame[frame]:
                score = iou(cur["box"], row["box"])
                if score > best_iou:
                    best, best_iou = row, score
            if best is None or best_iou < thr:
                break
            path[frame] = best
            cur = best
    return query, path


def evaluate(by_frame, frames, key, absent_after=None, thresholds=(0.65, 0.70, 0.72, 0.75, 0.78, 0.80, 0.85, 0.90)):
    query, path = track(by_frame, frames, key)
    qv = vec(query)
    rows, absent = [], []
    for frame in frames:
        scored = sorted(
            ((float(np.dot(qv, vec(r))), r) for r in dedupe(by_frame[frame])),
            key=lambda x: -x[0],
        )
        if frame in path:
            target = path[frame]
            rank = next(
                (i + 1 for i, (_s, r) in enumerate(scored) if iou(r["box"], target["box"]) >= 0.5),
                None,
            )
            rows.append((frame_seconds(frame), scored[0][0], rank, target["h"]))
        else:
            if absent_after is not None and frame_seconds(frame) < absent_after:
                continue
            absent.append(scored[0][0])
    return query, rows, absent, thresholds


def report(key, query, rows, absent, thresholds):
    n = len(rows)
    if not n:
        print(f"{key}: 跟踪不到第二帧, 跳过")
        return
    top1 = sum(1 for _s, _b, rank, _h in rows if rank == 1)
    top5 = sum(1 for _s, _b, rank, _h in rows if rank and rank <= 5)
    sims = np.array([b for _s, b, _r, _h in rows])
    print(f"目标 {key} (h={query['h']}px): 跟踪 {n} 帧  "
          f"Top-1 {top1}/{n} ({top1 / n:.0%})  Top-5 {top5}/{n} ({top5 / n:.0%})")
    print(f"  自身分数 min={sims.min():.3f} p10={np.percentile(sims, 10):.3f} "
          f"中位数={np.median(sims):.3f}")
    if not absent:
        print()
        return
    a = np.array(absent)
    print(f"  缺席帧 {len(a)}: 最高={a.max():.3f} p99={np.percentile(a, 99):.3f} "
          f"p90={np.percentile(a, 90):.3f}")
    print("  阈值   目标保留    缺席误收")
    for thr in thresholds:
        keep = sum(1 for s in sims if s >= thr)
        false_accept = sum(1 for s in a if s >= thr)
        print(f"  {thr:.2f}   {keep:>4}/{n}    {false_accept:>4}/{len(a)}")
    print()


def main():
    ap = argparse.ArgumentParser(description="密集场景 ReID 对照与拒识阈值扫描")
    ap.add_argument("--gallery", required=True, help="gallery.json 路径")
    ap.add_argument("--query", action="append", required=True,
                    help="查询裁剪, 形如 t_0100#0, 可重复")
    ap.add_argument("--absent-after", type=float, default=None,
                    help="只把该秒数之后的帧计入缺席集; 不给则所有非跟踪帧都算")
    ap.add_argument("--interval", type=float, default=2.0, help="抽帧间隔秒数")
    args = ap.parse_args()

    global frame_seconds
    frame_seconds = lambda f: (int(f.split("_")[1]) - 1) * args.interval  # noqa: E731

    _rec, by_frame, frames = load(args.gallery)
    print(f"gallery: {args.gallery}  裁剪 {sum(len(v) for v in by_frame.values())}  "
          f"帧 {len(frames)}")
    for key in args.query:
        query, rows, absent, thresholds = evaluate(
            by_frame, frames, key, absent_after=args.absent_after
        )
        report(key, query, rows, absent, thresholds)


if __name__ == "__main__":
    main()
