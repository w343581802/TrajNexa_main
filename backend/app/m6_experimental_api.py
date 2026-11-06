"""SA5：实验估计图层接口（不写正式 L2、不需要审核标定）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import m6_camera_ray_projection as ray
from app.b8_gates import require_experimental_geo_display

router = APIRouter(prefix="/api/v1", tags=["m6-experimental"],
                   dependencies=[Depends(require_experimental_geo_display)])


class CameraProfile(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    hfov_deg: float | None = None
    pitch_deg: float | None = None
    yaw_deg: float | None = None
    height_m: float | None = None
    lon: float | None = None
    lat: float | None = None
    cx: float | None = None
    cy: float | None = None


class PointIn(BaseModel):
    pixel: list[float]
    observation_id: str | None = None


class ProjectionIn(BaseModel):
    camera: CameraProfile
    points: list[PointIn]
    max_range_m: float = 80.0


def build_report(payload: ProjectionIn) -> dict:
    camera = payload.camera.model_dump()
    results, degraded = [], 0
    for index, point in enumerate(payload.points):
        entry = {"observation_id": point.observation_id, "pixel": point.pixel}
        try:
            projection = ray.project(camera, point.pixel, max_range_m=payload.max_range_m)
            entry.update(projection)
            entry["uncertainty_radius_m"] = ray.uncertainty_radius(camera, point.pixel)
        except ray.ProjectionUnavailable as exc:
            degraded += 1
            entry.update({"spatial_level": "L1", "degrade_reason": str(exc)})
        results.append(entry)
    return {"layer": "experimental_estimate", "quality": "experimental_estimate",
            "identity_status": "candidate_unverified",
            "persisted": False,
            "note": "未经审核标定与独立地理真值验证，不写 target_spatial_observations，不作为正式 L2",
            "points": results, "degraded": degraded}


@router.post("/experimental-projection")
def experimental_projection(payload: ProjectionIn) -> dict:
    if not payload.points:
        raise HTTPException(status_code=422, detail="points_required")
    return build_report(payload)
