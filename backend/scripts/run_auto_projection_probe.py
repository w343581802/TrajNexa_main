"""B8-SA1 探针：给定相机参数与像素点，输出实验估计位置或明确降级原因。

用法：
  python run_auto_projection_probe.py camera.json --points "622,497;720,503"
  python run_auto_projection_probe.py camera.json --points "..." --hfov 70
"""
from __future__ import annotations

import argparse
import json
import sys

from app import m6_camera_ray_projection as ray


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("camera")
    ap.add_argument("--points", required=True)
    ap.add_argument("--hfov", type=float, default=None)
    ap.add_argument("--max-range", type=float, default=ray.MAX_RANGE_M)
    args = ap.parse_args(argv[1:])

    with open(args.camera, "r", encoding="utf-8") as handle:
        camera = json.load(handle)
    if args.hfov is not None:
        camera["hfov_deg"] = args.hfov

    report = {"camera_uuid": camera.get("camera_uuid"), "source": "reported_profile",
              "hfov_source": "cli" if args.hfov is not None else camera.get("hfov_source"),
              "points": []}
    for item in args.points.split(";"):
        if not item.strip():
            continue
        u, v = (float(x) for x in item.split(","))
        entry = {"pixel": [u, v]}
        try:
            result = ray.project(camera, [u, v], max_range_m=args.max_range)
            entry.update(result)
            entry["uncertainty_radius_m"] = ray.uncertainty_radius(camera, [u, v])
        except ray.ProjectionUnavailable as exc:
            entry.update({"spatial_level": "L1", "degrade_reason": str(exc)})
        report["points"].append(entry)
    ok = sum(1 for p in report["points"] if p.get("spatial_level") == "L2-candidate")
    report["summary"] = {"total": len(report["points"]), "projected": ok,
                         "degraded": len(report["points"]) - ok}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
