"""P1-B-1B：真实录像上的人车时序关系评测。

输入：人员轨迹（ByteTrack）、车辆轨迹（ByteTrack + 去重/误检过滤）、车辆二级类型（可选）。
输出：relation_candidates.json / relation_events.json / relation_errors.json。
只生成候选关系，不写库、不更新特征、不做跨镜头。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.m6_person_vehicle_relation import DEFAULT_PARAMS, build_relations  # noqa: E402


def _load(path):
    return json.load(open(path, "r", encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="人车关系候选评测（P1-B-1B）")
    parser.add_argument("--person-tracks", required=True)
    parser.add_argument("--vehicle-tracks", required=True)
    parser.add_argument("--vehicle-types")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-overlap", type=float, default=DEFAULT_PARAMS["min_overlap_ratio"])
    parser.add_argument("--riding-min-frames", type=int, default=DEFAULT_PARAMS["riding_min_frames"])
    parser.add_argument("--approach-frames", type=int, default=DEFAULT_PARAMS["approach_min_frames"])
    args = parser.parse_args(argv)

    params = {"min_overlap_ratio": args.min_overlap,
              "riding_min_frames": args.riding_min_frames,
              "approach_min_frames": args.approach_frames}

    persons = _load(args.person_tracks)["tracks"]
    vehicles = _load(args.vehicle_tracks)["tracks"]
    types = {}
    if args.vehicle_types:
        for item in _load(args.vehicle_types)["tracks"]:
            types[item["track_id"]] = {"vehicle_type": item["vehicle_type"],
                                       "vehicle_type_status": item["vehicle_type_status"]}

    result = build_relations(persons, vehicles, params, types)

    os.makedirs(args.out_dir, exist_ok=True)
    json.dump({"params": params, "candidates": result["candidates"]},
              open(os.path.join(args.out_dir, "relation_candidates.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"events": result["events"]},
              open(os.path.join(args.out_dir, "relation_events.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump({"errors": result["errors"]},
              open(os.path.join(args.out_dir, "relation_errors.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    kinds = {}
    for item in result["candidates"]:
        kinds[item["relation"]] = kinds.get(item["relation"], 0) + 1
    print(json.dumps({
        "person_tracks": len(persons),
        "vehicle_tracks": len(vehicles),
        "candidates": len(result["candidates"]),
        "by_relation": kinds,
        "errors": len(result["errors"]),
        "status": {s: sum(1 for c in result["candidates"] if c["status"] == s)
                   for s in {c["status"] for c in result["candidates"]}},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
