"""Commit E1/E2：轨迹级 OSNet 特征与区分能力验证。

E1 只做特征：每帧 crop 取 512 维向量（不做单帧入库、不做跨镜头），
按检测质量加权聚合成轨迹模板，**同时保留逐帧向量**，避免聚合丢掉视角差异。

E2 只做判断：同人跨帧是否稳定高于不同人。不接轨迹、不改现有流程。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engines.factory import EngineFactory  # noqa: E402

MODEL = "models/reid/osnet_x0_25_msmt17_b1.onnx"


def _l2(vec):
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _load_engine(root):
    engine = EngineFactory.create("onnxruntime", task_type="reid", algorithm_type="osnet",
                                  model_file=os.path.join(root, MODEL),
                                  input_size=(128, 256), device="cpu")
    if not engine.load():
        raise SystemExit("reid_engine_load_failed")
    return engine


def quality(conf, height):
    """检测质量权重：置信度 × 尺寸饱和项（太小的框裁出来几乎全是背景）。"""
    return float(max(0.0, conf)) * min(1.0, float(height) / 80.0)


def pick_observations(track, max_frames):
    """按时间分层取样，每层挑质量最好的一帧。

    只按质量排序会把整条轨迹的帧全取在最后一小段（人走近时框最大），
    这样聚合模板会丢失早期视角，也做不了"断开再恢复"的模拟实验。
    """
    obs = sorted([o for o in track["observations"] if o.get("bbox")],
                 key=lambda o: o["timestamp_seconds"])
    if len(obs) <= max_frames:
        return obs
    bins = [[] for _ in range(max_frames)]
    total = len(obs)
    for i, o in enumerate(obs):
        bins[min(max_frames - 1, i * max_frames // total)].append(o)
    chosen = [max(b, key=lambda o: quality(o["confidence"], o["bbox"][3] - o["bbox"][1]))
              for b in bins if b]
    return sorted(chosen, key=lambda o: o["timestamp_seconds"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="轨迹级 OSNet 特征（E1）")
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--gt")
    parser.add_argument("--gt-time-tolerance", type=float, default=0.1)
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    engine = _load_engine(root)
    tracks = json.load(open(args.tracks, "r", encoding="utf-8"))["tracks"]

    out = {"model": MODEL, "tracks": [], "notes": []}
    cache = {}
    for track in tracks:
        chosen = pick_observations(track, args.max_frames)
        frames = []
        for o in chosen:
            path = os.path.join(args.frames_dir, o["frame_id"] + ".jpg")
            if not os.path.exists(path):
                continue
            if path not in cache:
                cache[path] = cv2.imread(path)
            img = cache[path]
            if img is None:
                continue
            box = [int(round(v)) for v in o["bbox"]]
            x1, y1 = max(0, box[0]), max(0, box[1])
            x2, y2 = min(img.shape[1], box[2]), min(img.shape[0], box[3])
            if x2 - x1 < 8 or y2 - y1 < 16:
                continue
            valid, embs = engine.extract_embeddings(img, [[x1, y1, x2, y2]])
            if not valid:
                continue
            vec = _l2(np.asarray(embs[0], dtype=np.float32))
            w = quality(o["confidence"], y2 - y1)
            frames.append({"frame_id": o["frame_id"],
                           "timestamp_seconds": o["timestamp_seconds"],
                           "bbox": [x1, y1, x2, y2],
                           "confidence": round(float(o["confidence"]), 4),
                           "weight": round(w, 4),
                           "vec": [round(float(v), 5) for v in vec]})
        if not frames:
            out["tracks"].append({"track_id": track["track_id"], "frames": [],
                                  "aggregate": None, "note": "no_usable_crop"})
            continue
        weights = np.asarray([f["weight"] for f in frames], dtype=np.float32)
        weights = weights / max(1e-6, float(weights.sum()))
        stacked = np.asarray([f["vec"] for f in frames], dtype=np.float32)
        agg = _l2((stacked * weights[:, None]).sum(axis=0))
        out["tracks"].append({
            "track_id": track["track_id"],
            "start_seconds": track["start_seconds"],
            "end_seconds": track["end_seconds"],
            "observation_count": len(track["observations"]),
            "used_frames": len(frames),
            "mean_quality": round(float(np.mean([f["weight"] for f in frames])), 4),
            "aggregate": [round(float(v), 5) for v in agg],
            "frames": frames,
        })

    if args.gt and os.path.isfile(args.gt):
        out["discrimination"] = discriminate(out["tracks"], tracks,
                                             json.load(open(args.gt, "r", encoding="utf-8")),
                                             args.gt_time_tolerance)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(summarize(out), ensure_ascii=False, indent=2))
    return 0


def _track_for_gt(raw_tracks, item, tol):
    """真值帧落在哪条轨迹上：用**全部**观测匹配，特征只抽了部分帧，不能拿它定位。"""
    best, bv = None, 0.0
    for track in raw_tracks:
        for obs in track.get("observations", []):
            if abs(obs["timestamp_seconds"] - item["timestamp_seconds"]) > tol:
                continue
            value = _iou(item["bbox"], obs["bbox"])
            if value > bv:
                best, bv = track["track_id"], value
    return best if bv >= 0.5 else None


def discriminate(tracks, raw_tracks, gt, tol):
    """同人 vs 不同人 vs 困难负样本。只用余弦相似度，不设门限、不做关联。"""
    by_id = {}
    for track in gt.get("tracks", []):
        votes = {}
        for item in track.get("frames", []):
            found = _track_for_gt(raw_tracks, item, tol)
            if found is not None:
                votes[found] = votes.get(found, 0) + 1
        if votes:
            by_id[track["gt_id"]] = max(votes, key=votes.get)
    index = {t["track_id"]: t for t in tracks if t.get("frames")}
    by_id = {k: v for k, v in by_id.items() if v in index}

    def agg(tid):
        return np.asarray(index[tid]["aggregate"], dtype=np.float32)

    result = {"gt_to_track": by_id, "intra": {}, "inter": {}, "hard_negatives": {}}
    for gt_id, tid in by_id.items():
        frames = index[tid]["frames"]
        sims = []
        for i in range(len(frames)):
            for j in range(i + 1, len(frames)):
                sims.append(float(np.dot(frames[i]["vec"], frames[j]["vec"])))
        result["intra"][gt_id] = {"track_id": tid, "pairs": len(sims),
                                  "mean": round(float(np.mean(sims)), 4) if sims else None,
                                  "min": round(float(np.min(sims)), 4) if sims else None,
                                  "max": round(float(np.max(sims)), 4) if sims else None}
    ids = list(by_id)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            result["inter"]["%s_vs_%s" % (ids[i], ids[j])] = round(
                float(np.dot(agg(by_id[ids[i]]), agg(by_id[ids[j]]))), 4)
    for gt_id, tid in by_id.items():
        start = index[tid]["start_seconds"]
        end = index[tid]["end_seconds"]
        scored = []
        for other in tracks:
            if not other.get("frames") or other["track_id"] == tid:
                continue
            if other["end_seconds"] < start - 2 or other["start_seconds"] > end + 2:
                continue
            scored.append({"track_id": other["track_id"],
                           "frames": other["used_frames"],
                           "similarity": round(float(np.dot(agg(tid), np.asarray(other["aggregate"], dtype=np.float32))), 4)})
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        result["hard_negatives"][gt_id] = scored[:5]
    return result


def summarize(out):
    disc = out.get("discrimination") or {}
    return {"tracks_with_features": sum(1 for t in out["tracks"] if t.get("frames")),
            "gt_to_track": disc.get("gt_to_track"),
            "intra": disc.get("intra"),
            "inter": disc.get("inter"),
            "hard_negatives": disc.get("hard_negatives")}


if __name__ == "__main__":
    sys.exit(main())
