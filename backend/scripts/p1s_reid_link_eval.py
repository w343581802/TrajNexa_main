"""M6-S1-1C：外观重关联评估（同一份检测 + ByteTrack 轨迹，只加一层外观接回）。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.m6_reid_link import DEFAULT_PARAMS, embed_tracks, link_tracks  # noqa: E402
from scripts.p1s_track_compare import load_observations, run_engine, summarise  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--detections", required=True)
    parser.add_argument("--frames", required=True, help="帧图片目录，文件名 %%04d.jpg")
    parser.add_argument("--model", default="/srv/models/reid/osnet_x0_25_msmt17_b1.onnx")
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-cosine", type=float, default=DEFAULT_PARAMS["min_cosine"])
    parser.add_argument("--interval", type=float, default=0.2)
    args = parser.parse_args()

    import onnxruntime as ort
    session = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    frames = load_observations(args.detections, ("人", "person"), 0.20)
    params = {"interval": args.interval, "max_gap_seconds": 3.0, "max_missing_frames": 15,
              "activation_threshold": 0.20, "high_conf_threshold": 0.25,
              "minimum_consecutive_frames": 1}
    tracks, elapsed, _ = run_engine("bytetrack", frames, params)
    before = summarise(tracks, frames, 3.0)

    cache = {}

    def loader(idx):
        if idx not in cache:
            path = Path(args.frames) / ("%04d.jpg" % idx)
            cache[idx] = cv2.imread(str(path)) if path.exists() else None
        return cache[idx]

    started = time.perf_counter()
    gallery = embed_tracks(tracks, loader, session)
    embed_seconds = time.perf_counter() - started
    result = link_tracks(tracks, gallery, {"min_cosine": args.min_cosine})
    merged = result["merged_tracks"]
    after = summarise(merged, frames, 3.0)

    report = {
        "detections": args.detections,
        "tracks_before": before,
        "tracks_after": after,
        "embedded_tracks": len(gallery),
        "embed_seconds": round(embed_seconds, 2),
        "embed_ms_per_track": round(1000.0 * embed_seconds / max(1, len(gallery)), 1),
        "links": result["links"],
        "merge_map": result["merge_map"],
        "tracking_seconds": round(elapsed, 2),
        "tracks": merged,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    print("tracks %d -> %d   fragments %d -> %d   links %d   embed %.1fs" % (
        before["tracks"], after["tracks"], before["fragments_lt_1s"],
        after["fragments_lt_1s"], len(result["links"]), embed_seconds))
    for link in result["links"][:10]:
        print("  link %d->%d cos=%.3f gap=%.2fs dist=%.1fpx" % (
            link["from"], link["to"], link["cosine"], link["gap_seconds"], link["center_distance"]))


if __name__ == "__main__":
    main()
