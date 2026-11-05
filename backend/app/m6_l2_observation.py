"""B8-S3：把一次图像接地点换算为 L2 空间观测，失败即降级，不阻断研判。"""
from __future__ import annotations

import uuid

from app import m6_ground_projection as gp
from app.models import TargetSpatialObservation


def evaluate(*, observation_id, camera_uuid, calibration, pixel, frame_size=None,
             contact=None):
    """返回 L2 结果或带原因降级结果；从不返回精确点当身份证据。"""
    contact = dict(contact or {"kind": "ground_contact", "source": "user_selected"})
    offset = contact.get("uncertainty_offset_m")
    try:
        result = gp.project(calibration, pixel, frame_size=frame_size,
                            contact_offset_m=offset)
    except gp.GroundCalibrationError as exc:
        return {"schema": "b8s-target-spatial/1",
                "source_observation_id": observation_id,
                "camera_uuid": camera_uuid,
                "spatial_level": "L1",
                "ground_point": None,
                "uncertainty_radius_m": None,
                "quality": "region_only_unverified",
                "identity_status": "candidate_unverified",
                "degrade_reason": str(exc),
                "ground_contact": contact,
                "uncertainties": ["calibration_unusable"]}
    result.update({
        "source_observation_id": observation_id,
        "identity_status": "candidate_unverified",
        "ground_contact": contact,
        "grid_origin": calibration.get("world_origin"),
    })
    return result


def persist(db, *, case_id, observation_id, evaluation):
    row = TargetSpatialObservation(
        id=str(uuid.uuid4()), case_id=case_id, observation_id=observation_id,
        camera_uuid=evaluation.get("camera_uuid"),
        calibration_id=evaluation.get("calibration_id"),
        calibration_revision=evaluation.get("calibration_revision"),
        spatial_level=evaluation.get("spatial_level") or "L1",
        quality=evaluation.get("quality") or "region_only_unverified",
        identity_status="candidate_unverified",
        ground_point=evaluation.get("ground_point"),
        uncertainty_radius_m=evaluation.get("uncertainty_radius_m"),
        payload=evaluation)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
