"""M6-S4-1A：候选归组的证据清单 + 人工核验拼图。

输出两样东西：
1. `--out`：所有"时间有重叠"的轨迹对及其全部证据（不预先裁决）；
2. `--sheet`：排名靠前的 N 对的对照图（左=轨迹A，右=轨迹B，同一帧），
   供人工判断"是不是同一个物理目标"。

先看人工结论，再决定归组阈值，不直接用余弦硬阈值归组。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.m6_track_grouping import group_tracks, pair_evidence  # noqa: E402

MIN_OBS = 5
MIN_HEIGHT = 40


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


def build_frame_vectors(tracks, loader, session, max_samples=6):
    """逐帧向量：键是 frame_id，便于同帧严格比较。"""
    from app.m6_target_evidence import embed_observations

    vectors = {}
    for track in tracks:
        usable = [o for o in track["observations"] if (o["bbox"][3] - o["bbox"][1]) >= MIN_HEIGHT]
        if len(usable) < 2:
            continue
        pairs = embed_observations(usable, loader, session, max_samples)
        if len(pairs) < 2:
            continue
        vectors[track["track_id"]] = {str(obs["frame_id"]): vec for obs, vec in pairs}
    return vectors


def render_sheet(pairs, loader, out_path, limit=20):
    tiles = []
    for ev in pairs[:limit]:
        obs = ev["_shared_obs"]
        row = []
        for box, label in ((obs[0], "A%d" % ev["track_a"]), (obs[1], "B%d" % ev["track_b"])):
            frame = loader(ev["_frame_id"])
            if frame is None:
                row = []
                break
            x1, y1, x2, y2 = [int(round(v)) for v in box]
            pad = 30
            crop = frame[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
            if crop.size == 0:
                row = []
                break
            crop = cv2.resize(crop, (120, 240))
            cv2.putText(crop, label, (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
            row.append(crop)
        if len(row) != 2:
            continue
        tile = np.hstack(row)
        cv2.putText(tile, "iou%.2f c%.2f" % (ev["mean_iou"], ev["cosine_median"] or -1),
                    (3, 232), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
        tiles.append(tile)
    if not tiles:
        return 0
    while len(tiles) % 8:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + 8]) for i in range(0, len(tiles), 8)]
    cv2.imwrite(str(out_path), np.vstack(rows))
    return len(tiles)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks-report", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sheet")
    parser.add_argument("--groups-out", help="把归组结果写成 PhysicalObjectCandidate 列表")
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()

    import onnxruntime as ort

    tracks = [t for t in load_tracks(args.tracks_report) if len(t["observations"]) >= MIN_OBS]
    loader = frame_reader(args.frames)
    session = ort.InferenceSession("/srv/models/reid/osnet_x0_25_msmt17_b1.onnx",
                                   providers=["CPUExecutionProvider"])
    vectors = build_frame_vectors(tracks, loader, session)

    evidence = []
    for i, a in enumerate(tracks):
        for b in tracks[i + 1:]:
            ev = pair_evidence(a, b, vectors)
            if ev["overlap_frames"] < 3:
                continue
            shared = sorted(set(str(o["frame_id"]) for o in a["observations"]) &
                            set(str(o["frame_id"]) for o in b["observations"]), key=lambda f: float(f))
            frame_id = shared[0]
            box_a = next(o["bbox"] for o in a["observations"] if str(o["frame_id"]) == frame_id)
            box_b = next(o["bbox"] for o in b["observations"] if str(o["frame_id"]) == frame_id)
            ev["_frame_id"] = frame_id
            ev["_shared_obs"] = [box_a, box_b]
            evidence.append(ev)

    evidence.sort(key=lambda e: (-(e["cosine_median"] or 0), -e["mean_iou"]))
    serialisable = [{k: v for k, v in e.items() if not k.startswith("_")} for e in evidence]
    Path(args.out).write_text(json.dumps({"tracks": len(tracks), "pairs": serialisable},
                                         ensure_ascii=False, indent=2), encoding="utf-8")

    if args.sheet:
        rendered = render_sheet(evidence, loader, args.sheet, args.top)
        print("对照图 %d 对 -> %s" % (rendered, args.sheet))

    if args.groups_out:
        groups, _all = group_tracks(tracks, vectors)
        Path(args.groups_out).write_text(json.dumps({
            "groups": [g["track_ids"] for g in groups],
            "detailed": groups,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        multi = [g for g in groups if g["grouped"]]
        print("归组：%d 个候选对象（其中多轨组 %d 个，涉及 %d 条轨迹）-> %s" % (
            len(groups), len(multi), sum(len(g["track_ids"]) for g in multi), args.groups_out))

    same = sum(1 for e in evidence if e["verdict"] == "same_object")
    review = sum(1 for e in evidence if e["verdict"] == "needs_review")
    conflict = sum(1 for e in evidence if e["verdict"] == "conflict_coexist")
    print("轨迹 %d 条，重叠对 %d：same_object=%d needs_review=%d conflict=%d" % (
        len(tracks), len(evidence), same, review, conflict))
    for e in evidence[:8]:
        print("  %d-%d iou=%.2f cmed=%s hit=%s motion=%s → %s" % (
            e["track_a"], e["track_b"], e["mean_iou"], e["cosine_median"],
            e["cosine_hit_ratio"], e["motion_agreement"], e["verdict"]))


if __name__ == "__main__":
    main()
