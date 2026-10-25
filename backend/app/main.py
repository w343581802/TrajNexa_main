"""Minimal main-system HTTP API. No TrajNexa_Map tables or endpoints are reused."""
from hashlib import sha256
from pathlib import Path
from datetime import datetime
from uuid import uuid4
import os

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MediaAsset, AnalysisJob, InspectionReport
from app.media import detect_signature, safe_basename
from app.config import settings

app = FastAPI(title='TrajNexa Demo M0', version='0.1.0')

class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    original_name: str
    mime_detected: str
    sha256: str
    size_bytes: int
    ingested_at: datetime

class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    media_id: str
    module: str
    status: str
    attempt: int
    progress: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None

class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_id: str
    media_id: str
    schema_version: str
    module_version: str
    result_json: dict
    created_at: datetime

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail
    else:
        error = {'code': 'http_error', 'message': str(detail), 'details': None}
    return JSONResponse(status_code=exc.status_code, content={'error': error})

def fail(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message, 'details': None})

@app.get('/api/v1/health')
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
        return {'status': 'ok', 'component': 'trajnexa_main_m0'}
    except Exception:
        return JSONResponse(status_code=503, content={'status': 'degraded'})

@app.post('/api/v1/media', response_model=MediaOut, status_code=201)
async def upload_media(file: UploadFile = File(...), db: Session = Depends(get_db)):
    media_id = str(uuid4())
    storage_root = settings.storage_root.resolve()
    storage_dir = storage_root / 'originals'
    storage_dir.mkdir(parents=True, exist_ok=True)
    temporary = storage_dir / f'.{media_id}.partial'
    length = 0
    digest = sha256()
    header = b''
    try:
        with temporary.open('xb') as dst:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                length += len(chunk)
                if length > settings.max_upload_bytes:
                    fail(413, 'file_too_large', '超过 Demo 配置的文件大小限制')
                if len(header) < 16:
                    header += chunk[:16 - len(header)]
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if not length:
            fail(415, 'empty_media', '无法接收空文件')
        info = detect_signature(header)
        if info is None:
            fail(415, 'unsupported_media', '无法识别的图像/视频签名，拒绝上传')
        kind, mime, suffix = info
        final = storage_dir / f'{media_id}{suffix}'
        temporary.replace(final)
        relpath = final.relative_to(storage_root).as_posix()
        asset = MediaAsset(id=media_id, kind=kind,
                           original_name=safe_basename(file.filename),
                           mime_detected=mime, sha256=digest.hexdigest(),
                           storage_relpath=relpath, size_bytes=length)
        try:
            db.add(asset)
            db.commit()
            db.refresh(asset)
        except Exception:
            db.rollback()
            final.unlink(missing_ok=True)
            raise
        return asset
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()

@app.get('/api/v1/media', response_model=list[MediaOut])
def list_media(db: Session = Depends(get_db)):
    return list(db.scalars(select(MediaAsset).order_by(MediaAsset.ingested_at.desc()).limit(100)))

@app.get('/api/v1/media/{media_id}', response_model=MediaOut)
def get_media(media_id: str, db: Session = Depends(get_db)):
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        fail(404, 'not_found', '素材不存在')
    return asset

@app.post('/api/v1/media/{media_id}/inspect', response_model=JobOut, status_code=202)
def inspect_media(media_id: str, db: Session = Depends(get_db)):
    if not db.get(MediaAsset, media_id):
        fail(404, 'not_found', '素材不存在')
    job = AnalysisJob(id=str(uuid4()), media_id=media_id, module='media_inspector_m1a', status='queued', attempt=0, progress=0)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job

@app.get('/api/v1/jobs/{job_id}', response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(AnalysisJob, job_id)
    if job is None:
        fail(404, 'not_found', '作业不存在')
    return job

@app.post('/api/v1/jobs/{job_id}/retry', response_model=JobOut)
def retry_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(AnalysisJob, job_id)
    if job is None:
        fail(404, 'not_found', '作业不存在')
    if job.status != 'failed':
        fail(409, 'invalid_job_state', '仅允许重试失败作业')
    job.status = 'queued'
    job.progress = 0
    job.error_code = job.error_message = None
    job.started_at = job.finished_at = None
    db.commit()
    db.refresh(job)
    return job

@app.get('/api/v1/inspections/{inspection_id}', response_model=ReportOut)
def get_report(inspection_id: str, db: Session = Depends(get_db)):
    report = db.get(InspectionReport, inspection_id)
    if report is None:
        fail(404, 'not_found', '报告不存在')
    return report

@app.get('/api/v1/jobs/{job_id}/report', response_model=ReportOut)
def get_job_report(job_id: str, db: Session = Depends(get_db)):
    report = db.scalar(select(InspectionReport).where(InspectionReport.job_id == job_id))
    if report is None:
        fail(404, 'not_found', '尚无报告')
    return report
