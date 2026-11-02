"""M6-S2：FastTracker（Ultralytics，AGPL-3.0）隔离评测脚本。

**只用于实验对照，不进入产品链路。**
Ultralytics 采用 AGPL-3.0，不能并入闭源交付的主系统；这里把它放在
`scripts/experimental/` 下，由脚本按需调用，`app/` 中没有任何代码 import 它。
若实验证明有效，再评估 MIT 版原项目（FAST-Tracker）的集成可行性。

输入与 `p1s_track_compare.py` 完全相同的检测结果，输出同样结构的轨迹报告，
方便与 ByteTrack 基线逐项对比。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from app.m6_track import TrackObservation  # noqa: E402
from scripts.p1s_track_compare import load_observations, summarise  # noqa: E402

PERSON_LABELS = ("人", "person")

DEFAULTS = {
    "tracker_type": "fasttrack",
    "track_high_thresh": 0.25,
    "track_low_thresh": 0.10,
    "new_track_thresh": 0.25,
    "track_buffer": 15,
    "match_thresh": 0.8,
    "fuse_score": True,
    "reset_velocity_offset_occ": 5,
    "reset_pos_offset_occ": 3,
    "enlarge_bbox_occ": 1.1,
    "dampen_motion_occ": 0.5,
    "active_occ_to_lost_thresh": 10,
    "occ_cover_thresh": 0.7,
    "occ_reappear_window": 40,
    "init_iou_suppress": 0.7,
}


class Boxes:
    """最小 `ultralytics.engine.results.Boxes` 替身。

    跟踪器需要同时满足：属性访问（`.xywh/.conf/.cls`）、`len()`、
    以及布尔索引（`results[mask]` 返回同类型），所以不能只用结构化数组。
    """

    def __init__(self, xywh, conf, cls):
        self.xywh = np.asarray(xywh, dtype=np.float32).reshape(-1, 4)
        self.conf = np.asarray(conf, dtype=np.float32).reshape(-1)
        self.cls = np.asarray(cls, dtype=np.float32).reshape(-1)

    def __len__(self):
        return len(self.conf)

    def __getitem__(self, idx):
        return Boxes(self.xywh[idx], self.conf[idx], self.cls[idx])


def to_results(observations):
    """把本项目的观测转成 Ultralytics 风格的检测对象。"""
    if not observations:
        return Boxes(np.zeros((0, 4)), np.zeros((0,)), np.zeros((0,)))
    boxes = np.asarray([o.bbox for o in observations], dtype=np.float32)
    xywh = np.stack([(boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2,
                     boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]], axis=1)
    return Boxes(xywh, [o.confidence for o in observations], [o.class_id for o in observations])


def build_tracker(buffer_frames, overrides=None, engine="fasttracker"):
    """engine=bytetrack 时用 Ultralytics 自带的 ByteTrack 做驱动层对照。

    为什么需要这个对照：如果"用同一份检测驱动 Ultralytics ByteTrack"也会出现
    串轨，那说明问题出在驱动层而不是 FastTracker 的遮挡逻辑。
    """
    cfg = dict(DEFAULTS, track_buffer=int(buffer_frames))
    cfg.update(overrides or {})
    if engine == "bytetrack":
        from ultralytics.trackers import BYTETracker
        return BYTETracker(SimpleNamespace(**cfg))
    from ultralytics.trackers import FASTTracker
    return FASTTracker(SimpleNamespace(**cfg))


def run(frames, buffer_frames, overrides=None, engine="fasttracker"):
    tracker = build_tracker(buffer_frames, overrides, engine)
    tracks = {}
    started = time.perf_counter()
    for frame_id, timestamp, observations in frames:
        out = tracker.update(to_results(observations), None)
        if out is None or len(out) == 0:
            continue
        for row in np.asarray(out):
            x1, y1, x2, y2, track_id, score, cls, det_index = row[:8]
            pos = int(det_index)
            if pos < 0 or pos >= len(observations):
                continue
            obs = observations[pos]
            entry = tracks.setdefault(int(track_id), {"track_id": int(track_id),
                                                      "observations": [], "frames": []})
            entry["observations"].append({"frame_id": frame_id,
                                          "timestamp_seconds": timestamp,
                                          "bbox": [float(x1), float(y1), float(x2), float(y2)],
                                          "confidence": float(score)})
    elapsed = time.perf_counter() - started
    result = []
    for track in tracks.values():
        obs = sorted(track["observations"], key=lambda o: o["timestamp_seconds"])
        result.append({"track_id": track["track_id"], "observations": obs,
                       "start_seconds": obs[0]["timestamp_seconds"],
                       "end_seconds": obs[-1]["timestamp_seconds"],
                       "duration_seconds": round(obs[-1]["timestamp_seconds"] - obs[0]["timestamp_seconds"], 3)})
    return result, elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--detections", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-conf", type=float, default=0.20)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--buffer-frames", type=int, default=15)
    parser.add_argument("--occ-reappear-window", type=int, default=None)
    parser.add_argument("--init-iou-suppress", type=float, default=None)
    parser.add_argument("--enlarge-bbox-occ", type=float, default=None)
    parser.add_argument("--engine", default="fasttracker", choices=["fasttracker", "bytetrack"])
    parser.add_argument("--baseline-report", default=None,
                        help="ByteTrack 报告：用来统计 FastTracker 是否把不同目标串成了一条")
    args = parser.parse_args()

    overrides = {}
    if args.occ_reappear_window is not None:
        overrides["occ_reappear_window"] = args.occ_reappear_window
    if args.init_iou_suppress is not None:
        overrides["init_iou_suppress"] = args.init_iou_suppress
    if args.enlarge_bbox_occ is not None:
        overrides["enlarge_bbox_occ"] = args.enlarge_bbox_occ

    frames = load_observations(args.detections, PERSON_LABELS, args.min_conf)
    tracks, elapsed = run(frames, args.buffer_frames, overrides, args.engine)
    summary = summarise(tracks, frames, args.interval * 3)
    summary["ms_per_frame"] = round(1000.0 * elapsed / max(1, len(frames)), 2)
    report = {"detections": args.detections, "frames": len(frames),
              "config": dict(DEFAULTS, track_buffer=args.buffer_frames),
              "summary": summary, "tracks": tracks}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    if args.baseline_report:
        merged = _chained_merges(tracks, args.baseline_report)
        report["chained_merges"] = merged
        print("   把不同目标串成一条的轨迹: %d 条 / 涉及 %d 个基准目标" % (
            merged["tracks_with_chains"], merged["chained_links"]))
    print("fasttracker tracks=%d median=%.1fs frag=%d handover=%d %.1f ms/frame" % (
        summary["tracks"], summary["median_duration_s"], summary["fragments_lt_1s"],
        summary["handover_suspects"], summary["ms_per_frame"]))


def _chained_merges(tracks, baseline_report):
    """统计"一条 FastTracker 轨迹先后覆盖多个互不重叠的基准目标"的情况。"""
    base = json.loads(Path(baseline_report).read_text(encoding="utf-8"))
    base_tracks = base["results"]["bytetrack"]["tracks"] if "results" in base else base["tracks"]
    by_frame = {}
    for track in base_tracks:
        for obs in track["observations"]:
            by_frame.setdefault(obs["frame_id"], []).append((track["track_id"], obs["bbox"]))

    def iou(a, b):
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        x2, y2 = min(a[2], b[2]), min(a[3], b[3])
        if x2 <= x1 or y2 <= y1:
            return 0.0
        inter = (x2 - x1) * (y2 - y1)
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / union if union > 0 else 0.0

    details, chained_links = [], 0
    for track in tracks:
        overlap = {}
        for obs in track["observations"]:
            for base_id, box in by_frame.get(obs["frame_id"], []):
                if iou(obs["bbox"], box) >= 0.3:
                    overlap.setdefault(base_id, []).append(obs["timestamp_seconds"])
        strong = {k: v for k, v in overlap.items() if len(v) >= 3}
        if len(strong) < 2:
            continue
        spans = sorted((min(v), max(v)) for v in strong.values())
        chains = sum(1 for a, b in zip(spans, spans[1:]) if b[0] > a[1] + 0.4)
        if chains:
            chained_links += chains
            details.append({"track_id": track["track_id"], "base_targets": len(strong),
                            "chains": chains, "frames": len(track["observations"])})
    return {"tracks_with_chains": len(details), "chained_links": chained_links,
            "details": sorted(details, key=lambda d: -d["chains"])[:10]}


if __name__ == "__main__":
    main()
