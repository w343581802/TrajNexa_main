"""B8-B: execute a persistent tracking run against real registered recordings.

The worker never reads a truth package, an exported frame directory or a /work path.
It plans camera windows from case-owned RecordingClip rows, extracts frames from the
actual video file, calls the existing window analyzer, and stores candidates as
unverified. Analyzer and scorer are injectable so the orchestration can be tested
without loading D-FINE or OSNet.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import m6_recording_source
from app.m6_recording_source import RecordingFrameError
from app.config import settings
from app.m4_video import resolve_case_clip
from app.m6_run_service import TrackingRunError
from app.m6_target_candidate_adapter import candidates_from_scores
from app.models import (RecordingClip, RecordingSource, TrackingCandidate,
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
        if (clip.camera_name or '').strip() and clip.duration_seconds and clip.start_local:
            clips.append(clip)
    return clips


def _clip_start(clip: RecordingClip) -> float | None:
    from app import m6_recording_source as source
    return source.seconds_of_day(clip.start_local)


def plan_windows(run: TrackingRun, clips: list[RecordingClip], done: set[str]) -> list[dict]:
    """Build camera windows around the anchor; deterministic and budget-aware."""
    anchor = run.anchor_json or {}
    scope = run.scope_json or {}
    anchor_label = anchor.get('camera_label')
    anchor_seconds = float(anchor.get('anchor_seconds') or 0.0)
    seg = float(scope.get('segment_seconds') or 6.0)
    gap = float(scope.get('gap_seconds') or 2.0)
    budget = int(scope.get('max_windows') or 6)

    by_camera: dict[str, list[RecordingClip]] = {}
    for clip in clips:
        by_camera.setdefault((clip.camera_name or '').strip(), []).append(clip)
    order = sorted(by_camera)
    if anchor_label in order:
        order.remove(anchor_label)
        order.append(anchor_label)  # anchor camera continuation comes last

    plans: list[dict] = []
    for label in order:
        if len(plans) >= budget:
            break
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
            plans.append({'camera_label': label, 'clip_id': clip.id,
                          'clip_relpath': clip.relpath, 'start_seconds': round(start, 3),
                          'end_seconds': round(min(start + seg, clip_end), 3),
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


def execute_run(db: Session, run_id: str, analyzer=None, fps: float = 5.0,
                storage_root: Path | None = None) -> TrackingRun:
    """Run or resume one tracking run; idempotent per planned window."""
    run = db.get(TrackingRun, run_id)
    if run is None:
        raise TrackingRunError('tracking_run_not_found')
    if run.status == 'cancelled':
        return run
    if run.status not in IN_PROGRESS_STATUSES:
        return run
    run.status, run.started_at, run.attempt = 'running', _utcnow(), run.attempt + 1
    run.error_code = run.error_message = None
    db.commit()

    progress = dict(run.progress_json or {})
    done = set(progress.get('analyzed_windows') or [])
    frames_used = int(progress.get('frames_used') or 0)
    budget = int((run.scope_json or {}).get('budget_frames') or 3000)
    root = Path(storage_root or settings.storage_root) / 'tracking_runs' / run.id

    try:
        clips = _case_clips(db, run.case_id)
        if not clips:
            run.status = 'unresolved'
            run.progress_json = {**progress, 'reason': 'no_case_recordings'}
            run.finished_at = _utcnow()
            db.commit()
            return run
        if analyzer is None:
            analyzer = RecordingAnalyzer(run, fps=fps)
        plans = plan_windows(run, clips, done)
        for plan in plans:
            if frames_used >= budget:
                run.status = 'budget_exhausted'
                break
            out_dir = root / ('w_' + plan['key'].replace('@', '_').replace('.', 'p'))
            if out_dir.exists():
                shutil.rmtree(out_dir)
            result = analyzer.analyze(plan, out_dir)
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
            if tracks and scored:
                verdict = candidates_from_scores(scored)
                for candidate in verdict.get('target_candidates') or []:
                    _store_candidate(db, run, plan, candidate, refs,
                                     'top1=%s margin=%s' % (candidate.get('retrieval_score'),
                                                              candidate.get('margin_top1_top2')))
            done.add(plan['key'])
            frames_used += frames
            progress = {'schema': 'b8-run-progress/1', 'analyzed_windows': sorted(done),
                        'windows_done': len(done), 'frames_used': frames_used,
                        'last_camera': plan['camera_label'],
                        'last_window': [plan['start_seconds'], plan['end_seconds']]}
            run.progress_json = progress
            run.status = 'running'
            db.commit()

        if run.status == 'running':
            has_candidates = db.scalar(select(TrackingCandidate.id).where(
                TrackingCandidate.run_id == run.id).limit(1)) is not None
            run.status = 'awaiting_review' if has_candidates else 'unresolved'
            run.finished_at = _utcnow()
            db.commit()
    except AnalyzerUnavailable as exc:
        db.rollback()
        run = db.get(TrackingRun, run_id)
        run.status, run.finished_at = 'failed', _utcnow()
        run.error_code, run.error_message = exc.code, '模型不可用，任务未产生结论'
        db.commit()
    except RecordingFrameError as exc:
        db.rollback()
        run = db.get(TrackingRun, run_id)
        run.status, run.finished_at = 'failed', _utcnow()
        run.error_code = exc.code
        run.error_message = str(exc)[:500]
        db.commit()
    except Exception as exc:  # noqa: BLE001 - worker must persist failure, not crash the loop
        db.rollback()
        run = db.get(TrackingRun, run_id)
        run.status, run.finished_at = 'failed', _utcnow()
        run.error_code, run.error_message = 'tracking_run_failed', str(exc)[:500]
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
        extraction = m6_recording_source.extract_window_frames(
            path, plan['start_seconds'], plan['end_seconds'], self.fps, out_dir)
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
                scored.append({'track_id': track['track_id'], 'retrieval_score': round(best, 4)})
        return scored


def claim_next_run(db: Session) -> TrackingRun | None:
    run = db.scalar(select(TrackingRun).where(TrackingRun.status == 'queued')
                    .order_by(TrackingRun.created_at, TrackingRun.id).limit(1))
    return run
