"""M6-S6-2：跨摄像头候选比对（锚点来自 A 镜头，候选来自 B/C 镜头）。

  python scripts/p6s1_cross_camera.py --anchor-camera 1-1 --anchor-track 12 \
      --camera 1-1:/work/mk/s6/1-1:/work/mk/s6/tracks_1-1.json:45001:-4 \
      --camera 2-1:/work/mk/s6/2-1:/work/mk/s6/tracks_2-1.json:44999:0 \
      --camera 2-2:/work/mk/s6/2-2:/work/mk/s6/tracks_2-2.json:44997:-1 \
      --out /work/mk/s6/cross.json

--camera 的字段：名称:帧目录:轨迹文件:文件起始墙钟秒:OSD 偏移秒。
每个镜头内部先做重复轨迹归组，再以"候选对象"为单位跨镜头比对。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import onnxruntime as ort  # noqa: E402

from app.m6_cross_camera import rank_cross_candidates, wall_clock  # noqa: E402
from app.m6_multiview_match import compare  # noqa: E402
from app.m6_target_evidence import embed_observations  # noqa: E402
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
    picked = []
    for index in range(k):
        center = start + span * (index + 0.5) / k
        picked.append(min(ordered, key=lambda o: abs(float(o["timestamp_seconds"]) - center)))
    return picked


def load_camera(spec):
    name, frames, tracks_path, start_wall, offset = spec.split(":")
    data = json.loads(Path(tracks_path).read_text(encoding="utf-8"))
    tracks = data["results"]["bytetrack"]["tracks"]
    return {"name": name, "frames": frames, "tracks": tracks,
            "start_wall": float(start_wall), "offset": float(offset),
            "loader": frame_reader(frames)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor-camera", required=True)
    parser.add_argument("--anchor-track", type=int, required=True)
    parser.add_argument("--camera", action="append", required=True)
    parser.add_argument("--max-samples", type=int, default=12)
    parser.add_argument("--min-height", type=float, default=40.0)
    parser.add_argument("--min-observations", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    session = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    cameras = {c["name"]: c for c in (load_camera(spec) for spec in args.camera)}
    anchor_cam = cameras[args.anchor_camera]

    vectors = {}
    for cam in cameras.values():
        cam["vectors"] = {}
        cam["tracks"] = [t for t in cam["tracks"]
                         if len(t["observations"]) >= args.min_observations]
        for track in cam["tracks"]:
            usable = [o for o in track["observations"]
                      if float(o["bbox"][3]) - float(o["bbox"][1]) >= args.min_height]
            if len(usable) < 3:
                continue
            sample = stratified(usable, args.max_samples)
            pairs = embed_observations(sample, cam["loader"], session, len(sample))
            if pairs:
                cam["vectors"][track["track_id"]] = {str(o["frame_id"]): v for o, v in pairs}
        print("%s：%d 条轨迹，编码 %d 条" % (cam["name"], len(cam["tracks"]),
                                            len(cam["vectors"])))

    def groups_of(cam):
        groups, _ = group_tracks(cam["tracks"], cam["vectors"])
        return groups

    anchor_groups = groups_of(anchor_cam)
    anchor_group = next((g for g in anchor_groups if args.anchor_track in g["track_ids"]), None)
    if anchor_group is None:
        raise SystemExit("锚点轨迹不在归组结果里")

    def pairs_of(cam, track_ids):
        by_id = {t["track_id"]: t for t in cam["tracks"]}
        out = []
        for tid in track_ids:
            obs_by_frame = {str(o["frame_id"]): o for o in by_id[tid]["observations"]}
            for frame_id, vector in cam["vectors"].get(tid, {}).items():
                obs = obs_by_frame.get(str(frame_id))
                if obs is not None:
                    out.append((obs, "unknown", vector))
        return out

    anchor_pairs = pairs_of(anchor_cam, anchor_group["track_ids"])
    anchor_times = [wall_clock(anchor_cam["start_wall"], anchor_cam["offset"],
                               float(o["timestamp_seconds"])) for o, _, _ in anchor_pairs]
    anchor_span = (min(anchor_times), max(anchor_times))

    candidates = []
    for name, cam in cameras.items():
        if name == args.anchor_camera:
            continue
        for group in groups_of(cam):
            pairs = pairs_of(cam, group["track_ids"])
            if not pairs:
                continue
            stats = compare(anchor_pairs, pairs, "per_frame")
            times = [wall_clock(cam["start_wall"], cam["offset"],
                                float(o["timestamp_seconds"])) for o, _, _ in pairs]
            candidates.append({"camera": name,
                               "track": "-".join(str(t) for t in group["track_ids"]),
                               "span": (min(times), max(times)),
                               "appearance": stats, "pairs": pairs})

    ranked = rank_cross_candidates(anchor_span, candidates)

    # 有重叠视野的摄像头会出现"同一时刻双路可见"：这时不能用"必须先后出现"的门控否决，
    # 改为找时间对齐的帧对，直接比较同一时刻的外观。
    import numpy as np
    anchor_by_time = [(wall_clock(anchor_cam["start_wall"], anchor_cam["offset"],
                                  float(o["timestamp_seconds"])), v) for o, _, v in anchor_pairs]
    by_track = {(c["camera"], c["track"]): c for c in candidates}
    for item in ranked:
        source = by_track.get((item["camera"], item["track"]))
        if source is None:
            continue
        cand_by_time = [(wall_clock(cameras[item["camera"]]["start_wall"],
                                    cameras[item["camera"]]["offset"],
                                    float(o["timestamp_seconds"])), v)
                        for o, _, v in source["pairs"]]
        hits = []
        for t_a, v_a in anchor_by_time:
            best = None
            for t_c, v_c in cand_by_time:
                delta = abs(t_a - t_c)
                if delta <= 1.0 and (best is None or delta < best[0]):
                    best = (delta, float(np.dot(v_a, v_c)))
            if best is not None:
                hits.append(best)
        item["simultaneous_frames"] = len(hits)
        item["simultaneous_cosine"] = (round(float(np.median([c for _, c in hits])), 3)
                                       if hits else None)
        if item["gap_seconds"] < 0 and item["simultaneous_frames"] >= 2 \
                and (item["simultaneous_cosine"] or 0) >= 0.80:
            item["status"] = "same_object_in_overlapping_views"
    report = {"anchor": {"camera": args.anchor_camera, "track": args.anchor_track,
                         "group": anchor_group["track_ids"],
                         "span": anchor_span, "frames": len(anchor_pairs)},
              "params": {"max_samples": args.max_samples, "min_height": args.min_height},
              "candidates": ranked}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("锚点：%s / 轨道 %s，墙钟 %.0f–%.0f（%d 帧）"
          % (args.anchor_camera, anchor_group["track_ids"], anchor_span[0], anchor_span[1],
             len(anchor_pairs)))
    for item in ranked[:12]:
        print("  #%-2d %-4s %-14s 外观 %.3f（%d 帧）时间差 %6.1fs 同刻 %s帧/%.2f → %s"
              % (item["rank"], item["camera"], item["track"], item["score"],
                 item["supporting_frames"], item["gap_seconds"],
                 item.get("simultaneous_frames", 0),
                 item.get("simultaneous_cosine") or 0.0, item["status"]))


if __name__ == "__main__":
    main()
