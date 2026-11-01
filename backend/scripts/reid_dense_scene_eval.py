# -*- coding: utf-8 -*-
"""密集场景 ReID 对照: 同帧去重 + IoU 跟踪真值 + 拒识阈值扫描.

配套 reid_cross_frame_eval.py 产出的 gallery.json
(字段: frame / i / box / h / conf / crop / vec).

真值不靠人工标框: 用 IoU 跟踪从查询框向后/向前找到同一目标的逐帧框;
在跟踪不到且距目标出现窗口足够远的帧上, 该帧最高分即"目标不在"的地板.

两个容易搞错的统计口径, 这里分开记录:
- target_similarity: 目标本人那个框的相似度(阈值命中只能看它);
- best_similarity: 该帧所有候选里的最高分(和"目标是否存在"无关).
早期版本把 best_similarity 当成目标分数, 会系统性高估"目标保留率",
所以本文件里阈值表的"保留"一律用 target_similarity.

用法:
    python scripts/reid_dense_scene_eval.py --gallery /work/gfull/gallery.json \\
        --query t_0100#0 --absent-after 600

注意: 跟踪会在遮挡/离开画面处断开, 这里如实报告跟踪帧数, 不把它补成"全片";
静态目标的"缺席帧"通常是跟踪断开而非目标离场, 不能当噪声地板用.
"""
from __future__ import annotations

import argparse
import json

import numpy as np

FRAME_INTERVAL = 2.0
FRAME_SECONDS = lambda frame: (int(frame.split("_")[1]) - 1) * FRAME_INTERVAL  # noqa: E731


def load(gallery_path):
    with open(gallery_path, encoding="utf-8") as fh:
        rec = json.load(fh)
    by_frame = {}
    for row in rec:
        by_frame.setdefault(row["frame"], []).append(row)
    frames = sorted(by_frame, key=FRAME_SECONDS)
    return rec, by_frame, frames


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
    """同帧去重: 按置信度从高到低保留, 与线上 pool 逻辑一致.

    gallery 缺少 conf 字段时退化为保持原顺序(旧版脚本写出的文件),
    并在运行时打印一次提示, 不静默改变排序依据.
    """
    if cands and "conf" not in cands[0]:
        dedupe.missing_conf = True
        ordered = cands
    else:
        ordered = sorted(cands, key=lambda r: -r.get("conf", 0.0))
    kept = []
    for row in ordered:
        if all(iou(row["box"], k["box"]) < thr for k in kept):
            kept.append(row)
    return kept


dedupe.missing_conf = False


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


def evaluate(by_frame, frames, key, absent_after=None):
    query, path = track(by_frame, frames, key)
    qv = vec(query)
    rows, absent = [], []
    for frame in frames:
        ranked = sorted(
            ((float(np.dot(qv, vec(r))), r) for r in dedupe(by_frame[frame])),
            key=lambda x: -x[0],
        )
        best_sim = ranked[0][0]
        if frame in path:
            target = path[frame]
            match = next(
                ((i, s) for i, (s, r) in enumerate(ranked)
                 if iou(r["box"], target["box"]) >= 0.5),
                None,
            )
            if match is None:
                continue  # 目标被同帧去重掉了, 不计入排名
            rank, target_sim = match[0] + 1, match[1]
            rows.append((FRAME_SECONDS(frame), target_sim, best_sim, rank, target["h"]))
        else:
            if absent_after is not None and FRAME_SECONDS(frame) < absent_after:
                continue
            absent.append(best_sim)
    return query, rows, absent


def report(key, query, rows, absent, thresholds=(0.65, 0.70, 0.72, 0.75, 0.78, 0.80, 0.85)):
    n = len(rows)
    if not n:
        print(f"{key}: 跟踪不到第二帧或目标全被去重, 跳过")
        return
    top1 = sum(1 for row in rows if row[3] == 1)
    top5 = sum(1 for row in rows if row[3] <= 5)
    target_sims = np.array([row[1] for row in rows])
    best_sims = np.array([row[2] for row in rows])
    print(f"目标 {key} (h={query['h']}px): 跟踪 {n} 帧  "
          f"Top-1 {top1}/{n} ({top1 / n:.0%})  Top-5 {top5}/{n} ({top5 / n:.0%})")
    print(f"  目标自身分数 min={target_sims.min():.3f} "
          f"p10={np.percentile(target_sims, 10):.3f} 中位数={np.median(target_sims):.3f}")
    print(f"  该帧最高分 中位数={np.median(best_sims):.3f} "
          f"(仅用于判断该帧是否有高分候选, 不代表目标)")
    if not absent:
        print()
        return
    a = np.array(absent)
    print(f"  缺席帧 {len(a)}: 最高={a.max():.3f} p99={np.percentile(a, 99):.3f} "
          f"p90={np.percentile(a, 90):.3f}")
    print("  阈值   目标被接受(目标分≥阈值)   缺席误收(该帧最高分≥阈值)")
    for thr in thresholds:
        keep = sum(1 for s in target_sims if s >= thr)
        false_accept = sum(1 for s in a if s >= thr)
        print(f"  {thr:.2f}   {keep:>4}/{n}                {false_accept:>4}/{len(a)}")
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

    global FRAME_INTERVAL, FRAME_SECONDS
    FRAME_INTERVAL = args.interval
    FRAME_SECONDS = lambda frame: (int(frame.split("_")[1]) - 1) * FRAME_INTERVAL  # noqa: E731

    _rec, by_frame, frames = load(args.gallery)
    total = sum(len(v) for v in by_frame.values())
    print(f"gallery: {args.gallery}  裁剪 {total}  帧 {len(frames)}")
    for key in args.query:
        query, rows, absent = evaluate(by_frame, frames, key, absent_after=args.absent_after)
        report(key, query, rows, absent)
    if dedupe.missing_conf:
        print("提示: gallery 缺少 conf 字段, 同帧去重退化为按原顺序保留, "
              "结果与线上不一致; 请用 reid_cross_frame_eval.py 重新生成。")


if __name__ == "__main__":
    main()
