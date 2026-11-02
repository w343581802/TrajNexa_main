"""P1-B-1C-1：车辆轨迹质量约束的离线对照。

输入一个 tracks JSON（`p1b_vehicle_eval` / `p1b_market_scan` 的输出均可），
输出切断异常后的轨迹与一份对照报告：异常数是否归零、真实车辆是否被切得太碎。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.m6_track_quality import DEFAULT_PARAMS, audit_track, refine_tracks  # noqa: E402


def _load(path):
    data = json.load(open(path, "r", encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("tracks", "vehicle_tracks"):
        if key in data:
            return data[key]
    raise SystemExit("无法从 %s 读取轨迹列表" % path)


def main(argv=None):
    parser = argparse.ArgumentParser(description="车辆轨迹质量约束（P1-B-1C-1）")
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--out", required=True, help="切断后的轨迹 JSON")
    parser.add_argument("--report", required=True, help="对照报告 JSON")
    parser.add_argument("--area-max-ratio", type=float, default=DEFAULT_PARAMS["area_max_ratio"])
    parser.add_argument("--center-jump-max", type=float, default=DEFAULT_PARAMS["center_jump_max"])
    parser.add_argument("--min-frames", type=int, default=8, help="统计口径：只看至少这么多帧的轨迹")
    args = parser.parse_args(argv)

    params = dict(DEFAULT_PARAMS,
                  area_max_ratio=args.area_max_ratio,
                  center_jump_max=args.center_jump_max)
    tracks = _load(args.tracks)
    before = [audit_track(t.get("frames") or t.get("observations") or [], params) for t in tracks]
    refined = refine_tracks(tracks, params)
    after = [audit_track(t.get("frames") or [], params) for t in refined["tracks"]]

    def summary(items, min_frames):
        kept = [a for a in items if a["frames"] >= min_frames]
        return {
            "tracks": len(items),
            "tracks_over_min_frames": len(kept),
            "violations": sum(a["violations"] for a in kept),
            "tracks_with_violation": sum(1 for a in kept if a["violations"]),
            "max_area_ratio": round(max([a["area_max_ratio"] for a in kept], default=1.0), 2),
            "max_jump_norm": round(max([a["max_jump_norm"] for a in kept], default=0.0), 2),
        }

    report = {
        "params": params,
        "before": summary(before, args.min_frames),
        "after": summary(after, args.min_frames),
        "splits": len(refined["splits"]),
        "tracks_split": len({s["parent_track_id"] for s in refined["splits"]}),
        "fragments": refined["fragments"],
        "split_reasons": {},
    }
    for split in refined["splits"]:
        for reason in split["reasons"]:
            report["split_reasons"][reason] = report["split_reasons"].get(reason, 0) + 1

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    json.dump({"params": params, "tracks": refined["tracks"]},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"report": report, "splits": refined["splits"][:200]},
              open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
