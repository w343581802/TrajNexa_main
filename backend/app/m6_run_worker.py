"""B8-B: execute a persistent tracking run against real registered recordings.

The worker never reads a truth package, an exported frame directory or a /work path.
It plans camera windows from case-owned RecordingClip rows, extracts frames from the
actual video file, calls the existing window analyzer, and stores candidates as
unverified. Analyzer and scorer are injectable so the orchestration can be tested
without loading D-FINE or OSNet.
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import m6_recording_source
from app.m6_recording_source import RecordingFrameError
from app import m6_l2_trajectory
from app import map_db_spatial
from app.config import settings
from app.m4_video import resolve_case_clip
from app.m6_run_service import TrackingRunError
from app.m6_target_candidate_adapter import candidates_from_scores
from app.models import (RecordingClip, RecordingSource, TrackingCandidate, TrackingCandidateReview,
                        TrackingRun, TrackingWindow)

IN_PROGRESS_STATUSES = ('queued', 'running')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalyzerUnavailable(RuntimeError):
    """Raised when the real detector/ReID weights are not available in this deployment."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _case_clips(db: Session, case_id: str) -> list[RecordingClip]:
    rows = db.execute(select(RecordingClip, RecordingSource)
                      .join(RecordingSource, RecordingClip.source_id == RecordingSource.id)
                      .where(RecordingSource.case_id == case_id,
                             RecordingClip.camera_name.is_not(None))).all()
    clips = []
    for clip, _source in rows:
        # 未匹配摄像头的段只有 OCR 名称，不能作为“下一台镜头”去分析
        if not (clip.match_status or '').startswith(('matched', 'suggested')):
            continue
        if (clip.camera_name or '').strip() and clip.duration_seconds and clip.start_local:
            clips.append(clip)
    return clips


def _clip_start(clip: RecordingClip) -> float | None:
    from app import m6_recording_source as source
    return source.seconds_of_day(clip.start_local)


def plan_windows(run: TrackingRun, clips: list[RecordingClip], done: set[str],
                spatial_hint: tuple[float, float] | None = None,
                camera_locations: dict[str, tuple[float, float]] | None = None) -> list[dict]:
    """Build camera windows around the anchor; deterministic and budget-aware."""
    anchor = run.anchor_json or {}
    scope = run.scope_json or {}
    anchor_label = anchor.get('camera_label')
    anchor_seconds = float(anchor.get('anchor_seconds') or 0.0)
    seg = float(scope.get('segment_seconds') or 6.0)
    gap = float(scope.get('gap_seconds') or 2.0)
    budget = int(scope.get('max_windows') or 6)
    direction = str(scope.get('direction') or 'forward')
    # 一次执行只推进一台镜头：确认/否决后由任务重新排队再走下一台，
    # 这样“连续追踪”是逐步推进的，不会一口气把预算全花掉。
    remaining = min(budget - len(done), 1)

    by_camera: dict[str, list[RecordingClip]] = {}
    for clip in clips:
        by_camera.setdefault((clip.camera_name or '').strip(), []).append(clip)
    requested = [str(v).strip() for v in (scope.get('camera_order') or []) if isinstance(v, str)]
    if requested:
        order = [label for label in requested if label in by_camera]
        order += sorted(label for label in by_camera if label not in order)
    else:
        order = sorted(by_camera)
        if direction == 'backward':
            order = list(reversed(order))
        if spatial_hint and camera_locations:
            # S4 软优先级：只调整“先查哪台”，不删除镜头；同分保持原有顺序。
            order = sorted(order, key=lambda label: -m6_l2_trajectory.spatial_priority(
                spatial_hint, *camera_locations.get(label, spatial_hint)))
        if anchor_label in order:
            order.remove(anchor_label)
            order.append(anchor_label)  # anchor camera continuation comes last

    plans: list[dict] = []
    for label in order:
        if len(plans) >= remaining:
            break
        if direction == 'backward':
            start = anchor_seconds - gap - seg
        else:
            start = anchor_seconds + gap
        for clip in sorted(by_camera[label], key=lambda c: c.start_local or ''):
            clip_start = _clip_start(clip)
            if clip_start is None:
                continue
            clip_end = clip_start + float(clip.duration_seconds or 0.0)
            if not (clip_start - 0.5 <= start < clip_end):
                continue
            key = '%s@%.1f' % (label, start)
            if key in done:
                break
            end = min(start + seg, clip_end)
            plans.append({'camera_label': label, 'clip_id': clip.id,
                          'clip_relpath': clip.relpath,
                          # 绝对当日秒用于展示与跨镜头比较；seek 必须相对本段录像
                          'start_seconds': round(start, 3),
                          'clip_start_seconds': round(clip_start, 3),
                          'seek_seconds': round(max(0.0, start - clip_start), 3),
                          'duration_seconds': round(max(0.1, end - start), 3),
                          'end_seconds': round(end, 3),
                          'key': key})
            break
    return plans[:budget]


def _store_candidate(db: Session, run: TrackingRun, plan: dict, candidate: dict,
                     frames_refs: list[str], note: str) -> TrackingCandidate | None:
    key = '%s/%s' % (plan['key'], candidate['track_id'])
    existing = db.scalar(select(TrackingCandidate).where(
        TrackingCandidate.run_id == run.id, TrackingCandidate.candidate_key == key))
    if existing is not None:
        return None
    row = TrackingCandidate(
        id=str(uuid4()), run_id=run.id, candidate_key=key,
        camera_label=plan['camera_label'], map_camera_id=(run.anchor_json or {}).get('map_camera_id'),
        track_id=str(candidate['track_id']),
        state=candidate.get('state', 'weak_candidate'),
        identity_status='candidate_unverified',
        retrieval_score=candidate.get('retrieval_score'),
        margin_top1_top2=candidate.get('margin_top1_top2'),
        window_start=plan['start_seconds'], window_end=plan['end_seconds'],
        evidence_refs=frames_refs,
        details_json={'supports': candidate.get('supports', []),
                      'conflicts': candidate.get('conflicts', []),
                      'unknown': candidate.get('unknown', []),
                      'note': note})
    db.add(row)
    return row


def _latest_spatial_hint(db: Session, case_id: str):
    """最近一条 L2/WGS84 估计点；取不到就返回 None，规划保持原基线。"""
    from app.models import TargetSpatialObservation
    row = db.scalars(select(TargetSpatialObservation)
                     .where(TargetSpatialObservation.case_id == case_id,
                            TargetSpatialObservation.spatial_level == 'L2')
                     .order_by(TargetSpatialObservation.created_at.desc()).limit(1)).first()
    ground = getattr(row, 'ground_point', None)
    if not isinstance(ground, dict) or ground.get('crs') != 'EPSG:4326':
        return None
    try:
        return float(ground['x']), float(ground['y'])
    except (KeyError, TypeError, ValueError):
        return None


def _camera_locations() -> dict[str, tuple[float, float]] | None:
    try:
        payload = map_db_spatial.read_map_spatial(dsn=settings.map_db_readonly_url)
    except Exception:
        return None
    result = {}
    for camera in payload.get('cameras') or []:
        location = camera.get('location') or {}
        name = camera.get('display_name')
        if name and location.get('lon') is not None and location.get('lat') is not None:
            result[name] = (float(location['lon']), float(location['lat']))
    return result or None


def _execution_progress(progress, *, started_at, used_before, used_after, executions,
                        windows, status, stop_reason=None):
    """记下这一轮执行的耗时与停止原因；耗时按任务累计，不随重新执行清零。"""
    payload = dict(progress)
    payload['schema'] = 'b8-run-progress/1'
    payload['research_seconds_used'] = round(max(0.0, float(used_after)), 3)
    payload['executions'] = int(executions)
    payload['last_execution'] = {
        'startedAt': started_at.isoformat(),
        'finishedAt': _utcnow().isoformat(),
        'addedSeconds': round(max(0.0, float(used_after) - float(used_before)), 3),
        'cumulativeSeconds': round(max(0.0, float(used_after)), 3),
        'windows': int(windows),
        'status': status,
    }
    if stop_reason:
        payload['stop_reason'] = stop_reason
        payload['last_execution']['stopReason'] = stop_reason
    return payload


def _is_strong_candidate(candidate: dict) -> bool:
    """第 1 名且仍以 candidate 状态保留时先交人审；没有候选才继续查下一台。"""
    return (int(candidate.get('rank') or 1) == 1
            and candidate.get('state') == 'candidate')


def execute_run(db: Session, run_id: str, analyzer=None, fps: float = 5.0,
                storage_root: Path | None = None, clock=None) -> TrackingRun:
    """Run or resume one tracking run; auto-advance until budget or review pause."""
    clock = clock or time.monotonic
    run = db.get(TrackingRun, run_id)
    if run is None:
        raise TrackingRunError('tracking_run_not_found')
    if run.status == 'cancelled':
        return run
    if run.status not in IN_PROGRESS_STATUSES:
        return run
    started_at = _utcnow()
    run.status, run.started_at, run.attempt = 'running', started_at, run.attempt + 1
    run.error_code = run.error_message = None
    db.commit()

    progress = dict(run.progress_json or {})
    done = set(progress.get('analyzed_windows') or [])
    frames_used = int(progress.get('frames_used') or 0)
    scope = run.scope_json or {}
    budget = int(scope.get('budget_frames') or 3000)
    research_budget = float(scope.get('research_seconds') or 120.0)
    used_seconds = float(progress.get('research_seconds_used') or 0.0)
    used_before = used_seconds
    executions = int(progress.get('executions') or 0) + 1
    first_started = progress.get('first_started_at') or started_at.isoformat()
    progress = {**progress, 'first_started_at': first_started,
                'executions': executions}
    stop_reason = None
    paused_for_review = False
    root = Path(storage_root or settings.storage_root) / 'tracking_runs' / run.id

    try:
        clips = _case_clips(db, run.case_id)
        if not clips:
            run.status = 'unresolved'
            progress = _execution_progress(
                progress, started_at=started_at, used_before=used_before,
                used_after=used_seconds, executions=executions, windows=len(done),
                status='unresolved', stop_reason='no_case_recordings')
            progress['reason'] = 'no_case_recordings'
            run.progress_json = progress
            run.finished_at = _utcnow()
            db.commit()
            return run
        if analyzer is None:
            analyzer = RecordingAnalyzer(run, fps=fps)
        hint = _latest_spatial_hint(db, run.case_id)
        locations = _camera_locations() if hint else None
        while True:
            if frames_used >= budget:
                stop_reason = 'frames_budget_exhausted'
                run.status = 'budget_exhausted'
                break
            research_budget = float((run.scope_json or {}).get('research_seconds')
                                    or research_budget)
            if used_seconds >= research_budget:
                stop_reason = 'research_budget_exhausted'
                run.status = 'budget_exhausted'
                break
            plans = plan_windows(run, clips, done, spatial_hint=hint,
                                 camera_locations=locations)
            if not plans:
                stop_reason = 'no_pending_window'
                break
            plan = plans[0]
            out_dir = root / ('w_' + plan['key'].replace('@', '_').replace('.', 'p'))
            if out_dir.exists():
                shutil.rmtree(out_dir)
            # 先落“正在研判”状态，前端据此显示摄像头与计时；失败时也会被清掉
            progress = {**progress, 'active': {
                'camera_label': plan['camera_label'],
                'window': [plan['start_seconds'], plan['end_seconds']],
                'started_at': _utcnow().isoformat(),
            }}
            run.progress_json = progress
            db.commit()
            window_started = clock()
            try:
                result = analyzer.analyze(plan, out_dir)
            finally:
                used_seconds += max(0.0, clock() - window_started)
            frames = int((result.get('cost') or {}).get('frames_saved') or 0)
            window = TrackingWindow(
                id=str(uuid4()), run_id=run.id, camera_label=plan['camera_label'],
                clip_id=plan['clip_id'], window_start=plan['start_seconds'],
                window_end=plan['end_seconds'], status=result.get('status', 'unknown'),
                tracks=len(result.get('tracks') or []), cost_json=result.get('cost') or {},
                note=str(result.get('note') or '')[:500])
            db.add(window)
            tracks = result.get('tracks') or []
            scored = analyzer.score(result) if tracks else []
            refs = list(result.get('evidence_refs') or [])
            strong = False
            if tracks and scored:
                top_k = (int(getattr(settings, 'm6_candidate_review_top_k', 3) or 1)
                         if (isinstance(analyzer, RecordingAnalyzer)
                             and bool(getattr(settings, 'm6_candidate_review', True))) else 1)
                verdict = candidates_from_scores(scored, top_k=max(1, top_k))
                for candidate in verdict.get('target_candidates') or []:
                    row = _store_candidate(db, run, plan, candidate, refs,
                                     'top1=%s margin=%s' % (candidate.get('retrieval_score'),
                                                              candidate.get('margin_top1_top2')))
                    if row is not None:
                        if _is_strong_candidate(candidate):
                            strong = True
                        if top_k > 1:
                            _store_visual_review(db, run, plan, row, candidate, result)
            done.add(plan['key'])
            frames_used += frames
            progress = _execution_progress(
                {**progress, 'active': None,
                 'analyzed_windows': sorted(done),
                 'windows_done': len(done), 'frames_used': frames_used,
                 'last_camera': plan['camera_label'],
                 'last_window': [plan['start_seconds'], plan['end_seconds']]},
                started_at=started_at, used_before=used_before,
                used_after=used_seconds, executions=executions,
                windows=len(done), status='running')
            run.progress_json = progress
            run.status = 'running'
            db.commit()
            if strong:
                paused_for_review = True
                stop_reason = 'candidate_requires_review'
                break

        if run.status == 'running':
            has_candidates = db.scalar(select(TrackingCandidate.id).where(
                TrackingCandidate.run_id == run.id).limit(1)) is not None
            if paused_for_review:
                run.status = 'awaiting_review'
            elif stop_reason in ('frames_budget_exhausted', 'research_budget_exhausted'):
                run.status = 'budget_exhausted'
            elif has_candidates:
                run.status = 'awaiting_review'
            else:
                run.status = 'unresolved'
        progress = _execution_progress(
            dict(run.progress_json or {}), started_at=started_at,
            used_before=used_before, used_after=used_seconds,
            executions=executions, windows=len(done), status=run.status,
            stop_reason=stop_reason)
        run.progress_json = progress
        run.finished_at = _utcnow()
        db.commit()
    except AnalyzerUnavailable as exc:
        db.rollback()
        run = db.get(TrackingRun, run_id)
        run.status, run.finished_at = 'failed', _utcnow()
        run.error_code, run.error_message = exc.code, '模型不可用，任务未产生结论'
        progress = _execution_progress(
            progress, started_at=started_at, used_before=used_before,
            used_after=used_seconds, executions=executions, windows=len(done),
            status='failed', stop_reason='error')
        progress['last_execution']['errorCode'] = exc.code
        run.progress_json = progress
        db.commit()
    except RecordingFrameError as exc:
        db.rollback()
        run = db.get(TrackingRun, run_id)
        run.status, run.finished_at = 'failed', _utcnow()
        run.error_code = exc.code
        run.error_message = str(exc)[:500]
        progress = _execution_progress(
            progress, started_at=started_at, used_before=used_before,
            used_after=used_seconds, executions=executions, windows=len(done),
            status='failed', stop_reason='error')
        progress['last_execution']['errorCode'] = exc.code
        run.progress_json = progress
        db.commit()
    except Exception as exc:  # noqa: BLE001 - worker must persist failure, not crash the loop
        db.rollback()
        run = db.get(TrackingRun, run_id)
        run.status, run.finished_at = 'failed', _utcnow()
        run.error_code, run.error_message = 'tracking_run_failed', str(exc)[:500]
        progress = _execution_progress(
            progress, started_at=started_at, used_before=used_before,
            used_after=used_seconds, executions=executions, windows=len(done),
            status='failed', stop_reason='error')
        progress['last_execution']['errorCode'] = 'tracking_run_failed'
        run.progress_json = progress
        db.commit()
    db.refresh(run)
    return run

class RecordingAnalyzer:
    """Real analyzer: decode registered clips, run window analysis, score with OSNet."""

    def __init__(self, run: TrackingRun, fps: float = 5.0):
        self.run = run
        self.fps = float(fps)
        self._engine = None
        self._reference = None

    def _reid(self):
        if self._engine is None:
            from app import m6_person_reid, model_capabilities
            entry = model_capabilities.resolve('person_reid', task='m6.one_hop')
            try:
                self._engine, _ = m6_person_reid.engine_for(entry['path'])
            except Exception as exc:  # noqa: BLE001
                raise AnalyzerUnavailable('person_reid_unavailable') from exc
            if not self._engine.ready():
                raise AnalyzerUnavailable('person_reid_unavailable')
        return self._engine

    def _anchor_embedding(self):
        if self._reference is not None:
            return self._reference
        import cv2
        from app.models import MediaAsset
        from app.db import SessionLocal
        anchor = self.run.anchor_json or {}
        with SessionLocal() as db:
            media = db.get(MediaAsset, anchor.get('media_id'))
            relpath = media.storage_relpath if media else None
        if not relpath:
            raise AnalyzerUnavailable('anchor_media_missing')
        path = Path(settings.storage_root).resolve() / relpath
        image = cv2.imread(str(path))
        if image is None:
            raise AnalyzerUnavailable('anchor_media_unreadable')
        box = [int(v) for v in (anchor.get('bbox_pixels') or [])]
        if len(box) != 4:
            raise AnalyzerUnavailable('anchor_bbox_missing')
        engine = self._reid()
        valid, embeddings = engine.extract_embeddings(image, [box])
        if not valid:
            raise AnalyzerUnavailable('anchor_embedding_failed')
        self._reference = embeddings[0]
        return self._reference

    def analyze(self, plan: dict, out_dir: Path) -> dict:
        from app.m6_window_analysis import analyze_window
        from app.db import SessionLocal
        with SessionLocal() as db:
            _clip, path = resolve_case_clip(db, case_id=self.run.case_id, clip_id=plan['clip_id'])
        out_dir.mkdir(parents=True, exist_ok=True)
        seek = float(plan.get('seek_seconds') or 0.0)
        extraction = m6_recording_source.extract_window_frames(
            path, seek, seek + float(plan.get('duration_seconds') or 4.0),
            self.fps, out_dir)
        result = analyze_window(
            out_dir, start_index=0, end_index=extraction['frames'] - 1, fps=self.fps,
            osd_anchor_seconds=plan['start_seconds'], camera=plan['camera_label'],
            window_id=plan['key'], task='m6.one_hop')
        result['cost'] = {**(result.get('cost') or {}), **extraction['cost'],
                          'seek_drift_seconds': extraction['seek_drift_seconds']}
        result['note'] = 'requested_seek_not_verified_pts'
        result['frames_dir'] = str(out_dir)
        result['evidence_refs'] = ['tracking_runs/%s/%s' % (self.run.id, p.name)
                                    for p in sorted(out_dir.glob('*.jpg'))[:2]]
        return result

    def score(self, result: dict) -> list[dict]:
        import cv2
        engine = self._reid()
        reference = self._anchor_embedding()
        frames_dir = Path(result.get('frames_dir') or '')
        scored = []
        for track in result.get('tracks') or []:
            obs = track.get('observations') or []
            if not obs:
                continue
            best = None
            for o in obs[-3:]:
                frame_no = int(o['frame_id']) + 1
                image = cv2.imread(str(frames_dir / ('%04d.jpg' % frame_no)))
                if image is None:
                    continue
                box = [int(v) for v in o['bbox']]
                valid, embeddings = engine.extract_embeddings(image, [box])
                if valid:
                    best = float(embeddings[0] @ reference)
                    break
            if best is not None:
                scored.append({'track_id': track['track_id'], 'retrieval_score': round(best, 4),
                           'frame_no': frame_no, 'bbox_pixels': box})
        return scored


def claim_next_run(db: Session) -> TrackingRun | None:
    run = db.scalar(select(TrackingRun).where(TrackingRun.status == 'queued')
                    .order_by(TrackingRun.created_at, TrackingRun.id).limit(1))
    return run
def _store_visual_review(db: Session, run: TrackingRun, plan: dict, row: TrackingCandidate,
                          candidate: dict, result: dict) -> None:
    """B8-C1 observe_only: 双图复核结果只入库，不改排名、不改身份状态。"""
    if not bool(getattr(settings, 'm6_candidate_review', True)):
        return
    import cv2
    from app import m6_candidate_visual_review as review
    from app.models import MediaAsset
    anchor = run.anchor_json or {}
    media = db.get(MediaAsset, anchor.get('media_id'))
    relpath = media.storage_relpath if media else None
    frame_no, bbox = candidate.get('frame_no'), candidate.get('bbox_pixels')
    frames_dir = Path(result.get('frames_dir') or '')
    base = {'candidate_key': row.candidate_key,
            'anchor_media_id': anchor.get('media_id'),
            'anchor_bbox_pixels': anchor.get('bbox_pixels'),
            'frame_no': frame_no, 'bbox_pixels': bbox,
            'schema': 'b8-candidate-visual-review/1'}
    data = None
    try:
        if not relpath or not anchor.get('bbox_pixels'):
            raise review.ReviewError('candidate_review_anchor_missing')
        if frame_no is None or not bbox:
            raise review.ReviewError('candidate_review_frame_missing')
        anchor_image = cv2.imread(str(Path(settings.storage_root).resolve() / relpath))
        frame_image = cv2.imread(str(frames_dir / ('%04d.jpg' % int(frame_no))))
        if anchor_image is None or frame_image is None:
            raise review.ReviewError('candidate_review_image_unreadable')
        data = review.review_pair(anchor_image, anchor.get('bbox_pixels'),
                                  frame_image, bbox)
    except Exception as exc:  # noqa: BLE001 - 复核失败不能中断追踪
        code = str(getattr(exc, 'code', None) or exc or 'candidate_review_failed')[:80]
        db.add(TrackingCandidateReview(
            id=str(uuid4()), run_id=run.id, candidate_id=row.id,
            camera_label=plan['camera_label'], overall='insufficient_evidence',
            error_code=code, review_json=base))
        return
    base.update(data)
    db.add(TrackingCandidateReview(
        id=str(uuid4()), run_id=run.id, candidate_id=row.id,
        camera_label=plan['camera_label'],
        overall=str(data.get('overall') or 'inconclusive'),
        model=data.get('model'), prompt_sha256=data.get('prompt_sha256'),
        review_json=base))


