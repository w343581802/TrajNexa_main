"""HTTP 接口：录像资源（只读目录浏览、目录登记、自动扫描开关、扫描结果）。

与 ``app/main.py`` 的约定一致：错误统一返回 ``{"error": {code, message, details}}``。
这里不提供任何写录像文件、改文件名、删除录像的接口——录像资源是只读输入。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import media_inputs, recordings
from app.config import settings
from app.db import get_db
from app.models import Case, RecordingClip, RecordingScanJob, RecordingSource

router = APIRouter(prefix='/api/v1/recordings', tags=['recordings'])


def fail(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message, 'details': None})


class SourceInput(BaseModel):
    case_id: str
    path: str = ''
    auto_scan: bool = False
    label: str = ''


class SourcePatch(BaseModel):
    auto_scan: bool | None = None
    label: str | None = None


class ScanInput(BaseModel):
    """保留空模型：手动扫描不需要参数，避免前端 POST 空 body 报 422。"""


def _resolve_recording_dir(raw: str) -> Path:
    """把前端传来的路径变成录像目录（与视频命名工具同一套路径口径）。

    支持三种写法，前端想传哪种都行：

    * 宿主机路径，如 G:\\测试数据\\视频\\正常（按 MEDIA_INPUT_PATH_MAP 换成容器挂载点）；
    * 容器内绝对路径，如 /data/videos/正常；
    * 相对录像根目录的路径，如 正常（前端目录浏览器就是这么传的）。

    传的是单个录像文件时取它所在目录（「添加视频」只登记目录）。
    录像资源是只读输入，所以路径必须落在 RECORDINGS_ROOT 之内。
    """
    root = recordings.recordings_root()
    text = (raw or '').strip().strip('"').strip("'")
    if not text:
        return root
    try:
        resolved = media_inputs.resolve_input(
            text, default_root=root, roots=[root], container_root=root)
    except media_inputs.InputPathError as exc:
        if exc.code == 'source_not_found':
            fail(422, 'recording_path_missing', '录像目录不存在或不可访问：' + text)
        fail(422, 'recording_path_invalid',
             '录像路径必须在录像根目录 ' + str(root) + ' 内：' + text)
    if resolved.is_file() and not recordings.is_video(resolved):
        fail(422, 'recording_path_invalid', '不是录像文件：' + resolved.name)
    return resolved.parent if resolved.is_file() else resolved


def _latest_job(db: Session, source_id: str) -> RecordingScanJob | None:
    return db.scalar(
        select(RecordingScanJob)
        .where(RecordingScanJob.source_id == source_id)
        .order_by(RecordingScanJob.created_at.desc())
        .limit(1))


def _source_json(db: Session, source: RecordingSource) -> dict:
    total = db.scalar(
        select(func.count()).select_from(RecordingClip)
        .where(RecordingClip.source_id == source.id)) or 0
    matched = db.scalar(
        select(func.count()).select_from(RecordingClip)
        .where(RecordingClip.source_id == source.id,
               RecordingClip.match_status == 'matched')) or 0
    with_time = db.scalar(
        select(func.count()).select_from(RecordingClip)
        .where(RecordingClip.source_id == source.id,
               RecordingClip.start_local.is_not(None))) or 0
    job = _latest_job(db, source.id)
    return {
        'id': source.id,
        'case_id': source.case_id,
        'path': source.path,
        'label': source.label or (Path(source.path).name if source.path else '录像根目录'),
        'auto_scan': bool(source.auto_scan),
        'created_at': source.created_at,
        'last_scan_at': source.last_scan_at,
        'last_status': source.last_status,
        'last_error': source.last_error,
        'file_count': int(total),
        'time_count': int(with_time),
        'matched_count': int(matched),
        'scanning': source.last_status in ('running', 'queued'),
        'job': _job_json(job) if job else None,
    }


def _job_json(job: RecordingScanJob | None) -> dict | None:
    if job is None:
        return None
    return {
        'id': job.id,
        'source_id': job.source_id,
        'status': job.status,
        'total_files': job.total_files,
        'processed_files': job.processed_files,
        'added_files': job.added_files,
        'updated_files': job.updated_files,
        'created_at': job.created_at,
        'started_at': job.started_at,
        'finished_at': job.finished_at,
        'error_message': job.error_message,
    }


def _clip_json(clip: RecordingClip) -> dict:
    return {
        'id': clip.id,
        'source_id': clip.source_id,
        'relpath': clip.relpath,
        'file_name': clip.file_name,
        'duration_seconds': clip.duration_seconds,
        'start_local': clip.start_local,
        'start_source': clip.start_source,
        'camera_name': clip.camera_name,
        'camera_uuid': clip.camera_uuid,
        'name_source': clip.name_source,
        'match_status': clip.match_status,
        'ocr_status': clip.ocr_status,
        'review_kind': clip.review_kind,
        'reviewer_note': clip.reviewer_note,
        'suggestions': clip.match_suggestions or [],
        'scanned_at': clip.scanned_at,
    }


@router.get('/root')
def recording_root():
    root = recordings.recordings_root()
    return {'root': str(root), 'exists': root.is_dir(),
            'scan_interval_seconds': settings.recordings_scan_interval_seconds}


@router.get('/browse')
def browse(path: str = ''):
    """列目录。path 可以是相对录像根目录的路径，也可以直接是宿主机路径。"""
    target = _resolve_recording_dir(path)
    if not target.is_dir():
        fail(422, 'recording_path_invalid', '录像目录不存在或不可访问')
    try:
        return recordings.list_dir(recordings.relative_path(target))
    except recordings.RecordingPathError as exc:
        fail(422, 'recording_path_invalid', str(exc))


@router.get('/sources')
def list_sources(case_id: str, db: Session = Depends(get_db)):
    if db.get(Case, case_id) is None:
        fail(404, 'not_found', '案件不存在')
    sources = db.scalars(
        select(RecordingSource).where(RecordingSource.case_id == case_id)
        .order_by(RecordingSource.created_at)).all()
    return [_source_json(db, source) for source in sources]


@router.post('/sources', status_code=201)
def create_source(body: SourceInput, db: Session = Depends(get_db)):
    if db.get(Case, body.case_id) is None:
        fail(404, 'not_found', '案件不存在')
    base = _resolve_recording_dir(body.path)
    if not base.is_dir():
        fail(422, 'recording_path_missing', '录像目录不存在或不可访问')
    rel = recordings.relative_path(base)
    existing = db.scalar(
        select(RecordingSource).where(RecordingSource.case_id == body.case_id,
                                      RecordingSource.path == rel))
    if existing is not None:
        return _source_json(db, existing)
    source = RecordingSource(
        id=str(uuid4()), case_id=body.case_id, path=rel,
        label=(body.label or base.name or '录像根目录')[:255],
        auto_scan=bool(body.auto_scan), last_status='idle')
    db.add(source)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        fail(409, 'recording_source_exists', '该案件已添加过这个录像目录')
    db.refresh(source)
    return _source_json(db, source)


@router.patch('/sources/{source_id}')
def patch_source(source_id: str, body: SourcePatch, db: Session = Depends(get_db)):
    source = db.get(RecordingSource, source_id)
    if source is None:
        fail(404, 'not_found', '录像目录不存在')
    if body.auto_scan is not None:
        source.auto_scan = bool(body.auto_scan)
    if body.label is not None:
        source.label = body.label.strip()[:255]
    db.commit()
    db.refresh(source)
    return _source_json(db, source)


@router.delete('/sources/{source_id}')
def delete_source(source_id: str, db: Session = Depends(get_db)):
    source = db.get(RecordingSource, source_id)
    if source is None:
        fail(404, 'not_found', '录像目录不存在')
    # 只删「扫描记录」，不碰任何录像文件
    for clip in db.scalars(select(RecordingClip).where(RecordingClip.source_id == source.id)):
        db.delete(clip)
    for job in db.scalars(select(RecordingScanJob).where(RecordingScanJob.source_id == source.id)):
        db.delete(job)
    db.delete(source)
    db.commit()
    return {'id': source_id, 'recordings_preserved': True,
            'message': '已移除录像目录登记；录像文件与索引文件未做任何改动'}


@router.post('/sources/{source_id}/scan', status_code=202)
def start_scan(source_id: str, body: ScanInput | None = None, db: Session = Depends(get_db)):
    source = db.get(RecordingSource, source_id)
    if source is None:
        fail(404, 'not_found', '录像目录不存在')
    active = db.scalar(
        select(RecordingScanJob).where(
            RecordingScanJob.source_id == source.id,
            RecordingScanJob.status.in_(('queued', 'running'))).limit(1))
    if active is not None:
        return {'source': _source_json(db, source), 'job': _job_json(active),
                'already_running': True}
    job = RecordingScanJob(id=str(uuid4()), source_id=source.id, status='queued')
    source.last_status = 'queued'
    source.last_error = None
    db.add(job)
    db.commit()
    db.refresh(job)
    return {'source': _source_json(db, source), 'job': _job_json(job),
            'already_running': False}


@router.get('/sources/{source_id}/job')
def source_job(source_id: str, db: Session = Depends(get_db)):
    source = db.get(RecordingSource, source_id)
    if source is None:
        fail(404, 'not_found', '录像目录不存在')
    return _job_json(_latest_job(db, source.id))


@router.get('/sources/{source_id}/clips')
def source_clips(source_id: str, limit: int = 200, db: Session = Depends(get_db)):
    if db.get(RecordingSource, source_id) is None:
        fail(404, 'not_found', '录像目录不存在')
    clips = db.scalars(
        select(RecordingClip).where(RecordingClip.source_id == source_id)
        .order_by(RecordingClip.start_local, RecordingClip.relpath)
        .limit(max(1, min(1000, limit)))).all()
    return [_clip_json(clip) for clip in clips]


@router.get('/clips')
def list_clips(case_id: str, limit: int = 500, db: Session = Depends(get_db)):
    """案件内所有录像段的扫描结果（时间轴/地图点亮用）。"""
    if db.get(Case, case_id) is None:
        fail(404, 'not_found', '案件不存在')
    source_ids = [row for row in db.scalars(
        select(RecordingSource.id).where(RecordingSource.case_id == case_id))]
    if not source_ids:
        return []
    clips = db.scalars(
        select(RecordingClip).where(RecordingClip.source_id.in_(source_ids))
        .order_by(RecordingClip.start_local, RecordingClip.relpath)
        .limit(max(1, min(2000, limit)))).all()
    return [_clip_json(clip) for clip in clips]


@router.get('/summary')
def summary(case_id: str, db: Session = Depends(get_db)):
    if db.get(Case, case_id) is None:
        fail(404, 'not_found', '案件不存在')
    data = recordings.case_summary(db, case_id)
    return data


class NameInput(BaseModel):
    name: str = ''
    note: str | None = None


@router.get('/clips/{clip_id}/frame')
def clip_frame(clip_id: str, db: Session = Depends(get_db)):
    """这段录像的开头截图，用于人工核对摄像头名称（只读录像文件）。"""
    try:
        path = recordings.clip_frame(db, clip_id)
    except LookupError as exc:
        fail(404, 'not_found', str(exc))
    except ValueError:
        fail(409, 'frame_unavailable', '这段录像取不到可用画面，无法显示截图')
    return FileResponse(path, media_type='image/jpeg',
                        headers={'Cache-Control': 'private, max-age=300'})


@router.post('/clips/{clip_id}/name')
def set_clip_name(clip_id: str, body: NameInput, db: Session = Depends(get_db)):
    """人工核对识别名称：改完立即按台账重新匹配摄像头，结果不会被自动扫描回滚。"""
    try:
        clip = recordings.set_clip_name(db, clip_id, name=body.name, note=body.note)
    except LookupError:
        fail(404, 'not_found', '录像片段不存在')
    return _clip_json(clip)

@router.post('/sources/{source_id}/rematch', status_code=202)
def rematch_source(source_id: str, db: Session = Depends(get_db)):
    try:
        return recordings.rematch_source(db, source_id)
    except LookupError:
        fail(404, 'not_found', '录像目录不存在')
    except ValueError:
        fail(409, 'catalog_unavailable', '缺少有效的摄像头目录快照，不能重新匹配')