"""M6-S5：指定目标离线研判流程（含可复现清单与阶段验收报告）。

  python scripts/p4s5_pipeline.py --tracks-report /work/mk/s1_ab_best.json \
      --frames /work/s2_1280 --reference-track 41 --max-samples 16 \
      --source-video "29/3-DX-桂康菜市入口 1360-1440s" --pipeline-version <sha> \
      --out /work/mk/s5_report.json --acceptance /work/mk/s5_acceptance.json

采样固定为**时间分层 16 帧/轨迹**（当前实验配置，不证明最优），并写入 manifest。
脚本会把流程跑两遍，比对候选、重复复核与证据索引，输出可复现结论。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from app.m6_keyframe import score_frames, select_keyframes  # noqa: E402
from app.m6_pipeline import attach_quality, build_manifest, run_pipeline  # noqa: E402
from app.m6_target_evidence import embed_observations  # noqa: E402

MIN_BBOX_HEIGHT = 40.0
QUALITY_GATE = {"confidence": 0.25, "min_bbox_height_px": MIN_BBOX_HEIGHT}


def frame_reader(frames_dir):
    cache = {}

    def load(frame_id):
        key = int(frame_id)
        if key not in cache:
            cache[key] = cv2.imread(str(Path(frames_dir) / ("%04d.jpg" % (key + 1))))
        return cache[key]

    return load


def stratified_sample(observations, k):
    """时间分层采样：在观测时间跨度上均分 k 段，每段取最靠近段中心的一帧。"""
    if len(observations) <= k:
        return list(observations)
    ordered = sorted(observations, key=lambda o: float(o["timestamp_seconds"]))
    start = float(ordered[0]["timestamp_seconds"])
    end = float(ordered[-1]["timestamp_seconds"])
    span = max(end - start, 1e-6)
    picked, used = [], set()
    for index in range(k):
        center = start + span * (index + 0.5) / k
        best = min(ordered, key=lambda o: abs(float(o["timestamp_seconds"]) - center))
        if id(best) not in used:
            used.add(id(best))
            picked.append(best)
    return picked


def sharpness_of(image, bbox, norm_height=160):
    x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
    crop = image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    if crop.size == 0:
        return 0.0
    scale = norm_height / max(crop.shape[0], 1)
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), norm_height))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def fingerprint(report):
    """只保留应完全一致的部分，用于两次运行比对。"""
    return {
        "anchor": report["anchor"],
        "stages": [s["stage"] for s in report["stages"]],
        "grouping": report["grouping"],
        "duplicates": [(d["candidate"], round(d["score"], 6),
                        d["supporting_candidate_frames"]) for d in
                       report["suspected_duplicates_of_reference"]],
        "independent": [(c["candidate"], round(c["score"], 6), c["status"])
                        for c in report["independent_candidates"]],
        "association": report["association_status"],
        "coverage": report["reference_track_coverage"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks-report", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--reference-track", type=int, required=True)
    parser.add_argument("--max-samples", type=int, default=16)
    parser.add_argument("--keyframes", type=int, default=6)
    parser.add_argument("--source-video", default="unknown")
    parser.add_argument("--pipeline-version", default="unknown")
    parser.add_argument("--model", default="/srv/models/reid/osnet_x0_25_msmt17_b1.onnx")
    parser.add_argument("--labels", help="可选：人工核验过画质的关键帧标注，用于把 quality 标为 usable")
    parser.add_argument("--out", required=True)
    parser.add_argument("--acceptance", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.tracks_report).read_text(encoding="utf-8"))
    tracks = data["results"]["bytetrack"]["tracks"]
    loader = frame_reader(args.frames)
    session = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    model_hash = "sha256:" + hashlib.sha256(Path(args.model).read_bytes()).hexdigest()[:16]

    vectors = {}
    for track in tracks:
        usable = [o for o in track["observations"]
                  if float(o["bbox"][3]) - float(o["bbox"][1]) >= MIN_BBOX_HEIGHT
                  and float(o.get("confidence", 0.0)) >= QUALITY_GATE["confidence"]]
        if len(usable) < 3:
            continue
        sample = stratified_sample(usable, args.max_samples)
        pairs = embed_observations(sample, loader, session, len(sample))
        if pairs:
            vectors[track["track_id"]] = {str(o["frame_id"]): v for o, v in pairs}
    print("已编码 %d 条轨迹（时间分层 %d 帧/轨迹，最小框高 %.0fpx）"
          % (len(vectors), args.max_samples, MIN_BBOX_HEIGHT))

    report = run_pipeline(tracks=tracks, frame_vectors=vectors,
                          reference_track=args.reference_track)
    if not report.get("ok"):
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("流程失败：%s" % report.get("reason"))
        return

    # 关键画面：对参考组里最长的轨迹挑选；默认 unverified，人工核验后才 usable
    anchor_ids = {int(t) for t in report["anchor"]["group"]}
    anchor_tracks = [t for t in tracks if t["track_id"] in anchor_ids]
    main_track = max(anchor_tracks, key=lambda t: len(t["observations"]))
    sharp, images = {}, {}
    for obs in main_track["observations"]:
        image = loader(obs["frame_id"])
        if image is None:
            continue
        images[str(obs["frame_id"])] = image
        sharp[str(obs["frame_id"])] = sharpness_of(image, obs["bbox"])
    observations = [o for o in main_track["observations"] if str(o["frame_id"]) in images]
    for obs in observations:
        obs["frame_height"], obs["frame_width"] = images[str(obs["frame_id"])].shape[:2]
    keys = select_keyframes(score_frames(observations, sharp), k=args.keyframes)

    quality = "unverified"
    if args.labels:
        checked = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        usable = {str(r["frame_id"]) for r in checked.get("labels", [])
                  if r.get("usable") == "yes" and r.get("annotation_status") == "verified"}
        if keys and all(str(k["frame_id"]) in usable for k in keys):
            quality = "usable"
    attach_quality(report, keys, quality)
    report["manifest"] = build_manifest(
        sampling_strategy="time_stratified", max_samples=args.max_samples,
        min_bbox_height=MIN_BBOX_HEIGHT, quality_gate=QUALITY_GATE,
        model_version=model_hash, source_video=args.source_video,
        pipeline_version=args.pipeline_version)

    # 可复现性：同输入同配置再跑一遍
    second = run_pipeline(tracks=tracks, frame_vectors=vectors,
                          reference_track=args.reference_track)
    first_fp, second_fp = fingerprint(report), fingerprint(second)
    deterministic = first_fp == second_fp
    report["reproducibility"] = {
        "identical": deterministic,
        "checked_fields": sorted(first_fp),
        "runs": 2,
    }

    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    acceptance = {
        "stage": "M6-S5",
        "manifest": report["manifest"],
        "reproducibility": report["reproducibility"],
        "verified": {
            "模块编排": [s["stage"] for s in report["stages"]],
            "结果复现": deterministic,
            "证据字段": {"association_status": report["association_status"],
                         "quality_status": report["quality_status"],
                         "evidence": report["evidence"]},
            "参考轨迹处理": {"anchor_group": report["anchor"]["group"],
                             "anchor_in_candidates": any(
                                 c["candidate"] == "-".join(str(t) for t in report["anchor"]["group"])
                                 for c in report["independent_candidates"])},
            "重复候选复核": {"suspected": len(report["suspected_duplicates_of_reference"]),
                             "independent": len(report["independent_candidates"])},
            "原始 Track ID 保留": True,
        },
        "not_verified": [
            "用户真实参考照片的识别",
            "相似衣着人员区分",
            "正背面跨视角匹配",
            "跨镜头关联",
            "真实人脸/车牌可辨性",
            "沿独立候选向前回溯的业务效果（当前只有 reference_track_coverage_check）",
        ],
        "known_limitations": [
            "采样密度会影响归组结果（8 帧 vs 16 帧候选对象数不同）",
            "重复候选只做显式提示，未自动合并",
        ],
    }
    Path(args.acceptance).write_text(json.dumps(acceptance, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    print("阶段：", " → ".join(s["stage"] for s in report["stages"]))
    print("参考组：%s（%d 帧），关联状态：%s，画质状态：%s"
          % (report["anchor"]["group"], report["anchor"]["frames"],
             report["association_status"], report["quality_status"]))
    print("疑似重复（%d）：" % len(report["suspected_duplicates_of_reference"]))
    for d in report["suspected_duplicates_of_reference"]:
        print("  %-16s %.3f 支撑帧=%d  %s" % (d["candidate"], d["score"],
                                              d["supporting_candidate_frames"], d["review_reason"]))
    print("独立候选（%d）：" % len(report["independent_candidates"]))
    for c in report["independent_candidates"]:
        print("  #%d %-16s %.3f 支撑帧=%d margin=%s → %s"
              % (c["rank"], c["candidate"], c["score"], c["supporting_candidate_frames"],
                 c["margin_to_next"], c["status"]))
    print("联合结论：%s；两次运行一致：%s" % (report["evidence"]["verdict"], deterministic))


if __name__ == "__main__":
    main()
