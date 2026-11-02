"""M6-S4-4A：从一条已知目标轨迹里挑出最值得人工查看的画面。

用法：
  python scripts/p4s4_keyframes.py --tracks-report /work/mk/s1_ab_best.json \
      --frames /work/s2_1280 --track 41 --k 6 --out /work/mk/s4_4a.json --sheet /work/mk/s4_4a.jpg

对照组（同一批帧里选同样数量）：
  - 均匀取样：等间隔抽 k 帧（"让人从头翻"的替代品）；
  - 只按框大小：像素最多的 k 帧（最直觉的替代方案）。
"""
import argparse
import json
from pathlib import Path

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.m6_keyframe import (DEFAULT_KEYFRAME_PARAMS, score_frames,  # noqa: E402
                             select_keyframes)


def frame_path(frames_dir, frame_id):
    candidates = [Path(frames_dir) / ("%04d.jpg" % (int(frame_id) + 1)),
                  Path(frames_dir) / ("%04d.jpg" % int(frame_id)),
                  Path(frames_dir) / ("%s.jpg" % frame_id)]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("找不到帧 %s" % frame_id)


def sharpness_of(image, bbox, norm_height=160):
    """清晰度必须在**同一尺度**上比较。

    直接对 30 px 的小框算拉普拉斯方差，会比 200 px 的大框高得多（边缘像素占比大），
    结果就是"远处又小又锐"的帧把"近距离清晰"的帧挤掉——第一版正是这么错的。
    """
    x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
    crop = image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    if crop.size == 0:
        return 0.0
    scale = norm_height / max(crop.shape[0], 1)
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), norm_height))
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def render(image, bbox, label, tile_h=320):
    x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
    pad = 20
    crop = image[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
    if crop.size == 0:
        crop = np.zeros((tile_h, 160, 3), np.uint8)
    scale = tile_h / crop.shape[0]
    crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), tile_h))
    canvas = np.zeros((tile_h + 26, max(crop.shape[1], 210), 3), np.uint8)
    canvas[0:tile_h, 0:crop.shape[1]] = crop
    cv2.putText(canvas, label, (3, tile_h + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (0, 255, 255), 1)
    return canvas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracks-report", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--track", type=int, required=True)
    parser.add_argument("--k", type=int, default=6)
    parser.add_argument("--views", help="可选：{frame_id: view} 人工朝向标注")
    parser.add_argument("--groups", help="可选：归组结果；同一候选对象的重复轨迹不计入遮挡")
    parser.add_argument("--out", required=True)
    parser.add_argument("--sheet", required=True)
    args = parser.parse_args()

    tracks = json.loads(Path(args.tracks_report).read_text(encoding="utf-8"))
    tracks = tracks["results"]["bytetrack"]["tracks"]
    target = next(t for t in tracks if t["track_id"] == args.track)
    views = {}
    if args.views:
        views = json.loads(Path(args.views).read_text(encoding="utf-8"))

    # 同一 PhysicalObjectCandidate 的并行轨迹是"同一个人的重复检测"，
    # 不能当成遮挡——否则拥挤度会把自己的重复框算成别人挡住自己。
    same_candidate = {args.track}
    if args.groups:
        groups = json.loads(Path(args.groups).read_text(encoding="utf-8"))["groups"]
        for group in groups:
            if args.track in group:
                same_candidate.update(group)

    others_by_frame = {}
    for track in tracks:
        if track["track_id"] in same_candidate:
            continue
        for obs in track["observations"]:
            others_by_frame.setdefault(str(obs["frame_id"]), []).append(
                [float(v) for v in obs["bbox"]])

    sharpness = {}
    images = {}
    for obs in target["observations"]:
        image = cv2.imread(str(frame_path(args.frames, obs["frame_id"])))
        if image is None:
            continue
        images[str(obs["frame_id"])] = image
        sharpness[str(obs["frame_id"])] = sharpness_of(image, obs["bbox"])

    observations = [o for o in target["observations"] if str(o["frame_id"]) in images]
    for obs in observations:
        obs["frame_height"], obs["frame_width"] = images[str(obs["frame_id"])].shape[:2]

    scored = score_frames(observations, sharpness, others_by_frame, views)
    picked = select_keyframes(scored, k=args.k, min_gap_seconds=0.4)

    # 对照组必须用自己的排序依据：sort_key=None 表示不按综合分重排
    by_size = select_keyframes(sorted(scored, key=lambda s: -s["height_px"]),
                               k=args.k, min_gap_seconds=0.4, sort_key=None)
    step = max(1, len(scored) // args.k)
    uniform = scored[::step][:args.k]

    above_min = [s for s in scored if not s.get("below_min_size")]
    report = {
        "track_id": args.track,
        "frames_above_min_height": len(above_min),
        "no_usable_frame": not above_min,
        "min_height_px": DEFAULT_KEYFRAME_PARAMS["min_height_px"],
        "frames_total": len(scored),
        "k": args.k,
        "picked": picked,
        "baseline_uniform": uniform,
        "baseline_size_only": by_size,
        "all_scores": scored,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    tiles = []
    for name, rows in (("KEY", picked), ("UNI", uniform), ("SIZE", by_size)):
        for item in rows:
            image = images[item["frame_id"]]
            tiles.append(render(image, item["bbox"],
                                "%s f%s h%.0f s%.2f" % (name, item["frame_id"],
                                                        item["height_px"], item["score"])))
    width = max(t.shape[1] for t in tiles)
    height = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, height - t.shape[0], 0, width - t.shape[1],
                                cv2.BORDER_CONSTANT, value=(0, 0, 0)) for t in tiles]
    rows = [np.hstack(tiles[i:i + 6]) for i in range(0, len(tiles), 6)]
    cv2.imwrite(args.sheet, np.vstack(rows))
    print("track %d：%d 帧 → 挑 %d 帧（达到最小可用尺寸 %d 帧）" % (
        args.track, len(scored), len(picked), len(above_min)))
    if not above_min:
        print("  ⚠ 全部帧都低于最小可用高度：不推荐安排人工查看，标为 no_usable_frame")
    for item in picked:
        print("  f%-5s h=%.0f size=%.2f sharp=%.2f outside=%.2f touch=%s crowd=%.2f → %.3f" % (
            item["frame_id"], item["height_px"], item["size"], item["sharpness"],
            item["outside_ratio"], item["border_touch"], item["crowding"], item["score"]))


if __name__ == "__main__":
    main()
