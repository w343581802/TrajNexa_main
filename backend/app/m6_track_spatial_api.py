"""TB2：追踪空间观测接口（实验估计）。"""
import json
from pathlib import Path

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

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "tb_samples" / "pilot_20261023_track.json"


@router.get("/track-spatial/sample")
def track_spatial_sample() -> dict:
    """真实片段上跑出的追踪框（2 fps、60 帧内）→ 实时投影，作为实验估计样本。"""
    if not SAMPLE_PATH.exists():
        raise HTTPException(status_code=404, detail="track_sample_unavailable")
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    report = m6_track_spatial.project_track(
        payload["camera"], payload["samples"], kind="vehicle", max_range_m=80.0,
    )
    report["sample"] = {k: payload.get(k) for k in ("clip", "fps", "method", "note", "frame_width", "frame_height")}
    report["sample"]["count"] = len(payload["samples"])
    return report
