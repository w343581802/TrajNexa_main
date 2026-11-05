"""TB1：追踪结果 → 自动空间观测（实验估计，不写正式 L2）。"""
from __future__ import annotations

from app import m6_camera_ray_projection as ray
from app import m6_ground_contact as ground

RELIABLE_CONTACT = ("estimated",)


def project_track(camera, samples, *, kind="vehicle", max_range_m=80.0):
    """samples: [{"t": sec, "box": [x1,y1,x2,y2]}] → 观测序列与汇总。"""
    points = []
    for sample in samples or []:
        record = {"t": sample.get("t"), "box": sample.get("box")}
        try:
            contact = ground.from_box(
                sample.get("box"), kind,
                frame_width=camera.get("width"), frame_height=camera.get("height"),
            )
        except ground.ContactUnavailable as exc:
            record.update({"spatial_level": "L1", "degrade_reason": str(exc)})
            points.append(record)
            continue
        record.update({"pixel": contact["contact_pixel"],
                       "contact_quality": contact["quality"],
                       "contact_uncertainties": contact["uncertainties"]})
        try:
            projection = ray.project(camera, contact["contact_pixel"], max_range_m=max_range_m)
            record.update(projection)
            record["uncertainty_radius_m"] = ray.uncertainty_radius(camera, contact["contact_pixel"])
        except ray.ProjectionUnavailable as exc:
            record.update({"spatial_level": "L1", "degrade_reason": str(exc)})
        points.append(record)
    projected = [p for p in points if p.get("spatial_level") == "L2-candidate"]
    reliable = [p for p in projected if p.get("contact_quality") in RELIABLE_CONTACT]
    return {
        "layer": "experimental_estimate",
        "quality": "experimental_estimate",
        "identity_status": "candidate_unverified",
        "persisted": False,
        "note": "追踪框推得的实验估计点；未经审核标定与独立真值验证，不写正式 L2，不作为身份证据",
        "points": points,
        "summary": {
            "total": len(points),
            "projected": len(projected),
            "degraded": len(points) - len(projected),
            "reliable_contact": len(reliable),
        },
    }
