"""P1-B-1A-2：两轮车细分类的关键帧选取与结果聚合。

分工：
- 本脚本只负责挑关键帧（车身完整、遮挡少、够大、够清晰）并把裁剪图落盘；
- 视觉模型（qwen3-vl:4b-instruct）在外部调用，结果按轨迹聚合；
- 无法区分燃油/电动时一律 unknown，不猜。

注意：视觉模型看到的是**外观线索**，不能保证真实动力类型；
人工确认（--overrides）优先于模型推断。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

VEHICLE_TYPES = ("motorcycle", "electric_two_wheeler", "bicycle", "unknown")


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def keyframe_score(frame, persons, image_shape, sample_interval):
    """越大越好：框大、置信度高、不在画面边缘、不被行人挡住。"""
    x1, y1, x2, y2 = frame["bbox"]
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    area = w * h
    edge = min(x1, y1, image_shape[1] - x2, image_shape[0] - y2)
    edge_penalty = 0.5 if edge < 4 else 1.0
    person_overlap = max((_iou(frame["bbox"], p) for p in persons), default=0.0)
    occlusion = 1.0 - min(1.0, person_overlap)
    size = min(1.0, area / (160.0 * 120.0))
    return frame["confidence"] * (0.4 + 0.6 * size) * occlusion * edge_penalty


def pick_keyframes(frames, persons_by_frame, image_shape, top_k, min_gap):
    scored = []
    for frame in frames:
        score = keyframe_score(frame, persons_by_frame.get(frame["frame_id"], []), image_shape, 0.2)
        scored.append((score, frame))
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = []
    for score, frame in scored:
        if all(abs(frame["timestamp_seconds"] - c["timestamp_seconds"]) >= min_gap for c in chosen):
            chosen.append(dict(frame, score=round(score, 4)))
        if len(chosen) >= top_k:
            break
    return sorted(chosen, key=lambda f: f["timestamp_seconds"])


def write_crops(tracks, persons_by_frame, frames_dir, out_dir, top_k, min_gap, min_obs, scale):
    import cv2      # 只有真正裁图时才需要 OpenCV，便于在无 cv2 环境里单测聚合逻辑

    os.makedirs(out_dir, exist_ok=True)
    manifest = []
    for track in tracks:
        if len(track.get("frames", [])) < min_obs:
            continue
        path0 = os.path.join(frames_dir, track["frames"][0]["frame_id"] + ".jpg")
        probe = cv2.imread(path0)
        if probe is None:
            continue
        chosen = pick_keyframes(track["frames"], persons_by_frame, probe.shape, top_k, min_gap)
        for idx, frame in enumerate(chosen):
            img = cv2.imread(os.path.join(frames_dir, frame["frame_id"] + ".jpg"))
            if img is None:
                continue
            x1, y1, x2, y2 = [int(round(v)) for v in frame["bbox"]]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(img.shape[1], x2), min(img.shape[0], y2)
            if x2 - x1 < 12 or y2 - y1 < 12:
                continue
            crop = img[y1:y2, x1:x2]
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            name = "t%03d_k%d_%s.jpg" % (track["track_id"], idx, frame["frame_id"])
            cv2.imwrite(os.path.join(out_dir, name), crop)
            manifest.append({"track_id": track["track_id"],
                             "frame_id": frame["frame_id"],
                             "timestamp_seconds": frame["timestamp_seconds"],
                             "bbox": frame["bbox"],
                             "score": frame["score"],
                             "detector_label": track.get("label", ""),
                             "crop": name})
    return manifest


def aggregate(manifest, votes, overrides=None, min_crop_size=100.0):
    """按轨迹汇总裁剪帧的判断：多数票 + 置信度加权，证据不足判 unknown。

    裁剪图小于 `min_crop_size` 时，无论模型怎么说都按 unknown 计——
    实测 50×50 的裁剪图上模型会给出 0.8 的"摩托车"，但人眼也分辨不出动力类型。
    """
    overrides = overrides or {}
    by_track = {}
    forced_unknown = 0
    for item in manifest:
        key = item["crop"]
        vote = votes.get(key)
        if not vote:
            continue
        x1, y1, x2, y2 = item["bbox"]
        too_small = (x2 - x1) < min_crop_size or (y2 - y1) < min_crop_size
        if too_small:
            forced_unknown += 1
        by_track.setdefault(item["track_id"], []).append(
            {"frame_id": item["frame_id"], "timestamp_seconds": item["timestamp_seconds"],
             "vehicle_type": "unknown" if too_small else vote.get("vehicle_type", "unknown"),
             "confidence": float(vote.get("confidence", 0.0)),
             "evidence": vote.get("evidence", ""),
             "forced_unknown_by_size": too_small,
             "crop": key})
    result = []
    for track_id, items in sorted(by_track.items()):
        weight = {}
        for item in items:
            if item["vehicle_type"] not in VEHICLE_TYPES:
                continue
            weight[item["vehicle_type"]] = weight.get(item["vehicle_type"], 0.0) + max(0.05, item["confidence"])
        total = sum(weight.values()) or 1.0
        ranked = sorted(weight.items(), key=lambda kv: kv[1], reverse=True)
        top_type, top_weight = ranked[0]
        share = top_weight / total
        decided = top_type if (top_type != "unknown" and share >= 0.6) else "unknown"
        override = overrides.get(str(track_id))
        result.append({
            "track_id": track_id,
            "vehicle_type": override or decided,
            "vehicle_type_status": "human_confirmed" if override else "model_inferred",
            "confidence": round(share, 3),
            "model_type": decided,
            "votes": items,
            "votes_forced_unknown_by_size": sum(1 for i in items if i.get("forced_unknown_by_size")),
        })
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="两轮车细分类关键帧与聚合（P1-B-1A-2）")
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--detections", required=True)
    parser.add_argument("--frames-dir", required=True)
    parser.add_argument("--crops-dir")
    parser.add_argument("--out-manifest")
    parser.add_argument("--votes")
    parser.add_argument("--overrides")
    parser.add_argument("--min-crop-size", type=float, default=100.0,
                        help="裁剪图小于该像素尺寸时强制 unknown，防止在小图上强行分类")
    parser.add_argument("--out")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-gap", type=float, default=1.5)
    parser.add_argument("--min-obs", type=int, default=6)
    parser.add_argument("--crop-scale", type=float, default=2.0)
    args = parser.parse_args(argv)

    tracks = json.load(open(args.tracks, "r", encoding="utf-8"))["tracks"]
    persons_by_frame = {}
    for frame in json.load(open(args.detections, "r", encoding="utf-8"))["frames"]:
        persons_by_frame[frame["frame_id"]] = [d["bbox"] for d in frame["detections"] if d.get("label") == "人"]

    if args.votes and args.out:
        manifest = json.load(open(args.out_manifest, "r", encoding="utf-8"))["items"]
        votes = json.load(open(args.votes, "r", encoding="utf-8"))
        overrides = json.load(open(args.overrides, "r", encoding="utf-8")) if args.overrides else {}
        result = aggregate(manifest, votes, overrides, args.min_crop_size)
        json.dump({"tracks": result}, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(json.dumps({"classified_tracks": len(result),
                          "types": {name: sum(1 for r in result if r["vehicle_type"] == name)
                                    for name in VEHICLE_TYPES},
                          "forced_unknown_by_size": sum(r.get("votes_forced_unknown_by_size", 0)
                                                        for r in result)}, ensure_ascii=False))
        return 0

    if args.out_manifest:
        manifest = write_crops(tracks, persons_by_frame, args.frames_dir, args.crops_dir,
                               args.top_k, args.min_gap, args.min_obs, args.crop_scale)
        json.dump({"crops_dir": args.crops_dir, "items": manifest},
                  open(args.out_manifest, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(json.dumps({"keyframes": len(manifest),
                          "tracks": len({m["track_id"] for m in manifest})}, ensure_ascii=False))
        return 0

    raise SystemExit("需要 --out-manifest，或同时提供 --votes、--out-manifest 与 --out")


if __name__ == "__main__":
    sys.exit(main())
