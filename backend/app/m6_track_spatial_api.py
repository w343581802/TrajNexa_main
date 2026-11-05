"""TB2：追踪空间观测接口（实验估计）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import m6_track_spatial

router = APIRouter(prefix="/api/v1", tags=["tb-track-spatial"])


class CameraIn(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    hfov_deg: float | None = None
    pitch_deg: float | None = None
    yaw_deg: float | None = None
    height_m: float | None = None
    lon: float | None = None
    lat: float | None = None


class SampleIn(BaseModel):
    t: float | None = None
    box: list[float]


class TrackIn(BaseModel):
    camera: CameraIn
    samples: list[SampleIn]
    kind: str = "vehicle"
    max_range_m: float = 80.0


@router.post("/track-spatial")
def track_spatial(payload: TrackIn) -> dict:
    if not payload.samples:
        raise HTTPException(status_code=422, detail="samples_required")
    return m6_track_spatial.project_track(
        payload.camera.model_dump(),
        [s.model_dump() for s in payload.samples],
        kind=payload.kind,
        max_range_m=payload.max_range_m,
    )
