"""M6-S6-0：轨迹候选总览图（用于人工挑选锚点目标）。

  python scripts/p6s0_track_sheet.py --frames /work/mk/s6/1-1 \
      --tracks /work/mk/s6/tracks_1-1.json --top 12 --out /work/mk/s6/sheet_1-1.jpg

按"中位框高 × 轨迹长度"排序，每行一条轨迹的首/中/尾三帧裁剪，标出 ID 与尺寸。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def render(image, bbox, label, height=200):
    x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        crop = np.zeros((height, 100, 3), dtype=np.uint8)
    scale = height / max(crop.shape[0], 1)
    crop = cv2.resize(crop, (max(20, int(crop.shape[1] * scale)), height))
    canvas = np.zeros((height + 22, max(crop.shape[1], 260), 3), dtype=np.uint8)
    canvas[:height, :crop.shape[1]] = crop
    cv2.putText(canvas, label, (4, height + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (255, 255, 255), 1, cv2.LINE_AA)
    return canvas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", required=True)
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--min-observations", type=int, default=15)
    parser.add_argument("--min-height", type=float, default=70)
    parser.add_argument("--track-ids", help="只渲染这些轨迹 ID，逗号分隔")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.tracks).read_text(encoding="utf-8"))
    tracks = data["results"]["bytetrack"]["tracks"]

    def load(frame_id):
        return cv2.imread(str(Path(args.frames) / ("%04d.jpg" % (int(frame_id) + 1))))

    wanted = {int(x) for x in args.track_ids.split(",")} if args.track_ids else None
    scored = []
    for track in tracks:
        obs = track["observations"]
        if wanted is not None:
            if track["track_id"] not in wanted:
                continue
        elif len(obs) < args.min_observations:
            continue
        heights = sorted(o["bbox"][3] - o["bbox"][1] for o in obs)
        median_h = heights[len(heights) // 2]
        if wanted is None and median_h < args.min_height:
            continue
        scored.append((median_h * (len(obs) ** 0.5), track, median_h))
    scored.sort(key=lambda item: -item[0])

    rows = []
    for _, track, median_h in scored[: args.top]:
        obs = track["observations"]
        picks = [obs[0], obs[len(obs) // 2], obs[-1]]
        cells = []
        for o in picks:
            image = load(o["frame_id"])
            if image is None:
                continue
            cells.append(render(image, o["bbox"], "#%d f%s h=%.0f" % (
                track["track_id"], o["frame_id"], o["bbox"][3] - o["bbox"][1])))
        if cells:
            rows.append(np.hstack(cells))
    if not rows:
        raise SystemExit("没有符合条件的轨迹")
    width = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0))) for r in rows]
    cv2.imwrite(args.out, np.vstack(rows))
    print("已输出 %d 条轨迹到 %s" % (len(rows), args.out))
    for _, track, median_h in scored[: args.top]:
        print("  #%d 帧数=%d 中位高=%.0f" % (track["track_id"], len(track["observations"]), median_h))


if __name__ == "__main__":
    main()
