"""M6-S4-1 离线验证：单镜头「远→近」的中近距离识别与向前回溯。

用法：
    python scripts/p4s1_trackback.py --detections dets.json --frames <帧目录> \
        --start-seconds 1360 --fps 5 --anchor-track 41 --out report.json

流程（不新增检测器/跟踪器）：
1. 同一份 D-FINE 检测 + ByteTrack，得到 Tracklet；
2. 取 anchor 轨迹中满足质量门槛（h≥150、conf≥0.40、≥3 帧）的**近距段**做锚点；
3. 把所有轨迹在锚点之前的部分切成"远距候选段"，逐帧 OSNet 向量与锚点两两比较；
4. 输出候选排序、证据帧（含**绝对秒**）、以及"锚点自己更早的那段"排在第几。

第 4 条是本阶段最有意义的自检：如果近距识别有效，同一个人更早的远距画面
应当排在候选首位；被相似人员挤下去就说明外观比较还不足以支撑回溯。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from app.m6_target_evidence import (  # noqa: E402
    DEFAULT_QUALITY_GATE,
    embed_observations,
    rank_tracklets,
    usable_observations,
)
REID_MODEL = "/srv/models/reid/osnet_x0_25_msmt17_b1.onnx"


def frame_loader(frames_dir):
    cache = {}

    def load(frame_id):
        if frame_id not in cache:
            path = Path(frames_dir) / ("%04d.jpg" % (frame_id + 1))
            image = cv2.imread(str(path)) if path.exists() else None
            cache[frame_id] = image
        return cache[frame_id]

    return load


def absolute_seconds(obs, start_seconds, fps):
    return round(start_seconds + int(obs["frame_id"]) / float(fps), 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks-report", required=True,
                        help="p1s_track_compare 输出的报告；本阶段只在既有 Tracklet 上做比较，不重跑跟踪")
    parser.add_argument("--frames", required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--anchor-track", type=int, required=True)
    parser.add_argument("--min-height", type=float, default=DEFAULT_QUALITY_GATE["min_height_px"])
    parser.add_argument("--max-samples", type=int, default=5)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import onnxruntime as ort

    report_data = json.loads(Path(args.tracks_report).read_text(encoding="utf-8"))
    if "results" in report_data:
        engine = "bytetrack" if "bytetrack" in report_data["results"] else next(iter(report_data["results"]))
        tracks = report_data["results"][engine]["tracks"]
    else:
        tracks = report_data["tracks"]

    anchor = next((t for t in tracks if t["track_id"] == args.anchor_track), None)
    if anchor is None:
        raise SystemExit("anchor track %s 不存在" % args.anchor_track)

    gate = {"min_height_px": args.min_height, "min_conf": 0.40, "min_frames": 3}
    anchor_obs = usable_observations(anchor, gate)
    if len(anchor_obs) < gate["min_frames"]:
        raise SystemExit("anchor 近距可用帧不足：%d" % len(anchor_obs))

    anchor_start = anchor_obs[0]["timestamp_seconds"]
    session = ort.InferenceSession(REID_MODEL, providers=["CPUExecutionProvider"])
    loader = frame_loader(args.frames)
    anchor_vectors = embed_observations(anchor_obs, loader, session, args.max_samples)

    tracklets, vectors_by_tracklet, spans = {}, {}, {}
    for track in tracks:
        earlier = [o for o in track["observations"] if o["timestamp_seconds"] < anchor_start - 1e-6]
        earlier = [o for o in earlier if (o["bbox"][3] - o["bbox"][1]) >= 40]
        if len(earlier) < 3:
            continue
        key = "track_%d_early" % track["track_id"]
        vectors = embed_observations(earlier, loader, session, args.max_samples)
        if len(vectors) < 2:
            continue
        tracklets[key] = track["track_id"]
        vectors_by_tracklet[key] = vectors
        spans[key] = (earlier[0]["timestamp_seconds"], earlier[-1]["timestamp_seconds"])

    ranked = rank_tracklets(anchor_vectors, tracklets, vectors_by_tracklet)

    evidence = []
    positive_key = "track_%d_early" % args.anchor_track
    positive_rank = None
    for item in ranked[:5]:
        key = item["tracklet_id"]
        best = max(
            ((obs, float(vec @ anchor_vec)) for obs, vec in vectors_by_tracklet[key]
             for _, anchor_vec in anchor_vectors),
            key=lambda pair: pair[1],
        )
        obs, cosine = best
        evidence.append({
            "tracklet_id": key,
            "frame_ref": "%04d.jpg" % (int(obs["frame_id"]) + 1),
            "absolute_seconds": absolute_seconds(obs, args.start_seconds, args.fps),
            "bbox": [round(v, 1) for v in obs["bbox"]],
            "cosine": round(cosine, 4),
            "status": item["status"],
        })
        if key == positive_key:
            positive_rank = item["rank"]

    report = {
        "task": {"mode": "trajectory_backtrack", "camera": args.frames,
                 "start_seconds": args.start_seconds, "fps": args.fps},
        "anchor": {"track_id": args.anchor_track, "near_frames": len(anchor_obs),
                   "near_start": absolute_seconds(anchor_obs[0], args.start_seconds, args.fps),
                   "near_end": absolute_seconds(anchor_obs[-1], args.start_seconds, args.fps),
                   "quality_gate": gate},
        "tracklets": [{"tracklet_id": k, "track_id": tracklets[k],
                       "start": round(spans[k][0], 2), "end": round(spans[k][1], 2),
                       "samples": len(vectors_by_tracklet[k])} for k in tracklets],
        "hypotheses": [{"tracklet_id": h["tracklet_id"], "rank": h["rank"],
                        "cosine_max": round(h["cosine_max"], 4),
                        "cosine_top_mean": round(h["cosine_top_mean"], 4),
                        "status": h["status"]} for h in ranked],
        "evidence": evidence,
        "backtrack_self_rank": positive_rank,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("anchor track %d 近距 %d 帧 %.2f-%.2fs，候选 %d 段" % (
        args.anchor_track, len(anchor_obs), report["anchor"]["near_start"],
        report["anchor"]["near_end"], len(ranked)))
    for h in ranked[:6]:
        mark = " <== 同一目标更早段" if h["tracklet_id"] == positive_key else ""
        print("  #%d %-16s cos_max=%.3f top3=%.3f%s" % (
            h["rank"], h["tracklet_id"], h["cosine_max"], h["cosine_top_mean"], mark))
    print("回溯自检：锚点更早段排名 = %s" % positive_rank)


if __name__ == "__main__":
    main()
