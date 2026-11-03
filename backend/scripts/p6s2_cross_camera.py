"""M6-S6-2：跨摄像头比对（三态时间模型 + 重叠区间定向加密采样）。

与 S6 首轮脚本（p6s1_cross_camera.py）并存，便于 A/B/C 对照：

- A：旧时间规则 + 全局均匀采样（= p6s1 的输出）
- B：新时间规则 + 全局均匀采样（--mode b）
- C：新时间规则 + 重叠区间定向加密（--mode c）

关键差异：
1. 外观粗筛**不再套用单镜头防自评的时间间隔过滤**，也统一用校正后墙钟时间；
2. 时间关系拆成 `overlap_seconds` 与 `transition_gap_seconds`；
3. 视野重叠未确认 / 时钟未校准 → pending，不排除；
4. 重叠候选的加密采样按墙钟时间一对一配对，帧不重复使用。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from app.m6_cross_camera import (clock_record, cross_status, pair_by_time,  # noqa: E402
                                 summarise_pairs, temporal_relation,
                                 time_sufficiency, to_wall)
from app.m6_reid_link import _to_blob, crop_for_embedding  # noqa: E402
from app.m6_track_grouping import group_tracks  # noqa: E402

MODEL = "/srv/models/reid/osnet_x0_25_msmt17_b1.onnx"


def frame_reader(frames_dir):
    cache = {}

    def load(frame_id):
        key = int(frame_id)
        if key not in cache:
            cache[key] = cv2.imread(str(Path(frames_dir) / ("%04d.jpg" % (key + 1))))
        return cache[key]

    return load


def stratified(observations, k):
    if len(observations) <= k:
        return list(observations)
    ordered = sorted(observations, key=lambda o: float(o["timestamp_seconds"]))
    start = float(ordered[0]["timestamp_seconds"])
    span = max(float(ordered[-1]["timestamp_seconds"]) - start, 1e-6)
    return [min(ordered, key=lambda o: abs(float(o["timestamp_seconds"])
                                           - (start + span * (i + 0.5) / k)))
            for i in range(k)]


def embed_crops(session, items, loader):
    """items: [(obs, wall_time)] → [(wall_time, vector)]，按原分辨率裁剪。"""
    input_name = session.get_inputs()[0].name
    out = []
    for obs, wall in items:
        image = loader(obs["frame_id"])
        if image is None:
            continue
        crop = crop_for_embedding(image, obs["bbox"])
        if crop is None:
            continue
        vector = session.run(None, {input_name: _to_blob(crop)})[0][0]
        out.append((wall, vector / (np.linalg.norm(vector) + 1e-9), obs))
    return out


def appearance_stats(anchor_vectors, candidate_vectors, weak_cosine=0.60):
    """粗筛：每个候选帧对锚点帧取最大余弦；不施加时间间隔过滤。"""
    if not anchor_vectors or not candidate_vectors:
        return {"score": 0.0, "supporting_candidate_frames": 0, "frames": 0}
    matrix = np.asarray([v for _, v in candidate_vectors])
    anchors = np.asarray([v for _, v in anchor_vectors])
    sims = matrix @ anchors.T                      # 候选帧 × 锚点帧
    per_candidate = sims.max(axis=1)
    return {"score": float(np.median(per_candidate)),
            "supporting_candidate_frames": int((per_candidate >= weak_cosine).sum()),
            "frames": int(len(per_candidate))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor-camera", required=True)
    parser.add_argument("--anchor-track", type=int, required=True)
    parser.add_argument("--camera", action="append", required=True,
                        help="名称:帧目录:轨迹文件:文件起始墙钟秒:偏移秒:偏移不确定度")
    parser.add_argument("--mode", choices=["b", "c"], default="c")
    parser.add_argument("--overlap-confirmed", action="append", default=[],
                        help="A:B:true|false，人工判定过的视野重叠关系（不填=unknown）")
    parser.add_argument("--max-samples", type=int, default=12)
    parser.add_argument("--min-height", type=float, default=40.0)
    parser.add_argument("--min-observations", type=int, default=8)
    parser.add_argument("--sync-tolerance", type=float, default=0.5)
    parser.add_argument("--dense-top", type=int, default=5)
    parser.add_argument("--only-tracks", help="只评估这些候选轨迹 ID（逗号分隔），用于真值集")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    only = ({int(x) for x in args.only_tracks.split(",")} if args.only_tracks else None)

    session = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    cameras = {}
    for spec in args.camera:
        parts = spec.split(":")
        name, frames, tracks_path, start_wall, offset, uncertainty = parts[:6]
        status = parts[6] if len(parts) > 6 else "provisional"
        data = json.loads(Path(tracks_path).read_text(encoding="utf-8"))
        cameras[name] = {
            "name": name, "frames": frames,
            "tracks": [t for t in data["results"]["bytetrack"]["tracks"]
                       if len(t["observations"]) >= args.min_observations],
            "clock": clock_record(name, float(start_wall), float(offset),
                                  float(uncertainty) if uncertainty not in ("", "null") else None,
                                  calibration_status=status),
            "loader": frame_reader(frames)}

    spatial = {}
    for item in args.overlap_confirmed:
        left, right, value = item.split(":")
        spatial[tuple(sorted((left, right)))] = (value == "true")

    for cam in cameras.values():
        cam["vectors"] = {}
        for track in cam["tracks"]:
            usable = [o for o in track["observations"]
                      if float(o["bbox"][3]) - float(o["bbox"][1]) >= args.min_height]
            if len(usable) < 3:
                continue
            sample = stratified(usable, args.max_samples)
            embedded = embed_crops(session, [(o, to_wall(cam["clock"],
                                                         float(o["timestamp_seconds"])))
                                             for o in sample], cam["loader"])
            if embedded:
                cam["vectors"][track["track_id"]] = {str(o["frame_id"]): v
                                                     for _, v, o in embedded}

    def groups_of(cam):
        groups, _ = group_tracks(cam["tracks"], cam["vectors"])
        return groups

    anchor_cam = cameras[args.anchor_camera]
    anchor_group = next((g for g in groups_of(anchor_cam)
                         if args.anchor_track in g["track_ids"]), None)
    if anchor_group is None:
        raise SystemExit("锚点轨迹不在归组结果里")

    def observations_of(cam, track_ids):
        by_id = {t["track_id"]: t for t in cam["tracks"]}
        out = []
        for tid in track_ids:
            for obs in by_id[tid]["observations"]:
                out.append(obs)
        return out

    def vectors_of(cam, track_ids):
        out = []
        for tid in track_ids:
            for frame_id, vector in cam["vectors"].get(tid, {}).items():
                out.append((frame_id, vector))
        return out

    anchor_obs = observations_of(anchor_cam, anchor_group["track_ids"])
    anchor_vectors = vectors_of(anchor_cam, anchor_group["track_ids"])
    anchor_span = (min(to_wall(anchor_cam["clock"], float(o["timestamp_seconds"]))
                       for o in anchor_obs),
                   max(to_wall(anchor_cam["clock"], float(o["timestamp_seconds"]))
                       for o in anchor_obs))

    candidates = []
    for name, cam in cameras.items():
        if name == args.anchor_camera:
            continue
        key = tuple(sorted((args.anchor_camera, name)))
        for group in groups_of(cam):
            if only is not None and not set(group["track_ids"]) & only:
                continue
            obs = observations_of(cam, group["track_ids"])
            if not obs:
                continue
            times = [to_wall(cam["clock"], float(o["timestamp_seconds"])) for o in obs]
            stats = appearance_stats(anchor_vectors, vectors_of(cam, group["track_ids"]))
            stats["time_sufficiency"] = time_sufficiency(anchor_cam["clock"], cam["clock"])
            relation = temporal_relation(anchor_span, (min(times), max(times)))
            verdict = cross_status(relation, stats, {"overlap_confirmed": spatial.get(key)})
            candidates.append({"camera": name,
                               "track": "-".join(str(t) for t in group["track_ids"]),
                               "track_ids": list(group["track_ids"]),
                               "span": (round(min(times), 2), round(max(times), 2)),
                               "retrieval_score": round(stats["score"], 4),
                               "supporting_frames": stats["supporting_candidate_frames"],
                               "relation": relation["relation"],
                               "overlap_seconds": relation["overlap_seconds"],
                               "transition_gap_seconds": relation.get("transition_gap_seconds"),
                               "status": verdict["status"]})

    candidates.sort(key=lambda c: -c["retrieval_score"])
    for index, item in enumerate(candidates):
        item["rank"] = index + 1

    # —— 重叠区间定向加密：只处理排名靠前且确有重叠的候选 ——
    if args.mode == "c":
        dense_done = 0
        for item in candidates:
            if dense_done >= args.dense_top:
                break
            if item["relation"] != "overlapping" or item["overlap_seconds"] < 1.0:
                continue
            cam = cameras[item["camera"]]
            start = max(anchor_span[0], item["span"][0]) - 0.5
            end = min(anchor_span[1], item["span"][1]) + 0.5
            anchor_items = [(o, to_wall(anchor_cam["clock"], float(o["timestamp_seconds"])))
                            for o in anchor_obs
                            if start <= to_wall(anchor_cam["clock"],
                                                float(o["timestamp_seconds"])) <= end]
            cand_items = [(o, to_wall(cam["clock"], float(o["timestamp_seconds"])))
                          for o in observations_of(cam, item["track_ids"])
                          if start <= to_wall(cam["clock"],
                                              float(o["timestamp_seconds"])) <= end]
            anchor_vecs = embed_crops(session, anchor_items, anchor_cam["loader"])
            cand_vecs = embed_crops(session, cand_items, cam["loader"])
            pairs = pair_by_time([(t, (v, o)) for t, v, o in anchor_vecs],
                                 [(t, (v, o)) for t, v, o in cand_vecs],
                                 args.sync_tolerance)
            cosines = [float(np.dot(a["anchor"][0], a["candidate"][0])) for a in pairs]
            summary = summarise_pairs(pairs, cosines)
            item["synchronous_evidence"] = {"window": [round(start, 2), round(end, 2)],
                             "anchor_frames": len(anchor_vecs),
                             "candidate_frames": len(cand_vecs),
                             "paired_frames": summary["paired_frames"],
                             "median_cosine": summary["median_cosine"],
                             "min_cosine": summary["min_cosine"],
                             "max_cosine": summary["max_cosine"],
                             "frame_pairs": [{"anchor_frame": str(a["anchor"][1]["frame_id"]),
                                              "candidate_frame": str(a["candidate"][1]["frame_id"]),
                                              "cosine": round(c, 3),
                                              "delta_seconds": a["delta_seconds"]}
                                             for a, c in zip(pairs, cosines)]}
            dense_done += 1

    report = {"anchor": {"camera": args.anchor_camera, "track": args.anchor_track,
                         "group": anchor_group["track_ids"],
                         "span": (round(anchor_span[0], 2), round(anchor_span[1], 2))},
              "clocks": {name: cam["clock"] for name, cam in cameras.items()},
              "spatial_prior": {":".join(k): v for k, v in spatial.items()},
              "mode": args.mode,
              "candidates": candidates}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("锚点 %s #%s 墙钟 %.2f–%.2f；模式 %s"
          % (args.anchor_camera, anchor_group["track_ids"], anchor_span[0], anchor_span[1],
             args.mode))
    for item in candidates[:8]:
        dense = item.get("synchronous_evidence") or {}
        print("  #%-2d %-4s %-12s 检索 %.3f（%d 帧）%s 重叠 %.1fs/间隔 %s → %s"
              % (item["rank"], item["camera"], item["track"], item["retrieval_score"],
                 item["supporting_frames"], item["relation"], item["overlap_seconds"],
                 item["transition_gap_seconds"], item["status"]))
        if dense:
            print("       加密：配对 %d 帧，余弦中位 %.3f（%.2f–%.2f）"
                  % (dense["paired_frames"], dense["median_cosine"] or 0,
                     dense["min_cosine"] or 0, dense["max_cosine"] or 0))


if __name__ == "__main__":
    main()
