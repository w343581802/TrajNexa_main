"""M6-S4-2：多视角匹配 A/B/C 对照实验。

用法：
  python scripts/p4s2_multiview.py --tracks-report ... --groups ... --frames ... \
      --annotations scripts/fixtures/m6_s4_views_target41.json --out report.json

锚点与候选来自**同一目标的不同时间段/不同视角段**，候选池里同时放入其他候选对象作为负样本；
参考帧与候选帧之间强制时间间隔，避免用同一批画面给自己打分。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from app.m6_multiview_match import compare, label_views  # noqa: E402
from app.m6_target_evidence import embed_observations  # noqa: E402

MODES = ("aggregate", "per_frame", "view_weighted")


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


def embed(labelled, loader, session, max_samples):
    usable = [obs for obs, _ in labelled]
    view_of = {str(obs["frame_id"]): view for obs, view in labelled}
    pairs = embed_observations(usable, loader, session, max_samples)
    return [(obs, view_of.get(str(obs["frame_id"]), "unknown"), vec) for obs, vec in pairs]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks-report", required=True)
    parser.add_argument("--groups", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--annotations", required=True)
    parser.add_argument("--max-samples", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import onnxruntime as ort

    spec = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    target_id = spec["target_track"]
    tracks = {t["track_id"]: t for t in load_tracks(args.tracks_report)}
    groups = json.loads(Path(args.groups).read_text(encoding="utf-8"))["groups"]
    loader = frame_reader(args.frames)
    session = ort.InferenceSession("/srv/models/osnet/osnet_x0_25_msmt17_b1.onnx".replace(
        "/srv/models/osnet/", "/srv/models/reid/"), providers=["CPUExecutionProvider"])

    target_labelled = label_views(tracks[target_id]["observations"], spec["views"])
    target_pairs = embed(target_labelled, loader, session, args.max_samples)
    target_group = next((g for g in groups if target_id in g), [target_id])

    # 负样本池必须是"不同的人"：同一身份的其他 Tracklet（未归进目标组的那几条）不能当负样本，
    # 否则会把真值当成错误候选，评测立刻失真。
    identity = set(spec.get("identity_tracks", [])) | set(target_group)
    negatives = []
    for g in groups:
        if set(g) & identity:
            continue
        pairs = []
        for tid in g:
            if tid in tracks:
                pairs.extend(embed(label_views(tracks[tid]["observations"], []), loader, session, 4))
        if pairs:
            negatives.append({"candidate": "-".join(str(i) for i in g), "pairs": pairs})

    results = []
    for case in spec["cases"]:
        anchor = [p for p in target_pairs if p[1] == case["anchor_view"]]
        probe = [p for p in target_pairs if p[1] in case["candidate_views"]]
        if not anchor or not probe:
            results.append({"case": case["name"], "skipped": "视角段内没有可用观测",
                            "anchor_frames": len(anchor), "probe_frames": len(probe)})
            continue
        row = {"case": case["name"], "anchor_view": case["anchor_view"],
               "candidate_views": case["candidate_views"],
               "anchor_frames": len(anchor), "probe_frames": len(probe)}
        for mode in MODES:
            target_score = compare(anchor, probe, mode)
            scored = []
            for neg in negatives:
                stats = compare(anchor, neg["pairs"], mode)
                scored.append((stats["score"], neg["candidate"], neg["candidate"]))
            scored.sort(reverse=True)
            better = sum(1 for s, _, _ in scored if s >= target_score["score"])
            margin = round(target_score["score"] - scored[0][0], 4) if scored else None
            # 证据不足就明确说"不足"，不因为排到第一就当成结论
            if len(anchor) < 2 or target_score["supporting_frames"] < 2:
                status = "insufficient_evidence"
            elif margin is not None and margin < 0.03:
                status = "ambiguous"
            else:
                status = "candidate"
            row[mode] = {
                "target_score": round(target_score["score"], 4),
                "target_rank": better + 1,
                "supporting_frames": target_score["supporting_frames"],
                "views_used": target_score["views_used"],
                "status": status,
                "top_negative": {"candidate": scored[0][1],
                                 "score": round(scored[0][0], 4)} if scored else None,
                "margin": margin,
            }
        results.append(row)

    report = {"target_track": target_id, "target_group": target_group,
              "views": spec["views"], "cases": results}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for row in results:
        if row.get("skipped"):
            print("%-28s 跳过：%s" % (row["case"], row["skipped"]))
            continue
        print("%-28s 锚点%d帧 候选%d帧" % (row["case"], row["anchor_frames"], row["probe_frames"]))
        for mode in MODES:
            m = row[mode]
            print("    %-14s score=%.3f rank=%d margin=%s 支撑=%d → %-21s 负样本=%s(%.3f)" % (
                mode, m["target_score"], m["target_rank"], m["margin"], m["supporting_frames"],
                m["status"],
                m["top_negative"]["candidate"] if m["top_negative"] else "-",
                m["top_negative"]["score"] if m["top_negative"] else 0.0))


if __name__ == "__main__":
    main()
