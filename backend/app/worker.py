"""M0 durable, single-instance, DB-backed worker. No model inference yet."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import hashlib
import logging
import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import recordings, video_naming
from app.config import settings
from app.db import SessionLocal
from app.models import (AnalysisJob, InspectionReport, MediaAsset,
                        RecordingScanJob, RecordingSource)
from app.inspector import inspect_media

logger = logging.getLogger(__name__)

def utcnow():
    return datetime.now(timezone.utc)

def recover_interrupted_jobs(db: Session):
    """Only call when starting the sole worker, not while another worker runs."""
    db.execute(update(AnalysisJob).where(AnalysisJob.status == 'running').values(status='queued', progress=0, started_at=None))
    db.execute(update(RecordingScanJob).where(RecordingScanJob.status == 'running')
               .values(status='queued', processed_files=0, started_at=None))
    db.execute(update(RecordingSource).where(RecordingSource.last_status == 'running')
               .values(last_status='queued'))
    db.commit()

def mock_inspect(media: MediaAsset, path: Path) -> dict:
    """M0 intentionally records unknown OCR/view/quality fields. Does not fabricate model output."""
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    if h.hexdigest() != media.sha256:
        raise ValueError('素材 SHA-256 与数据库记录不一致')
    return {
        'schema_version': '0.1.0',
        'processing_stage': 'm0_stub_no_ocr_or_video_decode',
        'media': {'media_id': media.id, 'kind': media.kind,
                  'sha256': media.sha256, 'size_bytes': media.size_bytes,
                  'mime_detected': media.mime_detected},
        'time': {'overlay_time_samples': [], 'timezone': None, 'time_status': 'not_checked'},
        'camera': {'overlay_name_raw': None, 'camera_candidates': [],
                   'camera_uuid': None, 'match_status': 'not_checked'},
        'quality': {'overall_status': 'unknown', 'issues': [], 'decode_status': 'not_checked'},
        'view_check': {'status': 'not_checked', 'spatial_revision': None},
        'review_state': 'pending',
    }

def run_media_job(db: Session) -> bool:
    query = select(AnalysisJob).where(AnalysisJob.status == 'queued').order_by(AnalysisJob.created_at, AnalysisJob.id).limit(1)
    if db.bind and db.bind.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    job = db.scalar(query)
    if job is None:
        return False
    job.status, job.started_at, job.attempt, job.progress = 'running', utcnow(), job.attempt + 1, 10
    db.commit()
    naming_result = None
    naming = job.module.startswith(video_naming.PREFIX)
    try:
        media = db.get(MediaAsset, job.media_id)
        if media is None:
            raise FileNotFoundError('媒体记录不存在')
        raw = Path(media.storage_relpath)
        if naming and raw.is_absolute():
            # 命名工具直接引用本地录像（前端传路径，不传文件）
            path = raw
        else:
            root = settings.storage_root.resolve()
            path = (root / media.storage_relpath).resolve()
            if not path.is_relative_to(root):
                raise FileNotFoundError('媒体文件不存在或位置非法')
        if not path.is_file():
            raise FileNotFoundError('媒体文件不存在或位置非法')
        if naming:
            batch_id = video_naming.batch_id_for_job(job.module)
            naming_result = video_naming.process_media(media, path, batch_id)
            result = naming_result
            schema_version, module_version = video_naming.SCHEMA_VERSION, video_naming.MODULE_VERSION
        else:
            result = inspect_media(media, path)
            schema_version, module_version = '0.1.3', 'm1c-catalog-0.1'
        report = InspectionReport(id=str(uuid4()), job_id=job.id, media_id=media.id,
            schema_version=schema_version, module_version=module_version, result_json=result)
        db.add(report)
        job.status, job.progress, job.finished_at = 'succeeded', 100, utcnow()
        db.commit()
        if naming:
            try:
                video_naming.finalize_folders(db, batch_id)
            except Exception:
                logger.exception('video naming finalize folders failed batch=%s', batch_id)
    except Exception as exc:
        db.rollback()
        if naming_result:
            try:
                video_naming.revert_rename(naming_result)
            except OSError:
                logger.warning('video naming revert failed media=%s', job.media_id)
        job = db.get(AnalysisJob, job.id)
        job.status, job.progress, job.finished_at = 'failed', 100, utcnow()
        job.error_code, job.error_message = 'processing_failed', str(exc)[:500]
        db.commit()
        logger.exception('job=%s failed', job.id)
    return True


def run_recording_job(db: Session) -> bool:
    """扫描一个录像目录（只读录像文件，写扫描结果表）。"""
    query = select(RecordingScanJob).where(RecordingScanJob.status == 'queued') \
        .order_by(RecordingScanJob.created_at, RecordingScanJob.id).limit(1)
    if db.bind and db.bind.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    job = db.scalar(query)
    if job is None:
        return False
    job.status, job.started_at = 'running', utcnow()
    source = db.get(RecordingSource, job.source_id)
    if source is not None:
        source.last_status = 'running'
        source.last_error = None
    db.commit()
    try:
        stats = recordings.scan_source(db, job)
        job = db.get(RecordingScanJob, job.id)
        job.status, job.finished_at = 'succeeded', utcnow()
        job.total_files = stats['total']
        job.processed_files = stats['total']
        job.added_files = stats['added']
        job.updated_files = stats['updated']
        source = db.get(RecordingSource, job.source_id)
        if source is not None:
            source.last_status = 'succeeded'
            source.last_scan_at = utcnow()
            source.last_error = None
        db.commit()
        logger.info('recording scan source=%s total=%s added=%s updated=%s skipped=%s',
                    job.source_id, stats['total'], stats['added'], stats['updated'], stats['skipped'])
    except Exception as exc:
        db.rollback()
        job = db.get(RecordingScanJob, job.id)
        job.status, job.finished_at = 'failed', utcnow()
        job.error_message = str(exc)[:500]
        source = db.get(RecordingSource, job.source_id)
        if source is not None:
            source.last_status = 'failed'
            source.last_error = str(exc)[:300]
        db.commit()
        logger.exception('recording scan source=%s failed', job.source_id)
    return True


def run_once(db: Session) -> bool:
    if run_media_job(db):
        return True
    return run_recording_job(db)

def main():
    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        recover_interrupted_jobs(db)
    while True:
        with SessionLocal() as db:
            # 自动扫描：给开启开关、且过了扫描间隔的录像目录排一次增量扫描。
            recordings.enqueue_auto_scans(db)
            if not run_once(db):
                time.sleep(settings.worker_poll_seconds)

if __name__ == '__main__':
    main()