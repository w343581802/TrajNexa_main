"""B8 时间轴接口（V1.1 P0）：卡片聚合与任务事件，只读。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import timeline_projection as projection
from app import timeline_view
from app.db import get_db
from app.timeline_camera_directory import camera_directory
from app.timeline_camera_positions import camera_positions
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
    # 区域卡设备映射：Map 台账完全同名才给 uuid；目录不可用时为空，不猜设备。
    return _guard(projection.build_timeline, db, case_id, target_id, camera_directory())


@router.get("/cases/{case_id}/targets/{target_id}/timeline-view")
def target_timeline_view(case_id: str, target_id: str, db: Session = Depends(get_db)):
    """简洁主视图：已确认轨迹 / 待核查线索 / 区域排查；技术卡保留在 /timeline。"""
    return _guard(timeline_view.build_timeline_view, db, case_id, target_id,
                  camera_directory(), camera_positions())


@router.get("/cases/{case_id}/tracking-runs/{run_id}/events")
def tracking_run_events(case_id: str, run_id: str, after_seq: int = Query(0, ge=0),
                        db: Session = Depends(get_db)):
    """任务事件（增量拉取）：after_seq 之后的新事件。"""
    return _guard(projection.build_events, db, case_id, run_id, after_seq)