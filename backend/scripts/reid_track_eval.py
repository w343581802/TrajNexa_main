"""Commit C：单镜头 ByteTrack 轨迹评测脚本（脚本级，不动 UI / 数据库）。

输入两种来源：
1. --detections：已导出的逐帧检测 JSON（可含低分框，供 ByteTrack 二次关联使用）；
2. --from-gallery：现有 gallery.json（conf 有下限，仅作冒烟）。

输出（写入 --out 目录）：
- tracks.json        轨迹本身：track_id、起止时间、每帧框与置信度、原始检测编号、状态
- track_events.json  轨迹事件：创建 / 关联 / 丢失 / 恢复 / 结束 / 重叠冲突
- errors.json        评测与执行问题：ID Switch、False Merge、断裂、推理失败、输入错误

评测口径：以人工真值里“可见帧”为分母；IoU 匹配只是辅助，
自动识别到的身份切换一律标记 needs_review，不当作已确认错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.m6_fragments import (  # noqa: E402
    attribute_fragments,
    dedupe_same_frame,
    to_detections,
)
from app.m6_track import (  # noqa: E402
    ByteTrackAdapter,
    TrackDependencyError,
    observations_from_detections,
)


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def _frame_seconds(frame):
    """取帧时间；缺字段返回 None，不要用 0.0 兜底（会把整段素材压到同一时刻）。"""
    for key in ("timestamp_seconds", "seconds"):
        value = frame.get(key)
        if value is not None:
            return float(value)
    return None


def validate_frames(frames, source):
    """时间必须存在、单调、帧号不重复，否则后面的跟踪结论全部不可信。"""
    if not frames:
        raise SystemExit("frames_empty: %s 里没有任何帧" % source)
    seen, last = set(), None
    for frame in frames:
        fid = frame["frame_id"]
        if fid in seen:
            raise SystemExit("frame_duplicated: %s 重复出现" % fid)
        seen.add(fid)
        if last is not None and frame["timestamp_seconds"] < last:
            raise SystemExit("timestamp_not_monotonic: %s 时间倒退（%.3f < %.3f）"
                             % (fid, frame["timestamp_seconds"], last))
        last = frame["timestamp_seconds"]
    return frames


def load_frames(path, sample_interval, min_conf):
    """读取逐帧检测，按时间排序；低分框保留给 ByteTrack 的第二阶段。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    frames = data.get("frames") if isinstance(data, dict) else data
    if not isinstance(frames, list):
        raise SystemExit("detections_invalid: frames 不是数组")
    out = []
    for frame in frames:
        if "frame_id" not in frame:
            raise SystemExit("frame_id_missing: 第 %d 条没有 frame_id" % len(out))
        sec = _frame_seconds(frame)
        if sec is None:
            raise SystemExit("timestamp_missing: %s 缺少 timestamp_seconds，"
                             "无法按真实采样间隔跟踪" % frame["frame_id"])
        dets = [d for d in frame.get("detections", [])
                if float(d.get("confidence", d.get("score", 1.0))) >= min_conf]
        out.append({"frame_id": frame["frame_id"],
                    "timestamp_seconds": sec, "detections": dets})
    out.sort(key=lambda f: f["timestamp_seconds"])
    return validate_frames(out, path)


def frames_from_gallery(path, sample_interval, min_conf):
    """旧 gallery 兼容入口。返回 (frames, notes)。

    没有 timestamp_seconds 时只能按帧号 × 请求抽帧间隔推算：这是“请求参数推算值”，
    不是视频里核验过的时间，评测结论里必须单独标注。
    """
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    by, inferred = {}, 0
    for row in rows:
        frame_id = row.get("frame")
        if not frame_id:
            raise SystemExit("gallery_frame_missing: 存在没有 frame 字段的行")
        raw = row.get("timestamp_seconds")
        if raw is None:
            try:
                index = int(str(frame_id).split("_")[1])
            except (IndexError, ValueError) as exc:
                raise SystemExit("gallery_frame_unparsable: 无法从 %s 推算时间" % frame_id) from exc
            sec = (index - 1) * float(sample_interval)
            inferred += 1
        else:
            sec = float(raw)
        item = by.setdefault(frame_id, {"frame_id": frame_id,
                                        "timestamp_seconds": sec, "detections": []})
        conf = float(row.get("conf", 1.0))
        if conf < min_conf:
            continue
        item["detections"].append({
            "bbox": row["box"], "confidence": conf,
            "class_id": 0, "label": row.get("label", "person"), "index": row.get("i"),
        })
    frames = sorted(by.values(), key=lambda f: f["timestamp_seconds"])
    notes = []
    if inferred:
        notes.append("gallery_timestamps_inferred: %d 行缺少 timestamp_seconds，"
                     "时间由帧号×%.2fs 推算，非视频核验值" % (inferred, float(sample_interval)))
    return validate_frames(frames, path), notes


def evaluate(adapter, frames, gt, time_tolerance=0.05):
    """轨迹与人工真值对照。返回 errors 列表与指标。

    真值是按 0.5 fps 标的；D1 用 5/10 fps 复核时帧号对不上，只能按“时间接近 + IoU”
    匹配。time_tolerance 就是这个时间窗，跨采样率对照时必须显式给出。
    """
    errors = []
    gt_frames = []
    for track in gt.get("tracks", []):
        for item in track.get("frames", []):
            gt_frames.append({"gt_id": track["gt_id"], "frame_id": item["frame_id"],
                              "timestamp_seconds": item.get("timestamp_seconds"),
                              "bbox": item["bbox"], "track": track.get("label", "")})
    pred = {}
    pred_by_time = []
    for tid, track in adapter._tracks.items():  # noqa: SLF001 - 同仓评测脚本
        for obs in track.observations:
            pred.setdefault(obs.frame_id, []).append((tid, obs))
            pred_by_time.append((obs.timestamp_seconds, tid, obs))
    pred_by_time.sort(key=lambda p: p[0])

    def candidates(item):
        ts = item.get("timestamp_seconds")
        if ts is None or time_tolerance <= 0:
            return pred.get(item["frame_id"], [])
        return [(tid, obs) for t, tid, obs in pred_by_time
                if abs(t - float(ts)) <= time_tolerance]

    covered = 0
    gt_to_pred = {}
    pred_to_gt = {}
    for item in gt_frames:
        best, best_iou = None, 0.0
        for tid, obs in candidates(item):
            value = _iou(item["bbox"], obs.bbox)
            if value > best_iou:
                best, best_iou = tid, value
        if best is not None and best_iou >= 0.5:
            covered += 1
            gt_to_pred.setdefault(item["gt_id"], []).append((item["frame_id"], best))
            pred_to_gt.setdefault(best, set()).add(item["gt_id"])

    total = len(gt_frames)
    for gt_id, pairs in gt_to_pred.items():
        ids = [tid for _, tid in pairs]
        switches = []
        for i in range(1, len(pairs)):
            if ids[i] != ids[i - 1]:
                switches.append({"gt_id": gt_id, "from_track": ids[i - 1],
                                 "to_track": ids[i], "frame_id": pairs[i][0],
                                 "status": "needs_review",
                                 "note": "IoU 匹配推断的换轨，需人工看帧确认"})
        if switches:
            errors.append({"type": "id_switch_candidate", "gt_id": gt_id,
                           "count": len(switches), "items": switches})
        if len(set(ids)) > 1:
            errors.append({"type": "track_fragmentation", "gt_id": gt_id,
                           "distinct_tracks": sorted(set(ids))})
    for tid, gts in pred_to_gt.items():
        if len(gts) > 1:
            errors.append({"type": "false_merge", "track_id": tid,
                           "gt_ids": sorted(gts), "status": "needs_review"})

    metrics = {
        "gt_frames": total,
        "gt_frames_covered": covered,
        "track_recall": round(covered / total, 4) if total else None,
        "gt_tracks": len(gt.get("tracks", [])),
        "gt_tracks_with_prediction": len(gt_to_pred),
        "predicted_tracks": len(adapter._tracks),  # noqa: SLF001
        "id_switch_candidates": sum(1 for e in errors if e["type"] == "id_switch_candidate"),
        "false_merges": sum(1 for e in errors if e["type"] == "false_merge"),
        "fragmentations": sum(1 for e in errors if e["type"] == "track_fragmentation"),
    }
    return errors, metrics


def main(argv=None):
    parser = argparse.ArgumentParser(description="ByteTrack 单镜头轨迹评测（Commit C）")
    parser.add_argument("--detections", help="逐帧检测 JSON")
    parser.add_argument("--from-gallery", help="现有 gallery.json（冒烟用，conf 有下限）")
    parser.add_argument("--sample-interval", type=float, default=2.0)
    parser.add_argument("--max-gap-seconds", type=float, default=1.5)
    parser.add_argument("--max-missing-frames", type=int, default=3)
    parser.add_argument("--high-conf", type=float, default=0.25,
                        help="ByteTrack 高置信门限；按 D-FINE 分数分布校准（p50≈0.29）")
    parser.add_argument("--activation", type=float, default=0.20,
                        help="新建轨迹的最低置信度")
    parser.add_argument("--min-iou", type=float, default=0.1)
    parser.add_argument("--min-detection-conf", type=float, default=0.15)
    parser.add_argument("--stage", choices=["f0", "f1", "f2"], default="f0",
                        help="f0 原生；f1 加同帧 IoU 去重；f2 再加碎片归属")
    parser.add_argument("--dedupe-iou", type=float, default=0.5)
    parser.add_argument("--gt", help="人工真值 JSON")
    parser.add_argument("--gt-time-tolerance", type=float, default=0.05,
                        help="按时间匹配真值的窗口（秒）；跨采样率复核时设 0.25~0.4")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    if not args.detections and not args.from_gallery:
        raise SystemExit("需要 --detections 或 --from-gallery")
    notes = []
    if args.detections:
        frames = load_frames(args.detections, args.sample_interval, args.min_detection_conf)
    else:
        frames, notes = frames_from_gallery(
            args.from_gallery, args.sample_interval, args.min_detection_conf)

    adapter = ByteTrackAdapter(
        sample_interval_seconds=args.sample_interval,
        params={
            "max_gap_seconds": args.max_gap_seconds,
            "max_missing_frames": args.max_missing_frames,
            "high_conf_threshold": args.high_conf,
            "activation_threshold": args.activation,
            "minimum_iou_threshold": args.min_iou,
        },
    )
    stats = {"stage": args.stage, "dedupe_dropped": 0, "fragments_merged": 0, "ambiguous": 0}
    prev_boxes = []
    try:
        for frame in frames:
            dets = [dict(d, index=i) for i, d in enumerate(frame["detections"])]
            if args.stage in ("f1", "f2"):
                kept, dropped = dedupe_same_frame(to_detections(dets), args.dedupe_iou)
                stats["dedupe_dropped"] += len(dropped)
                dets = [d.to_dict() for d in kept]
            if args.stage == "f2":
                kept, merged, ambiguous = attribute_fragments(to_detections(dets), prev_boxes)
                stats["fragments_merged"] += len(merged)
                stats["ambiguous"] += len(ambiguous)
                dets = [d.to_dict() for d in kept]
            active = adapter.update(observations_from_detections(
                frame["frame_id"], frame["timestamp_seconds"], dets))
            prev_boxes = [t.observations[-1].bbox for t in active if t.observations]
    except TrackDependencyError as exc:
        raise SystemExit("track_dependency_error: %s" % exc)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "tracks.json"), "w", encoding="utf-8") as fh:
        json.dump(adapter.tracks_json(), fh, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "track_events.json"), "w", encoding="utf-8") as fh:
        json.dump(adapter.events_json(), fh, ensure_ascii=False, indent=2)

    errors = []
    metrics = {}
    if args.gt and os.path.isfile(args.gt):
        with open(args.gt, "r", encoding="utf-8") as fh:
            gt = json.load(fh)
        errors, metrics = evaluate(adapter, frames, gt, args.gt_time_tolerance)
    errors.extend(adapter.duplicate_overlaps())
    warnings = notes + list(adapter._warnings)  # noqa: SLF001
    with open(os.path.join(args.out, "errors.json"), "w", encoding="utf-8") as fh:
        json.dump({"error_count": len(errors), "evaluation": metrics,
                   "preprocessing": stats,
                   "input_notes": notes, "warnings": warnings, "errors": errors},
                  fh, ensure_ascii=False, indent=2)

    summary = adapter.summary()
    summary.update(metrics)
    summary.update({"stage": stats["stage"],
                    "dedupe_dropped": stats["dedupe_dropped"],
                    "fragments_merged": stats["fragments_merged"],
                    "ambiguous": stats["ambiguous"]})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
