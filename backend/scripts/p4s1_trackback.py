"""M6-S4-1：单镜头「远→近」识别与向前回溯（S4-1A 归组 + S4-1B 证据状态）。

流程（不新增检测器/跟踪器）：
1. 读入已有 Tracklet 报告，可选读入归组结果（PhysicalObjectCandidate）；
2. 取 anchor 轨迹中满足质量门槛（h≥150 px、conf≥0.40、≥3 帧）的**近距段**做锚点；
3. 把"锚点之前"的所有轨迹段按**候选对象**（有归组就按组，没有就按单条轨迹）比较；
4. 输出候选排序、证据状态、绝对/相对时间与原始帧引用。

时间字段（design/45 §10.3 要求，不把相对时间冒充绝对时间）：
- `frame_id`：窗口内帧序号（0 起）；
- `video_relative_seconds`：从录像首帧起算的秒数（由帧序号与 5 fps 抽帧推算）；
- `source_pts_seconds`：**真实解码 PTS**，只有拿到 pts 映射时才填，否则为 null；
- `timestamp_source`：上面的时间是从哪来的（`frame_index_derived` / `decoded_pts`）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from app.m6_target_evidence import (  # noqa: E402
    DEFAULT_RANK_PARAMS,
    embed_observations,
    rank_tracklets,
    usable_observations,
)

MIN_CANDIDATE_HEIGHT = 40


def load_tracks(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "results" in data:
        engine = "bytetrack" if "bytetrack" in data["results"] else next(iter(data["results"]))
        return data["results"][engine]["tracks"]
    return data["tracks"]


def frame_reader(frames_dir):
    cache = {}

    def load(frame_id):
        key = int(frame_id)
        if key not in cache:
            cache[key] = cv2.imread(str(Path(frames_dir) / ("%04d.jpg" % (key + 1))))
        return cache[key]

    return load


def time_fields(obs, start_seconds, fps, pts_map):
    frame_id = int(obs["frame_id"])
    relative = round(start_seconds + frame_id / float(fps), 3)
    pts = None
    source = "frame_index_derived"
    if pts_map and str(frame_id) in pts_map:
        pts = float(pts_map[str(frame_id)])
        source = "decoded_pts"
    return {
        "frame_id": frame_id,
        "frame_ref": "%04d.jpg" % (frame_id + 1),
        "video_relative_seconds": relative,
        "source_pts_seconds": pts,
        "timestamp_source": source,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks-report", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--anchor-track", type=int, required=True)
    parser.add_argument("--groups", help="归组结果 JSON（p4s1_group_pairs.py --groups-out）")
    parser.add_argument("--pts-map", help="帧序号→真实 PTS 的 JSON；没有就保持 null")
    parser.add_argument("--min-height", type=float, default=150.0)
    parser.add_argument("--max-samples", type=int, default=5)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import onnxruntime as ort

    tracks = load_tracks(args.tracks_report)
    by_id = {t["track_id"]: t for t in tracks}
    anchor = by_id.get(args.anchor_track)
    if anchor is None:
        raise SystemExit("anchor track %s 不存在" % args.anchor_track)

    gate = {"min_height_px": args.min_height, "min_conf": 0.40, "min_frames": 3}
    anchor_obs = usable_observations(anchor, gate)
    if len(anchor_obs) < gate["min_frames"]:
        raise SystemExit("anchor 近距可用帧不足：%d" % len(anchor_obs))
    anchor_start = anchor_obs[0]["timestamp_seconds"]

    pts_map = json.loads(Path(args.pts_map).read_text(encoding="utf-8")) if args.pts_map else None
    loader = frame_reader(args.frames)
    session = ort.InferenceSession("/srv/models/reid/osnet_x0_25_msmt17_b1.onnx",
                                   providers=["CPUExecutionProvider"])
    anchor_vectors = embed_observations(anchor_obs, loader, session, args.max_samples)

    if args.groups:
        groups_json = json.loads(Path(args.groups).read_text(encoding="utf-8"))
        units = [[tid for tid in g if tid in by_id] for g in groups_json["groups"]]
    else:
        units = [[t["track_id"]] for t in tracks]

    def member_key(ids):
        return "PhysicalObjectCandidate_" + "-".join(str(i) for i in ids)

    vectors_by_unit, unit_meta = {}, {}
    for ids in units:
        collected = []
        for tid in ids:
            track = by_id[tid]
            earlier = [o for o in track["observations"]
                       if o["timestamp_seconds"] < anchor_start - 1e-6
                       and (o["bbox"][3] - o["bbox"][1]) >= MIN_CANDIDATE_HEIGHT]
            if len(earlier) < 3:
                continue
            collected.extend(embed_observations(earlier, loader, session, args.max_samples))
        if len(collected) < 2:
            continue
        key = member_key(ids)
        vectors_by_unit[key] = collected
        unit_meta[key] = {"track_ids": ids,
                          "first_seconds": round(min(o["timestamp_seconds"] for o, _ in collected), 2),
                          "last_seconds": round(max(o["timestamp_seconds"] for o, _ in collected), 2)}

    anchor_key = member_key([args.anchor_track])
    excluded = set()
    ranked = rank_tracklets(anchor_vectors, list(vectors_by_unit), vectors_by_unit)

    evidence = []
    for item in ranked[:8]:
        key = item["tracklet_id"]
        best_obs, best_cos = None, -1.0
        for obs, vec in vectors_by_unit[key]:
            for _, anchor_vec in anchor_vectors:
                cos = float(vec @ anchor_vec)
                if cos > best_cos:
                    best_obs, best_cos = obs, cos
        record = time_fields(best_obs, args.start_seconds, args.fps, pts_map)
        record.update({
            "candidate": key,
            "track_ids": unit_meta[key]["track_ids"],
            "cosine": round(best_cos, 4),
            "status": item["status"],
            "is_anchor_unit": key == anchor_key,
        })
        evidence.append(record)

    report = {
        "task": {"mode": "trajectory_backtrack", "frames_dir": args.frames,
                 "window_start_seconds": args.start_seconds, "fps": args.fps},
        "anchor": dict(time_fields(anchor_obs[0], args.start_seconds, args.fps, pts_map),
                       track_id=args.anchor_track, near_frames=len(anchor_obs),
                       quality_gate=gate),
        "candidates": [{
            "candidate": h["tracklet_id"],
            "rank": h["rank"],
            "track_ids": unit_meta[h["tracklet_id"]]["track_ids"],
            "time_span": [unit_meta[h["tracklet_id"]]["first_seconds"],
                          unit_meta[h["tracklet_id"]]["last_seconds"]],
            "cosine_max": round(h["cosine_max"], 4),
            "cosine_top_mean": round(h["cosine_top_mean"], 4),
            "cosine_median": round(h["cosine_median"], 4),
            "supporting_frames": h["supporting_frames"],
            "single_frame_share": round(h["single_frame_share"], 3),
            "margin_to_next": h["margin_to_next"],
            "samples": h["samples"],
            "status": h["status"],
        } for h in ranked],
        "evidence": evidence,
        "meta": {"rank_params": DEFAULT_RANK_PARAMS, "grouped": bool(args.groups)},
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("anchor track %d：近距 %d 帧，候选对象 %d 个" % (
        args.anchor_track, len(anchor_obs), len(ranked)))
    for h in ranked[:6]:
        span = unit_meta[h["tracklet_id"]]
        print("  #%d %-34s groups=%-22s top3=%.3f median=%.3f support=%d margin=%s → %s" % (
            h["rank"], h["tracklet_id"], str(span["track_ids"]),
            h["cosine_top_mean"], h["cosine_median"], h["supporting_frames"],
            h["margin_to_next"], h["status"]))


if __name__ == "__main__":
    main()
