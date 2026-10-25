"""M0 durable, single-instance, DB-backed worker. No model inference yet."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import hashlib
import logging
import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import MediaAsset, AnalysisJob, InspectionReport
from app.inspector import inspect_media

logger = logging.getLogger(__name__)

def utcnow():
    return datetime.now(timezone.utc)

def recover_interrupted_jobs(db: Session):
    """Only call when starting the sole worker, not while another worker runs."""
    db.execute(update(AnalysisJob).where(AnalysisJob.status == 'running').values(status='queued', progress=0, started_at=None))
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

def run_once(db: Session) -> bool:
    query = select(AnalysisJob).where(AnalysisJob.status == 'queued').order_by(AnalysisJob.created_at, AnalysisJob.id).limit(1)
    if db.bind and db.bind.dialect.name == 'postgresql':
        query = query.with_for_update(skip_locked=True)
    job = db.scalar(query)
    if job is None:
        return False
    job.status, job.started_at, job.attempt, job.progress = 'running', utcnow(), job.attempt + 1, 10
    db.commit()
    try:
        media = db.get(MediaAsset, job.media_id)
        if media is None:
            raise FileNotFoundError('媒体记录不存在')
        root = settings.storage_root.resolve()
        path = (root / media.storage_relpath).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError('媒体文件不存在或位置非法')
        result = inspect_media(media, path)
        report = InspectionReport(id=str(uuid4()), job_id=job.id, media_id=media.id,
            schema_version='0.1.3', module_version='m1c-catalog-0.1', result_json=result)
        db.add(report)
        job.status, job.progress, job.finished_at = 'succeeded', 100, utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(AnalysisJob, job.id)
        job.status, job.progress, job.finished_at = 'failed', 100, utcnow()
        job.error_code, job.error_message = 'processing_failed', str(exc)[:500]
        db.commit()
        logger.exception('job=%s failed', job.id)
    return True

def main():
    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        recover_interrupted_jobs(db)
    while True:
        with SessionLocal() as db:
            if not run_once(db):
                time.sleep(settings.worker_poll_seconds)

if __name__ == '__main__':
    main()
