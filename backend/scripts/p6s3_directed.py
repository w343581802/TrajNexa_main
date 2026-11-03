"""S6 定向连续追踪演示：已确认观测 → 空间通道 + 通行时间 → 只分析该窗口。

所有时间先按各摄像头首帧 OSD 锚点换算成绝对秒，再做窗口计算。
用法（容器内）：
  python scripts/p6s3_directed.py --anchor-camera 2-2 --anchor-track 111 \
      --camera 2-2:/work/mk/s6b/tracks_2-2.json:53997 \
      --camera 2-1:/work/mk/s6b/tracks_2-1.json:53998 \
      --camera 1-1:/work/mk/s6b/tracks_1-1.json:53997 --direction both
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.m6_directed import (absolute_track, camera_link, candidates_in_window,
                             plan_windows, target_profile)


def clock(seconds):
    total = float(seconds) % 86400
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    return "%02d:%02d:%05.2f" % (hours, minutes, total % 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor-camera", required=True)
    ap.add_argument("--anchor-track", type=int, required=True)
    ap.add_argument("--exit-side", default=None)
    ap.add_argument("--camera", action="append", required=True,
                    help="摄像头:轨迹文件:首帧OSD锚点")
    ap.add_argument("--travel", default="2,10")
    ap.add_argument("--direction", default="both", choices=["forward", "backward", "both"])
    args = ap.parse_args()

    tracks_by_cam, anchor_obs = {}, None
    for spec in args.camera:
        cam, path, anchor = spec.rsplit(":", 2)
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)["results"]["bytetrack"]["tracks"]
        tracks_by_cam[cam] = [absolute_track(t, float(anchor)) for t in raw]
        if cam == args.anchor_camera:
            for track in tracks_by_cam[cam]:
                if track["track_id"] == args.anchor_track:
                    obs = track["observations"]
                    anchor_obs = (obs[0]["absolute_seconds"], obs[-1]["absolute_seconds"])

    if anchor_obs is None:
        raise SystemExit("找不到锚点轨迹")
    low, high = (float(v) for v in args.travel.split(","))
    links = [camera_link("L-%s-%s" % (args.anchor_camera, cam), args.anchor_camera, cam,
                         (low, high), exit_side=args.exit_side, direction=args.direction)
             for cam in tracks_by_cam if cam != args.anchor_camera]
    profile = target_profile(args.anchor_camera, args.anchor_track, anchor_obs[0], anchor_obs[1],
                            exit_side=args.exit_side)
    print("锚点 %s #%d 绝对观测 %s – %s（%.1f s）" % (
        args.anchor_camera, args.anchor_track, clock(anchor_obs[0]), clock(anchor_obs[1]),
        anchor_obs[1] - anchor_obs[0]))
    for plan in plan_windows(profile, links):
        cam = plan["camera"]
        tracks = tracks_by_cam[cam]
        picked = candidates_in_window(tracks, plan["window"])
        start, end = plan["window"]
        print("→ %s %s 窗口 %s–%s（%.1f–%.1f s）" % (
            cam, plan["mode"], clock(start), clock(end), start, end))
        print("   全量 %d 条 → 窗口内 %d 条（收缩 %.1f%%）  候选：#%s" % (
            len(tracks), len(picked), 100.0 * (1 - len(picked) / max(1, len(tracks))),
            ", #".join(str(c["track"]["track_id"]) for c in picked[:10])))


if __name__ == "__main__":
    main()
