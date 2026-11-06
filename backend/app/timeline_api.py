"""B8 时间轴接口（V1.1 P0）：卡片聚合与任务事件，只读。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import timeline_projection as projection
from app.db import get_db
from app.recording_api import fail

router = APIRouter(prefix="/api/v1", tags=["b8-timeline"])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except projection.TimelineError as exc:
        status = 404 if exc.code in ("target_not_found", "tracking_run_not_found") else 422
        fail(status, exc.code, exc.message)


@router.get("/cases/{case_id}/targets/{target_id}/timeline")
def target_timeline(case_id: str, target_id: str, db: Session = Depends(get_db)):
    """目标时间轴：anchor / camera_task / track_segment / gap / spatial_observation。"""
    return _guard(projection.build_timeline, db, case_id, target_id)


@router.get("/cases/{case_id}/tracking-runs/{run_id}/events")
def tracking_run_events(case_id: str, run_id: str, after_seq: int = Query(0, ge=0),
                        db: Session = Depends(get_db)):
    """任务事件（增量拉取）：after_seq 之后的新事件。"""
    return _guard(projection.build_events, db, case_id, run_id, after_seq)