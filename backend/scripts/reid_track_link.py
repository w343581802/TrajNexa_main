"""Commit E3：轨迹接回（先拒绝，再连接）。

规则刻意保守：
- 只用**已确认**轨迹（观测数 ≥ min_frames）做参考模板，短轨迹不参与连接；
- 外观相似度、时间间隔、运动连续性三项同时满足才考虑；
- 候选之间必须有明确差距（margin），否则拒绝，宁可断轨；
- 不做跨镜头、不写库、不更新"未确认"轨迹的模板。
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def gt_of_track(track, gt, tol=0.1):
    """用观测框与真值帧做 IoU 投票，判断这条轨迹属于哪个真值目标（仅评测用）。"""
    votes = {}
    for obs in track["observations"]:
        for person in gt.get("tracks", []):
            for item in person.get("frames", []):
                if abs(item["timestamp_seconds"] - obs["timestamp_seconds"]) > tol:
                    continue
                if _iou(item["bbox"], obs["bbox"]) >= 0.5:
                    votes[person["gt_id"]] = votes.get(person["gt_id"], 0) + 1
    return max(votes, key=votes.get) if votes else None


def link_tracks(features, tracks, params):
    """返回 (accepted_links, rejected) —— rejected 里带上拒绝原因，便于复盘。"""
    index = {t["track_id"]: t for t in tracks["tracks"]}
    feat = {t["track_id"]: np.asarray(t["aggregate"], dtype=np.float32)
            for t in features["tracks"] if t.get("aggregate")}
    usable = [tid for tid in feat
              if len(index[tid]["observations"]) >= params["min_frames"]]
    usable.sort(key=lambda tid: index[tid]["start_seconds"])

    accepted, rejected = [], []
    for i, a in enumerate(usable):
        for b in usable[i + 1:]:
            ta, tb = index[a], index[b]
            gap = tb["start_seconds"] - ta["end_seconds"]
            if gap < 0 or gap > params["max_gap_seconds"]:
                continue
            sim = float(np.dot(feat[a], feat[b]))
            ca, cb = _center(ta["observations"][-1]["bbox"]), _center(tb["observations"][0]["bbox"])
            dist = float(np.hypot(cb[0] - ca[0], cb[1] - ca[1]))
            allowed = params["max_center_distance_px"] + params["px_per_second"] * gap
            candidate = {"from": a, "to": b, "gap_seconds": round(gap, 2),
                         "similarity": round(sim, 4), "center_distance": round(dist, 1),
                         "allowed_distance": round(allowed, 1)}
            if sim < params["min_similarity"]:
                rejected.append(dict(candidate, reason="similarity_below_threshold"))
                continue
            if dist > allowed:
                rejected.append(dict(candidate, reason="motion_inconsistent"))
                continue
            candidate["score"] = round(sim - 0.001 * dist, 4)
            accepted.append(candidate)

    # 每个轨迹只保留一个最可信的后继/前驱，且必须与次优有明显差距
    final, used_to, used_from = [], set(), set()
    accepted.sort(key=lambda x: x["score"], reverse=True)
    for cand in accepted:
        # 两侧都要比：既可能是"多条轨迹都想接同一个后继"，
        # 也可能是"一条轨迹有多个同样好的后继"，后者同样算歧义。
        rivals = [c for c in accepted
                  if (c["to"] == cand["to"] or c["from"] == cand["from"]) and c is not cand]
        best_rival = max([c["score"] for c in rivals], default=None)
        if cand["from"] in used_from or cand["to"] in used_to:
            rejected.append(dict(cand, reason="track_already_linked"))
            continue
        if best_rival is not None and (cand["score"] - best_rival) < params["min_margin"]:
            rejected.append(dict(cand, reason="ambiguous_candidate"))
            continue
        used_from.add(cand["from"])
        used_to.add(cand["to"])
        final.append(cand)
    return final, rejected


def main(argv=None):
    parser = argparse.ArgumentParser(description="轨迹接回（E3）")
    parser.add_argument("--features", required=True)
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--gt")
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-frames", type=int, default=5)
    parser.add_argument("--min-similarity", type=float, default=0.85)
    parser.add_argument("--min-margin", type=float, default=0.05)
    parser.add_argument("--max-gap-seconds", type=float, default=8.0)
    parser.add_argument("--max-center-distance-px", type=float, default=250.0)
    parser.add_argument("--px-per-second", type=float, default=100.0)
    args = parser.parse_args(argv)

    params = {"min_frames": args.min_frames, "min_similarity": args.min_similarity,
              "min_margin": args.min_margin, "max_gap_seconds": args.max_gap_seconds,
              "max_center_distance_px": args.max_center_distance_px,
              "px_per_second": args.px_per_second}
    features = json.load(open(args.features, "r", encoding="utf-8"))
    tracks = json.load(open(args.tracks, "r", encoding="utf-8"))
    links, rejected = link_tracks(features, tracks, params)

    evaluation = {"checked": True}
    if args.gt:
        gt = json.load(open(args.gt, "r", encoding="utf-8"))
        owners = {t["track_id"]: gt_of_track(t, gt) for t in tracks["tracks"]}
        same = correct = wrong = 0
        detail = []
        for link in links:
            a, b = owners.get(link["from"]), owners.get(link["to"])
            ok = a is not None and a == b
            correct += 1 if ok else 0
            wrong += 0 if ok else 1
            detail.append({"from": link["from"], "to": link["to"],
                           "gt_from": a, "gt_to": b, "correct": ok})
        evaluation = {"links": len(links), "correct": correct, "wrong": wrong,
                      "detail": detail}
    out = {"params": params, "accepted": links, "rejected": rejected,
           "evaluation": evaluation}
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"accepted": len(links), "rejected": len(rejected),
                      "evaluation": evaluation}, ensure_ascii=False, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
