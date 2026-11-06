"""B8-B HTTP: case-scoped persistent tracking runs.

Every route is scoped by case_id. Nothing here runs inference; execution is the
worker loop. No request field can set a candidate to confirmed.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app import m6_run_service as service
from app.m6_run_service import TrackingRunError
from app.models import TrackingCandidate, TrackingCandidateReview
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


def _inside_storage(path: Path) -> bool:
    root = Path(settings.storage_root).resolve()
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _first_frame(run_dir: Path, refs: list, frame_no) -> Path | None:
    """Resolve old and new evidence refs without inventing a frame.

    New runs store tracking_runs/<run>/w_<camera>_<start>/0001.jpg.
    Older rows lost the w_... component, so a unique basename match inside
    the run directory is the only safe fallback.
    """
    root = Path(settings.storage_root)
    if frame_no is not None:
        try:
            number = int(frame_no)
        except (TypeError, ValueError):
            number = None
        if number is not None:
            for name in ('%04d.jpg' % (number + 1), '%04d.jpg' % number,
                         '%04d.jpeg' % (number + 1), '%04d.png' % (number + 1)):
                matches = [p for p in run_dir.rglob(name) if p.is_file()] if run_dir.is_dir() else []
                if len(matches) == 1:
                    return matches[0]
    for ref in refs:
        raw = str(ref or '').replace('\\', '/').lstrip('/')
        if not raw:
            continue
        direct = root / raw
        if _inside_storage(direct) and direct.is_file():
            return direct
        base = Path(raw).name
        matches = [p for p in run_dir.rglob(base) if p.is_file()] if run_dir.is_dir() else []
        if len(matches) == 1:
            return matches[0]
    return None


def _bbox_from(*values) -> list[int] | None:
    for value in values:
        if (isinstance(value, list) and len(value) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
            return [int(round(float(v))) for v in value]
    return None

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


@router.get('/cases/{case_id}/tracking-runs/{run_id}/candidates/{candidate_id}/evidence/image')
def get_candidate_evidence_image(case_id: str, run_id: str, candidate_id: str,
                                 authorized: bool = False,
                                 kind: str = Query('full', pattern='^(full|crop)$'),
                                 db: Session = Depends(get_db)):
    """Serve one saved candidate frame, optionally cropped by the reviewed box.

    This is a read-only evidence view inside the case. It never creates an
    observation or changes identity_status; a crop is only returned when a
    reproducible bbox exists in details_json or the latest visual review.
    """
    if not authorized:
        fail(422, 'evidence-authorization-required', '请确认已获授权使用该案件证据画面。')
    run = _guard(service.get_run, db, case_id, run_id)
    candidate = db.get(TrackingCandidate, candidate_id)
    if candidate is None or candidate.run_id != run.id:
        fail(404, 'candidate_not_found', '该追踪任务下没有这个候选。')
    details = candidate.details_json or {}
    review = db.scalars(select(TrackingCandidateReview)
                        .where(TrackingCandidateReview.candidate_id == candidate.id)
                        .order_by(TrackingCandidateReview.created_at.desc())).first()
    review_json = (review.review_json or {}) if review is not None else {}
    frame_no = details.get('frame_no')
    if frame_no is None:
        frame_no = review_json.get('frame_no')
    refs = list(candidate.evidence_refs or [])
    if details.get('evidence_ref'):
        refs.append(details['evidence_ref'])
    run_dir = Path(settings.storage_root) / 'tracking_runs' / run.id
    path = _first_frame(run_dir, refs, frame_no)
    if path is None:
        fail(404, 'candidate-evidence-missing', '该候选没有可读取的证据帧。')
    if kind == 'full':
        suffix = path.suffix.lower()
        media = 'image/png' if suffix == '.png' else 'image/jpeg'
        headers = {'Cache-Control': 'private, max-age=60',
                   'X-Evidence-Kind': 'full'}
        if frame_no is not None:
            headers['X-Evidence-Frame'] = str(frame_no)
        return Response(path.read_bytes(), media_type=media, headers=headers)

    bbox = _bbox_from(details.get('bbox_pixels'), details.get('bbox'),
                      review_json.get('bbox_pixels'), review_json.get('target_bbox_pixels'))
    if bbox is None:
        fail(409, 'candidate-crop-bbox-missing',
             '该候选没有可复现的目标框，无法裁剪；请查看完整帧。')
    frame = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        fail(409, 'candidate-evidence-decode-failed', '证据帧无法解码。')
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    pad = max(4, int(max(x2 - x1, y2 - y1) * 0.08))
    x1, y1 = max(0, min(x1, x2) - pad), max(0, min(y1, y2) - pad)
    x2, y2 = min(width, max(bbox[0], bbox[2]) + pad), min(height, max(bbox[1], bbox[3]) + pad)
    if x2 <= x1 or y2 <= y1:
        fail(409, 'candidate-crop-outside-frame', '目标框不在证据帧范围内。')
    crop = frame[y1:y2, x1:x2]
    ok, encoded = cv2.imencode('.jpg', crop, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        fail(409, 'candidate-crop-encode-failed', '目标裁剪图生成失败。')
    headers = {'Cache-Control': 'private, max-age=60',
               'X-Evidence-Kind': 'crop',
               'X-Evidence-Bbox': '%d,%d,%d,%d' % (x1, y1, x2, y2)}
    if frame_no is not None:
        headers['X-Evidence-Frame'] = str(frame_no)
    return Response(encoded.tobytes(), media_type='image/jpeg', headers=headers)


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
    result = _guard(service.extend_research, db, case_id, run_id, payload)
    return {**service.run_detail(db, result['run']), 'dispatch': result['dispatch']}


@router.post('/cases/{case_id}/tracking-runs/{run_id}/cancel')
def cancel_tracking_run(case_id: str, run_id: str, db: Session = Depends(get_db)):
    run = _guard(service.cancel_run, db, case_id, run_id)
    return service.run_detail(db, run)
