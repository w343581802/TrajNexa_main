"""M6-S5：指定目标离线研判流程（把各实验模块串起来跑一遍）。

  python scripts/p4s5_pipeline.py --tracks-report /work/mk/s1_ab_best.json \
      --frames /work/s2_1280 --reference-track 41 --out /work/mk/s5_report.json

检测+跟踪用的是**已有的 ByteTrack 输出**（该步骤已由前面的门禁单独验证），
本脚本只验证"归组 → 外观比对 → 候选 → 回溯 → 关联证据 → 关键画面 → 报告"能否贯通。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import onnxruntime as ort  # noqa: E402

from app.m6_keyframe import score_frames, select_keyframes  # noqa: E402
from app.m6_pipeline import attach_quality, run_pipeline  # noqa: E402
from app.m6_target_evidence import embed_observations  # noqa: E402

MODEL = "/srv/models/reid/osnet_x0_25_msmt17_b1.onnx"


def frame_reader(frames_dir):
    cache = {}

    def load(frame_id):
        key = int(frame_id)
        if key not in cache:
            cache[key] = cv2.imread(str(Path(frames_dir) / ("%04d.jpg" % (key + 1))))
        return cache[key]

    return load


def sharpness_of(image, bbox, norm_height=160):
    x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
    crop = image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    if crop.size == 0:
        return 0.0
    scale = norm_height / max(crop.shape[0], 1)
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), norm_height))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks-report", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--reference-track", type=int, required=True)
    parser.add_argument("--max-samples", type=int, default=8)
    parser.add_argument("--keyframes", type=int, default=6)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    report = json.loads(Path(args.tracks_report).read_text(encoding="utf-8"))
    tracks = report["results"]["bytetrack"]["tracks"]
    loader = frame_reader(args.frames)
    session = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])

    vectors = {}
    for track in tracks:
        obs = [o for o in track["observations"]
               if float(o["bbox"][3]) - float(o["bbox"][1]) >= 40.0]
        if len(obs) < 3:
            continue
        pairs = embed_observations(obs, loader, session, args.max_samples)
        if pairs:
            vectors[track["track_id"]] = {str(o["frame_id"]): v for o, v in pairs}
    print("已编码 %d 条轨迹" % len(vectors))

    result = run_pipeline(tracks=tracks, frame_vectors=vectors,
                          reference_track=args.reference_track)
    if not result.get("ok"):
        print("流程失败：%s" % result.get("reason"))
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    # 关键画面：只对参考对象里最长的那条轨迹挑
    owner = [t for t in tracks
             if t["track_id"] in {int(x) for x in result["candidates"][0]["candidate"].split("-")}]
    main_track = max(owner, key=lambda t: len(t["observations"]))
    sharp = {}
    images = {}
    for obs in main_track["observations"]:
        image = loader(obs["frame_id"])
        if image is None:
            continue
        images[str(obs["frame_id"])] = image
        sharp[str(obs["frame_id"])] = sharpness_of(image, obs["bbox"])
    observations = [o for o in main_track["observations"] if str(o["frame_id"]) in images]
    for obs in observations:
        obs["frame_height"], obs["frame_width"] = images[str(obs["frame_id"])].shape[:2]
    scored = score_frames(observations, sharp)
    keys = select_keyframes(scored, k=args.keyframes)
    attach_quality(result, keys, "usable" if keys else "unusable")

    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("阶段：", " → ".join(s["stage"] for s in result["stages"]))
    for stage in result["stages"]:
        print("  %-20s %s" % (stage["stage"], stage["detail"]))
    print("候选（前 5）：")
    for item in result["candidates"]:
        print("  #%d %-24s score=%.3f 支撑帧=%d margin=%s → %s" % (
            item["rank"], item["candidate"], item["score"],
            item["supporting_candidate_frames"], item["margin_to_next"], item["status"]))
    print("关联状态：%s；关键画面 %d 帧；联合结论：%s" % (
        result["association_status"], len(keys), result["evidence"]["verdict"]))
    for item in keys:
        print("  f%-5s h=%.0f → %.3f" % (item["frame_id"], item["height_px"], item["score"]))


if __name__ == "__main__":
    main()
