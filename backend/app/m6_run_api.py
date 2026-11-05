"""B8-B HTTP: case-scoped persistent tracking runs.

Every route is scoped by case_id. Nothing here runs inference; execution is the
worker loop. No request field can set a candidate to confirmed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app import m6_run_service as service
from app.m6_run_service import TrackingRunError
from app.models import TrackingCandidateReview
from app.recording_api import fail

router = APIRouter(prefix='/api/v1')


def _ok(payload: dict) -> dict:
    return payload


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TrackingRunError as exc:
        status = 404 if exc.code in ('case_not_found', 'tracking_run_not_found',
                                    'observation_not_found', 'candidate_not_found',
                                    'observation_media_not_found') else 409 if exc.code in (
            'run_cancelled', 'run_not_cancellable') else 422
        fail(status, exc.code, exc.message)


def _candidate_out(row) -> dict:
    return {
        'id': row.id, 'camera_label': row.camera_label,
        'map_camera_id': row.map_camera_id, 'track_id': row.track_id,
        'state': row.state, 'identity_status': row.identity_status,
        'retrieval_score': row.retrieval_score,
        'margin_top1_top2': row.margin_top1_top2,
        'window': [row.window_start, row.window_end],
        'evidence_refs': row.evidence_refs or [],
        # B8-D 预留：缩略图/证据预览地址由后端生成，前端不拼文件路径。
        # 现在固定为 null，地图/时间轴先显示占位，接入图片服务后只改这里。
        'thumbnail_url': None,
        'evidence_preview': None,
        'details': row.details_json or {},
        'review_required': True, 'created_at': row.created_at,
    }


def _window_out(row) -> dict:
    return {
        'id': row.id, 'camera_label': row.camera_label, 'clip_id': row.clip_id,
        'window': [row.window_start, row.window_end], 'status': row.status,
        'tracks': row.tracks, 'cost': row.cost_json or {}, 'note': row.note,
        'thumbnail_url': None, 'clip_preview': None,
        'created_at': row.created_at,
    }


def _visual_review_out(row) -> dict:
    return {'id': row.id, 'overall': row.overall, 'model': row.model,
            'prompt_sha256': row.prompt_sha256, 'error_code': row.error_code,
            'review': row.review_json or {}, 'created_at': row.created_at}


def _review_out(row) -> dict:
    return {
        'id': row.id, 'candidate_id': row.candidate_id, 'decision': row.decision,
        'reviewer': row.reviewer, 'note': row.note, 'created_at': row.created_at,
    }


@router.get('/cases/{case_id}/tracking-cameras')
def list_tracking_cameras(case_id: str, db: Session = Depends(get_db)):
    return _guard(service.list_tracking_cameras, db, case_id)


@router.post('/cases/{case_id}/tracking-runs', status_code=201)
def create_tracking_run(case_id: str, payload: dict, db: Session = Depends(get_db)):
    run = _guard(service.create_run, db, case_id, payload)
    return service.run_detail(db, run)


@router.get('/cases/{case_id}/tracking-runs')
def list_tracking_runs(case_id: str, limit: int = Query(50, ge=1, le=100),
                       db: Session = Depends(get_db)):
    rows = _guard(service.list_runs, db, case_id, limit)
    return [_ok(service.run_detail(db, r)) for r in rows]


@router.get('/cases/{case_id}/tracking-runs/{run_id}')
def get_tracking_run(case_id: str, run_id: str, db: Session = Depends(get_db)):
    run = _guard(service.get_run, db, case_id, run_id)
    return service.run_detail(db, run)


@router.get('/cases/{case_id}/tracking-runs/{run_id}/candidates')
def list_tracking_candidates(case_id: str, run_id: str, db: Session = Depends(get_db)):
    rows = _guard(service.list_candidates, db, case_id, run_id)
    reviews = {}
    for review in db.scalars(select(TrackingCandidateReview)
                             .where(TrackingCandidateReview.run_id == run_id)
                             .order_by(TrackingCandidateReview.created_at)).all():
        reviews[review.candidate_id] = review
    return [{**_candidate_out(r),
             'visual_review': (_visual_review_out(reviews[r.id]) if r.id in reviews else None)}
            for r in rows]


@router.get('/cases/{case_id}/tracking-runs/{run_id}/windows')
def list_tracking_windows(case_id: str, run_id: str, db: Session = Depends(get_db)):
    rows = _guard(service.list_windows, db, case_id, run_id)
    return [_window_out(r) for r in rows]


@router.get('/cases/{case_id}/tracking-runs/{run_id}/reviews')
def list_tracking_reviews(case_id: str, run_id: str, db: Session = Depends(get_db)):
    rows = _guard(service.list_reviews, db, case_id, run_id)
    return [_review_out(r) for r in rows]


@router.post('/cases/{case_id}/tracking-runs/{run_id}/reviews', status_code=201)
def add_tracking_review(case_id: str, run_id: str, payload: dict,
                        db: Session = Depends(get_db)):
    row = _guard(service.add_review, db, case_id, run_id, payload)
    run = _guard(service.get_run, db, case_id, run_id)
    return {'review': _review_out(row), 'run': service.run_detail(db, run)}


@router.post('/cases/{case_id}/tracking-runs/{run_id}/extend')
def extend_tracking_run(case_id: str, run_id: str, payload: dict,
                        db: Session = Depends(get_db)):
    run = _guard(service.extend_research, db, case_id, run_id, payload)
    return service.run_detail(db, run)


@router.post('/cases/{case_id}/tracking-runs/{run_id}/cancel')
def cancel_tracking_run(case_id: str, run_id: str, db: Session = Depends(get_db)):
    run = _guard(service.cancel_run, db, case_id, run_id)
    return service.run_detail(db, run)
