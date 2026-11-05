"""B8-B: case-scoped tracking run persistence and read API (no inference).

Rules:
- The anchor must be verified by the server: case, observation, clip and camera name
  all come from the database; the browser only supplies IDs and offsets.
- Candidates start as 'candidate_unverified'; HTTP writes cannot promote them.
- Only append-only TrackingReviewEvent rows change identity_status.
- Reads are case-scoped; cross-case lookups return 404 without revealing existence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import m6_obs_clip, m6_recording_source
from app.m4_video import KeyframeError, checked_offset, resolve_case_clip
from app.models import (Case, InspectionReport, MediaAsset, RecordingClip, RecordingSource,
                        TargetElementGroup, TargetElementObject, TargetObservation, TrackingCandidate,
                        TrackingReviewEvent, TrackingRun, TrackingWindow)

RUN_STATUSES = ('queued', 'running', 'awaiting_review', 'budget_exhausted',
                'unresolved', 'completed', 'failed', 'cancelled')
REVIEW_DECISIONS = ('approve', 'reject', 'revoke')
DEFAULT_SCOPE = {'segment_seconds': 6.0, 'gap_seconds': 2.0,
                 'max_windows': 6, 'budget_frames': 3000}
MAX_WINDOWS = 40
MAX_FRAMES = 20000


class TrackingRunError(ValueError):
    """Validation error carrying a stable code; the API maps it to HTTP."""

    def __init__(self, code: str, message: str = ''):
        super().__init__(code)
        self.code = code
        self.message = message or code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_case(db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise TrackingRunError('case_not_found')
    return case


def get_run(db: Session, case_id: str, run_id: str) -> TrackingRun:
    _get_case(db, case_id)
    run = db.get(TrackingRun, run_id)
    if run is None or run.case_id != case_id:
        raise TrackingRunError('tracking_run_not_found')
    return run


def _number(value, *, default, low, high, code):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrackingRunError(code)
    if not (low <= float(value) <= high):
        raise TrackingRunError(code)
    return float(value)


def _direction(payload: dict) -> str:
    value = str(payload.get('direction') or 'forward').strip().lower()
    if value not in ('forward', 'backward'):
        raise TrackingRunError('invalid_direction')
    return value


def create_run(db: Session, case_id: str, payload: dict) -> TrackingRun:
    if not isinstance(payload, dict):
        raise TrackingRunError('invalid_request')
    _get_case(db, case_id)
    observation_id = str(payload.get('observation_id') or '').strip()
    clip_id = str(payload.get('clip_id') or '').strip()
    group_id = str(payload.get('target_group_id') or '').strip()
    if not observation_id and group_id:
        group = db.get(TargetElementGroup, group_id)
        if group is None or group.case_id != case_id:
            raise TrackingRunError('target_group_not_found')
        primary = db.get(TargetElementObject, group.primary_object_id)
        if primary is None or not primary.m4_observation_id:
            raise TrackingRunError('primary_observation_missing',
                                   '主目标还没有独立观测，无法作为追踪起点')
        observation_id = primary.m4_observation_id
    if not observation_id:
        raise TrackingRunError('observation_or_group_required')

    observation = db.get(TargetObservation, observation_id)
    if observation is None or observation.case_id != case_id:
        raise TrackingRunError('observation_not_found')
    bbox = (observation.analysis_json or {}).get('bbox_pixels')
    if not (isinstance(bbox, list) and len(bbox) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox)):
        raise TrackingRunError('observation_bbox_missing')
    media = db.get(MediaAsset, observation.media_id)
    if media is None or media.case_id != case_id:
        raise TrackingRunError('observation_media_not_found')

    if clip_id:
        try:
            clip, _path = resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
        except KeyframeError as exc:
            raise TrackingRunError('clip_' + str(exc).removeprefix('m4c_').removeprefix('clip_')) from None
    else:
        # 用户只导入目标照片：按截图角标识别的时间与摄像头，在案件录像里自动定位
        try:
            clip, offset = m6_obs_clip.resolve_observation_clip(db, case_id, observation)
        except m6_obs_clip.ObservationClipError as exc:
            raise TrackingRunError(exc.code, exc.message) from None
    camera_label = (clip.camera_name or '').strip()
    if not camera_label:
        raise TrackingRunError('clip_camera_missing')
    if not (clip.match_status or '').startswith(('matched', 'suggested')):
        raise TrackingRunError('clip_camera_unverified')

    if clip_id:
        try:
            offset = checked_offset(payload.get('offset_seconds', 0.0), clip)
        except KeyframeError as exc:
            raise TrackingRunError(str(exc).replace('m4c_', 'clip_')) from None
    start_of_day = m6_recording_source.seconds_of_day(clip.start_local)
    if start_of_day is None:
        raise TrackingRunError('clip_time_missing')

    scope = {
        'segment_seconds': _number(payload.get('segment_seconds'), default=6.0,
                                   low=2.0, high=60.0, code='invalid_segment_seconds'),
        'gap_seconds': _number(payload.get('gap_seconds'), default=2.0,
                               low=0.5, high=60.0, code='invalid_gap_seconds'),
        'max_windows': int(_number(payload.get('max_windows'), default=6.0,
                                   low=1, high=MAX_WINDOWS, code='invalid_max_windows')),
        'budget_frames': int(_number(payload.get('budget_frames'), default=3000.0,
                                     low=1, high=MAX_FRAMES, code='invalid_budget_frames')),
        'map_camera_id': str(payload.get('map_camera_id') or '').strip() or None,
        'spatial_revision': None,
        'clock_quality': 'uncalibrated',
        'exploratory': True,
        'direction': _direction(payload),
        'camera_order': [str(v).strip() for v in (payload.get('camera_order') or [])
                         if isinstance(v, str) and 0 < len(v.strip()) <= 120][:12],
        'research_seconds': int(_number(payload.get('research_seconds'), default=120.0,
                                        low=15, high=3600, code='invalid_research_seconds')),
    }
    anchor = {
        'case_id': case_id,
        'observation_id': observation.id,
        'media_id': observation.media_id,
        'target_id': observation.id,
        'bbox_pixels': [int(round(float(v))) for v in bbox],
        'camera_label': camera_label,
        'camera_uuid': clip.camera_uuid,
        'map_camera_id': scope['map_camera_id'],
        'clip_id': clip.id,
        'clip_relpath': clip.relpath,
        'start_local': clip.start_local,
        'start_of_day_seconds': round(start_of_day, 3),
        'offset_in_clip_seconds': offset,
        'anchor_seconds': round(start_of_day + offset, 3),
        'clock_quality': 'uncalibrated',
        'time_source': clip.start_source or 'unknown',
    }
    run = TrackingRun(id=str(uuid4()), case_id=case_id, observation_id=observation.id,
                      status='queued', anchor_json=anchor, scope_json=scope,
                      progress_json={'analyzed_windows': [], 'windows_done': 0,
                                     'frames_used': 0, 'snapshot_schema': 'b8-run-progress/1'},
                      attempt=0)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def list_tracking_cameras(db: Session, case_id: str) -> list[dict]:
    """案件内已导入录像、且摄像头已核实的镜头清单（供地图/时间轴选起点）。"""
    _get_case(db, case_id)
    rows = db.execute(select(RecordingClip).join(
        RecordingSource, RecordingClip.source_id == RecordingSource.id)
        .where(RecordingSource.case_id == case_id,
               RecordingClip.camera_name.is_not(None))).scalars().all()
    grouped: dict[str, dict] = {}
    for clip in rows:
        if not (clip.match_status or '').startswith(('matched', 'suggested')):
            continue
        label = (clip.camera_name or '').strip()
        if not label:
            continue
        key = clip.camera_uuid or label
        item = grouped.setdefault(key, {'camera_uuid': clip.camera_uuid,
                                        'camera_label': label, 'clips': 0,
                                        'first_start': None, 'last_start': None,
                                        'coverage_seconds': 0.0})
        item['clips'] += 1
        item['coverage_seconds'] = round(item['coverage_seconds'] + float(clip.duration_seconds or 0.0), 1)
        if clip.start_local and (item['first_start'] is None or clip.start_local < item['first_start']):
            item['first_start'] = clip.start_local
        if clip.start_local and (item['last_start'] is None or clip.start_local > item['last_start']):
            item['last_start'] = clip.start_local
    return sorted(grouped.values(), key=lambda x: x['camera_label'])


def list_runs(db: Session, case_id: str, limit: int = 50) -> list[TrackingRun]:
    _get_case(db, case_id)
    return list(db.scalars(select(TrackingRun).where(TrackingRun.case_id == case_id)
                           .order_by(TrackingRun.created_at.desc(), TrackingRun.id.desc())
                           .limit(max(1, min(limit, 100)))))


def list_candidates(db: Session, case_id: str, run_id: str) -> list[TrackingCandidate]:
    run = get_run(db, case_id, run_id)
    return list(db.scalars(select(TrackingCandidate)
                           .where(TrackingCandidate.run_id == run.id)
                           .order_by(TrackingCandidate.retrieval_score.desc().nullslast(),
                                     TrackingCandidate.created_at, TrackingCandidate.id)))


def list_windows(db: Session, case_id: str, run_id: str) -> list[TrackingWindow]:
    run = get_run(db, case_id, run_id)
    return list(db.scalars(select(TrackingWindow).where(TrackingWindow.run_id == run.id)
                           .order_by(TrackingWindow.created_at, TrackingWindow.id)))


def list_reviews(db: Session, case_id: str, run_id: str) -> list[TrackingReviewEvent]:
    run = get_run(db, case_id, run_id)
    return list(db.scalars(select(TrackingReviewEvent)
                           .where(TrackingReviewEvent.run_id == run.id)
                           .order_by(TrackingReviewEvent.created_at, TrackingReviewEvent.id)))


def add_review(db: Session, case_id: str, run_id: str, payload: dict) -> TrackingReviewEvent:
    run = get_run(db, case_id, run_id)
    if run.status == 'cancelled':
        raise TrackingRunError('run_cancelled')
    candidate_id = str(payload.get('candidate_id') or '').strip()
    decision = str(payload.get('decision') or '').strip()
    if decision not in REVIEW_DECISIONS:
        raise TrackingRunError('invalid_review_decision')
    candidate = db.get(TrackingCandidate, candidate_id)
    if candidate is None or candidate.run_id != run.id:
        raise TrackingRunError('candidate_not_found')
    reviewer = str(payload.get('reviewer') or '').strip() or 'human_reviewer'
    note = str(payload.get('note') or '').strip()[:500]
    event = TrackingReviewEvent(id=str(uuid4()), run_id=run.id, candidate_id=candidate.id,
                                decision=decision, reviewer=reviewer[:120], note=note)
    db.add(event)
    if decision == 'approve':
        candidate.state = 'confirmed_by_review'
        candidate.identity_status = 'human_confirmed'
        if run.status in ('awaiting_review', 'unresolved', 'completed', 'budget_exhausted'):
            run.status, run.finished_at = 'queued', None
    elif decision == 'reject':
        candidate.state = 'rejected_by_review'
        candidate.identity_status = 'human_rejected'
    else:  # revoke is a new event too; history is never rewritten
        candidate.state = 'weak_candidate'
        candidate.identity_status = 'candidate_unverified'
    db.commit()
    db.refresh(event)
    return event


def extend_research(db: Session, case_id: str, run_id: str, payload: dict) -> TrackingRun:
    """用户手动延长研判时间窗口；只加不减。"""
    run = get_run(db, case_id, run_id)
    if run.status not in ('queued', 'running'):
        raise TrackingRunError('run_not_running')
    extra = _number(payload.get('seconds'), default=60.0, low=15, high=1800,
                    code='invalid_extend_seconds')
    scope = dict(run.scope_json or {})
    scope['research_seconds'] = min(3600, int(scope.get('research_seconds') or 120) + int(extra))
    run.scope_json = scope
    db.commit()
    db.refresh(run)
    return run


def cancel_run(db: Session, case_id: str, run_id: str) -> TrackingRun:
    run = get_run(db, case_id, run_id)
    if run.status in ('queued', 'running'):
        run.status, run.finished_at = 'cancelled', _utcnow()
    elif run.status == 'cancelled':
        pass
    else:
        raise TrackingRunError('run_not_cancellable')
    db.commit()
    db.refresh(run)
    return run


def run_detail(db: Session, run: TrackingRun) -> dict:
    candidate_count = db.scalar(select(func.count()).select_from(TrackingCandidate)
                                .where(TrackingCandidate.run_id == run.id)) or 0
    window_count = db.scalar(select(func.count()).select_from(TrackingWindow)
                             .where(TrackingWindow.run_id == run.id)) or 0
    review_count = db.scalar(select(func.count()).select_from(TrackingReviewEvent)
                             .where(TrackingReviewEvent.run_id == run.id)) or 0
    return {
        'schema': 'b8-tracking-run/1',
        'id': run.id,
        'case_id': run.case_id,
        'observation_id': run.observation_id,
        'status': run.status,
        'attempt': run.attempt,
        'anchor': run.anchor_json or {},
        'scope': run.scope_json or {},
        'progress': run.progress_json or {},
        'candidates': candidate_count,
        'windows': window_count,
        'reviews': review_count,
        'error': {'code': run.error_code, 'message': run.error_message} if run.error_code else None,
        'created_at': run.created_at,
        'started_at': run.started_at,
        'finished_at': run.finished_at,
        'identity_established': False,
        'note': '候选不等于确认；只有人工审核事件能改变 identity_status。',
        'direction': (run.scope_json or {}).get('direction', 'forward'),
        'research_seconds': (run.scope_json or {}).get('research_seconds'),
        'research_started_at': run.started_at,
        'research_deadline': ((run.started_at + timedelta(
            seconds=int((run.scope_json or {}).get('research_seconds') or 120)))
            if run.started_at else None),
        'active': ((run.progress_json or {}).get('active')
                   if run.status in ('queued', 'running') else None),
    }
