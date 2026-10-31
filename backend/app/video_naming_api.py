"""HTTP 接口：视频命名工具（本地路径 / 目录 → 开头几秒 OCR → 原地重命名 → CSV）。

前端是本地 demo，只传路径文本，不传文件；API 负责展开目录、建作业，
worker 读取本地真实录像，OCR 后把文件改名为「摄像头名称_时间」。
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
import logging
import mimetypes
import os
import re
import shutil

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import media_inputs, video_naming
from app.config import settings
from app.db import get_db
from app.models import AnalysisJob, InspectionReport, MediaAsset

router = APIRouter(prefix='/api/v1/video-tools/naming', tags=['video-naming'])
logger = logging.getLogger(__name__)

# 视频扩展名与录像资源共用一份，避免两处定义慢慢走偏
VIDEO_SUFFIXES = media_inputs.VIDEO_SUFFIXES


class NamingRequest(BaseModel):
    paths: list[str] = Field(default_factory=list, description='本地视频文件或目录路径')
    recursive: bool = True
    rename_folders: bool = False


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=220, description='新的文件名（可带扩展名）')


def fail(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message, 'details': None})


# 路径口径统一走 media_inputs（录像资源 / 视频命名 / 添加要素共用）：
# 宿主机路径翻译、相对路径解析、允许根校验、目录枚举都在那一个模块里。
_PATH_ERROR_STATUS = {
    'empty_path': 422,
    'source_not_found': 404,
    'path_not_allowed': 403,
    'unsupported_video': 415,
    'no_videos': 422,
    'too_many_files': 422,
}


def _translate_host_path(text: str) -> str:
    """宿主机路径 → 容器内可写挂载点。实现已收到 media_inputs，这里保留给老调用方。"""
    return media_inputs.translate_host_path(text)


def _allowed_roots() -> list[Path]:
    """VIDEO_NAMING_ROOTS 配了才限制可访问目录；本地 demo 不配则只校验路径存在。"""
    return media_inputs.allowed_roots()


def _collect(paths: list[str], recursive: bool) -> tuple[list[Path], list[Path]]:
    """展开一批「文件或目录」输入；错误码与历史接口保持一致。"""
    try:
        collected = media_inputs.collect_inputs(
            paths, recursive=recursive, limit=settings.video_naming_max_files,
            default_root=settings.video_naming_root)
    except media_inputs.InputPathError as exc:
        fail(_PATH_ERROR_STATUS.get(exc.code, 422), exc.code, exc.message)
    return collected.files, collected.roots


def _jobs(db: Session, batch_id: str) -> list[AnalysisJob]:
    try:
        module = video_naming.batch_module(batch_id)
    except video_naming.VideoNamingError:
        fail(404, 'batch_not_found', '命名批次不存在')
    return list(db.scalars(
        select(AnalysisJob).where(AnalysisJob.module == module)
        .order_by(AnalysisJob.created_at, AnalysisJob.id)))


def _report(db: Session, job: AnalysisJob) -> InspectionReport | None:
    return db.scalar(select(InspectionReport).where(InspectionReport.job_id == job.id))


def _safe_stored_path(relpath: str | None) -> Path | None:
    if not relpath:
        return None
    root = settings.storage_root.resolve()
    path = (root / relpath).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def _safe_local_path(value: str | None) -> Path | None:
    """下载用的本地路径校验：与命名流程共用 media_inputs 的同一套口径。

    以前这里自己拼了一遍「绝对路径 + 允许根 + 是文件」，与 ``_collect`` 的口径会走偏；
    现在统一走 ``media_inputs.resolve_input``：宿主机路径翻译、相对路径解析、
    允许根校验、符号链接解析都在一处。
    """
    if not value:
        return None
    try:
        path = media_inputs.resolve_input(value, default_root=settings.video_naming_root)
    except media_inputs.InputPathError:
        return None
    return path if path.is_file() else None


def _item(db: Session, job: AnalysisJob) -> dict:
    media = db.get(MediaAsset, job.media_id)
    report = _report(db, job)
    result = report.result_json if report and isinstance(report.result_json, dict) else {}
    item = {
        'media_id': job.media_id,
        'job_id': job.id,
        'status': job.status,
        'progress': job.progress,
        'original_name': media.original_name if media else '',
        'original_path': result.get('original_path')
        or (media.storage_relpath if media and Path(media.storage_relpath).is_absolute() else ''),
        'renamed_name': result.get('renamed_name') or '',
        'renamed_path': result.get('renamed_path') or '',
        'folder_original_path': result.get('folder_original_path') or '',
        'folder_renamed_path': result.get('folder_renamed_path') or '',
        'folder_renamed_name': result.get('folder_renamed_name') or '',
        'camera_name': result.get('camera_name') or '',
        'camera_uuid': result.get('camera_uuid'),
        'capture_time': result.get('capture_time'),
        'time_source': result.get('time_source') or '',
        'ocr_status': result.get('ocr_status') or '',
        'match_status': result.get('match_status') or '',
        'duration_seconds': result.get('duration_seconds'),
        'size_bytes': media.size_bytes if media else 0,
        'needs_review': bool(result.get('needs_review')),
        'manual_renamed': bool(result.get('manual_renamed')),
        'warnings': result.get('warnings') or [],
        'error': job.error_message,
        'frame_relpath': result.get('frame_relpath'),
        'renamed_relpath': result.get('renamed_path'),
    }
    item['download_url'] = (
        f'/api/v1/video-tools/naming/batches/{video_naming.batch_id_for_job(job.module)}'
        f'/items/{job.media_id}/download' if item['renamed_relpath'] else None)
    item['frame_url'] = (
        f'/api/v1/video-tools/naming/batches/{video_naming.batch_id_for_job(job.module)}'
        f'/items/{job.media_id}/frame' if item['frame_relpath'] else None)
    return item


def _batch_payload(db: Session, batch_id: str) -> dict:
    jobs = _jobs(db, batch_id)
    if not jobs:
        fail(404, 'batch_not_found', '命名批次不存在')
    batch_id = video_naming.batch_id_for_job(jobs[0].module)
    items = [_item(db, job) for job in jobs]
    statuses = {item['status'] for item in items}
    if statuses & {'queued', 'running'}:
        status = 'processing'
    elif 'succeeded' in statuses and 'failed' in statuses:
        status = 'partial'
    elif 'succeeded' in statuses:
        status = 'succeeded'
    else:
        status = 'failed'
    completed = sum(item['status'] in {'succeeded', 'failed'} for item in items)
    return {
        'batch_id': batch_id,
        'status': status,
        'total': len(items),
        'completed': completed,
        'succeeded': sum(item['status'] == 'succeeded' for item in items),
        'failed': sum(item['status'] == 'failed' for item in items),
        'created_at': jobs[0].created_at,
        'items': items,
        'table_url': f'/api/v1/video-tools/naming/batches/{batch_id}/table.csv',
        'can_download_table': any(item['status'] == 'succeeded' for item in items),
    }


@router.post('/batches', status_code=202)
def create_batch(payload: NamingRequest, db: Session = Depends(get_db)):
    if not payload.paths:
        fail(422, 'no_paths', '请输入视频文件或目录路径')
    files, roots = _collect(payload.paths, payload.recursive)
    batch_id = str(uuid4())
    module = video_naming.batch_module(batch_id)
    video_naming.write_batch_meta(batch_id, roots, payload.rename_folders)
    try:
        for path in files:
            try:
                size = path.stat().st_size
            except OSError as exc:
                fail(409, 'source_unreadable', f'无法读取：{path.name}（{exc}）')
            media_id = str(uuid4())
            mime = mimetypes.guess_type(path.name)[0] or 'video/mp4'
            media = MediaAsset(
                id=media_id,
                case_id=None,  # 工具产物不绑定案件，删案件不会误删录像
                kind='video',
                original_name=path.name,
                mime_detected=mime,
                sha256='',
                storage_relpath=str(path),
                size_bytes=size,
            )
            job = AnalysisJob(id=str(uuid4()), media_id=media.id, module=module,
                              status='queued', attempt=0, progress=0)
            db.add(media)
            # models 没有 relationship()，先 flush media 再插 job，避免 PostgreSQL 外键顺序问题
            db.flush()
            db.add(job)
            db.flush()
        db.commit()
    except HTTPException:
        db.rollback()
        shutil.rmtree(video_naming.batch_dir(batch_id), ignore_errors=True)
        raise
    except Exception:
        db.rollback()
        shutil.rmtree(video_naming.batch_dir(batch_id), ignore_errors=True)
        logger.exception('video naming create batch failed batch=%s', batch_id)
        fail(500, 'video_naming_batch_failed', '创建命名批次失败，请重试')
    return _batch_payload(db, batch_id)


@router.get('/batches/{batch_id}')
def get_batch(batch_id: str, db: Session = Depends(get_db)):
    return _batch_payload(db, batch_id)


@router.get('/batches/{batch_id}/table.csv')
def get_table(batch_id: str, db: Session = Depends(get_db)):
    payload = _batch_payload(db, batch_id)
    body = video_naming.csv_text(payload['items'])
    ascii_name = f'video_naming_{batch_id[:8]}.csv'
    utf8_name = quote(f'视频命名_{batch_id[:8]}.csv')
    return Response(
        content=body,
        media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"},
    )


def _item_file(db: Session, batch_id: str, media_id: str, field: str) -> tuple[Path, dict]:
    jobs = _jobs(db, batch_id)
    job = next((item for item in jobs if item.media_id == media_id), None)
    if job is None:
        fail(404, 'item_not_found', '该批次里没有这个视频')
    if job.status != 'succeeded':
        fail(409, 'item_not_ready', '该视频还在处理或已失败，暂不能下载')
    report = _report(db, job)
    result = report.result_json if report and isinstance(report.result_json, dict) else {}
    if field == 'frame_relpath':
        path = _safe_stored_path(result.get(field))
    else:
        path = _safe_local_path(result.get('renamed_path'))
    if path is None:
        fail(404, 'file_not_found', '文件不存在或已被清理')
    return path, result


@router.get('/batches/{batch_id}/items/{media_id}/download')
def download_renamed(batch_id: str, media_id: str, db: Session = Depends(get_db)):
    path, result = _item_file(db, batch_id, media_id, 'renamed_path')
    media = db.get(MediaAsset, media_id)
    return FileResponse(path, media_type=media.mime_detected if media else 'application/octet-stream',
                        filename=result.get('renamed_name') or path.name)


@router.get('/batches/{batch_id}/items/{media_id}/frame')
def download_frame(batch_id: str, media_id: str, db: Session = Depends(get_db)):
    path, _ = _item_file(db, batch_id, media_id, 'frame_relpath')
    return FileResponse(path, media_type='image/jpeg',
                        headers={'Cache-Control': 'private, max-age=600'})


@router.post('/batches/{batch_id}/items/{media_id}/rename')
def rename_item(batch_id: str, media_id: str, payload: RenameRequest, db: Session = Depends(get_db)):
    """人工改名：没有识别出来的视频，看截图后由用户填名称，直接改磁盘文件。"""
    jobs = _jobs(db, batch_id)
    job = next((item for item in jobs if item.media_id == media_id), None)
    if job is None:
        fail(404, 'item_not_found', '该批次里没有这个视频')
    if job.status not in {'succeeded', 'failed'}:
        fail(409, 'item_not_ready', '该视频还在处理中')
    report = _report(db, job)
    result = dict(report.result_json) if report and isinstance(report.result_json, dict) else {}
    media = db.get(MediaAsset, media_id)
    if media is None:
        fail(404, 'item_not_found', '视频记录不存在')

    current = Path(result.get('renamed_path') or media.storage_relpath or '')
    if not current.is_file():
        fail(404, 'file_not_found', '录像文件不存在或路径不可用')

    text = payload.name.strip()
    suffix = Path(text).suffix.lower()
    if not suffix:
        # 没写扩展名就沿用当前文件扩展名，避免把视频改成无后缀文件
        suffix = current.suffix
        text = f'{text}{suffix}'
    elif suffix != current.suffix.lower():
        fail(422, 'extension_changed', f'扩展名必须是 {current.suffix}，避免文件打不开')
    if not re.fullmatch(r'\.[a-z0-9]{1,8}', suffix):
        fail(422, 'invalid_name', '文件名不合法')

    safe = Path(text)
    if safe.name != text or text in {'.', '..'}:
        fail(422, 'invalid_name', '文件名不能包含路径分隔符')
    stem = video_naming.safe_component(safe.stem, '视频')
    final_name = f'{video_naming.truncate_utf8(stem, 180)}{suffix}'

    target = current.parent / final_name
    if target != current:
        if target.exists():
            fail(409, 'name_taken', f'同目录已有同名文件：{final_name}')
        try:
            current.replace(target)
        except OSError as exc:
            logger.warning('manual rename failed media=%s: %s', media_id, exc)
            fail(409, 'rename_failed', f'改名失败：{exc}')

    result.update({
        'renamed_name': target.name,
        'renamed_path': str(target),
        'manual_renamed': True,
        'needs_review': False,
        'ocr_status': result.get('ocr_status') or 'not_checked',
    })
    warnings = [w for w in (result.get('warnings') or []) if 'OCR' not in str(w)]
    result['warnings'] = warnings
    report = report or InspectionReport(
        id=str(uuid4()), job_id=job.id, media_id=media.id,
        schema_version=video_naming.SCHEMA_VERSION, module_version=video_naming.MODULE_VERSION)
    report.result_json = result
    db.add(report)
    media.storage_relpath = str(target)
    job.status = 'succeeded'
    job.error_code = None
    job.error_message = None
    db.commit()
    try:
        video_naming.finalize_folders(db, batch_id)
    except Exception:
        logger.exception('manual rename finalize folders failed batch=%s', batch_id)
    return _item(db, job)
