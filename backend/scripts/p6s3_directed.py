"""S6 定向连续追踪演示：已确认观测 → 空间通道 + 通行时间 → 只分析该窗口。

用法（容器内）：
  python scripts/p6s3_directed.py --anchor-camera 2-2 --anchor-track 111 \
      --anchor-exit 8.6 --camera 2-1:/work/mk/s6b/tracks_2-1.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.m6_directed import (camera_link, candidates_in_window, plan_windows,
                             target_profile)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor-camera", required=True)
    ap.add_argument("--anchor-track", type=int, required=True)
    ap.add_argument("--anchor-enter", type=float, required=True)
    ap.add_argument("--anchor-exit", type=float, required=True)
    ap.add_argument("--exit-side", default=None)
    ap.add_argument("--camera", action="append", required=True,
                    help="摄像头:轨迹文件")
    ap.add_argument("--travel", default="2,10")
    args = ap.parse_args()

    targets = {}
    for spec in args.camera:
        cam, path = spec.split(":", 1)
        with open(path, encoding="utf-8") as fh:
            targets[cam] = json.load(fh)["results"]["bytetrack"]["tracks"]

    low, high = (float(v) for v in args.travel.split(","))
    links = [camera_link("L-%s-%s" % (args.anchor_camera, cam), args.anchor_camera, cam,
                         (low, high)) for cam in targets]
    profile = target_profile(args.anchor_camera, args.anchor_track, args.anchor_enter,
                            args.anchor_exit, exit_side=args.exit_side)
    plans = plan_windows(profile, links)
    print("锚点 %s #%d 离开于 %.1f s" % (args.anchor_camera, args.anchor_track, args.anchor_exit))
    for plan in plans:
        cam = plan["camera"]
        tracks = targets[cam]
        picked = candidates_in_window(tracks, plan["window"])
        print("→ %s 窗口 %.1f–%.1f s（%s）" % (cam, plan["window"][0], plan["window"][1], plan["reason"]))
        print("   全量候选 %d 条 → 窗口内 %d 条（收缩 %.1f%%）" % (
            len(tracks), len(picked),
            100.0 * (1 - len(picked) / max(1, len(tracks)))))
        print("   窗口内轨迹：#" + ", #".join(str(c["track"]["track_id"]) for c in picked[:12]))


if __name__ == "__main__":
    main()
