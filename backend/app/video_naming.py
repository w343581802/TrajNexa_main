"""视频命名工具：读开头几秒角标，OCR 摄像头名称 + 时间，直接重命名本地录像并出表格。

前端只传本地路径 / 目录；API 展开目录，worker 读取真实文件，
按 OCR 结果重命名原文件（不改内容、不转码），截图留在 storage 里做证据。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import csv
import io
import json
import logging
import re
import unicodedata
import uuid

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import overlay_identity, recordings
from app.camera_catalog import load_catalog
from app.config import settings
from app.models import AnalysisJob, InspectionReport, MediaAsset

logger = logging.getLogger(__name__)

PREFIX = 'video_naming:'
SCHEMA_VERSION = 'video-naming-1'
MODULE_VERSION = 'video-naming-0.1'

_INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_TIME = re.compile(r'[\s_-]*\d{8}[\s_-]?\d{6}$')


class VideoNamingError(ValueError):
    """可以安全展示给用户的命名失败。"""


def batch_id_for_job(module: str) -> str:
    if not module.startswith(PREFIX):
        raise VideoNamingError('not_a_video_naming_job')
    value = module[len(PREFIX):]
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise VideoNamingError('invalid_batch_id') from None


def batch_module(batch_id: str) -> str:
    try:
        normalised = str(uuid.UUID(batch_id))
    except ValueError:
        raise VideoNamingError('invalid_batch_id') from None
    return f'{PREFIX}{normalised}'


def safe_component(value: str, fallback: str = '未命名') -> str:
    """Windows / Linux 都安全的一段文件名，不保留路径分隔符。"""
    value = unicodedata.normalize('NFKC', value or '')
    value = _INVALID_FILE_CHARS.sub('_', value)
    value = re.sub(r'\s+', ' ', value).strip(' ._-')
    value = truncate_utf8(value, 120).strip(' ._-')
    return value or fallback


def truncate_utf8(value: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断，避免长中文名超过文件系统 NAME_MAX。"""
    raw = value.encode('utf-8')
    if len(raw) <= max_bytes:
        return value
    return raw[:max_bytes].decode('utf-8', 'ignore').rstrip(' ._-')


def compact_time(value: str | None) -> str:
    if not value:
        return ''
    try:
        return datetime.fromisoformat(value).strftime('%Y%m%d_%H%M%S')
    except ValueError:
        return ''


def build_renamed_name(original_name: str, camera_name: str | None,
                       capture_time: str | None, suffix: str | None = None) -> str:
    """命名口径：摄像头名称_YYYYMMDD_HHMMSS.ext；缺什么就明确标出来。"""
    original = Path(original_name)
    detected = (suffix or original.suffix or '').lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}', detected):
        detected = '.mp4'
    if camera_name:
        base = safe_component(_TRAILING_TIME.sub('', camera_name), '摄像头')
    else:
        base = f'{safe_component(original.stem, "视频")}_OCR未识别'
    stamp = compact_time(capture_time)
    if stamp:
        base = f'{base}_{stamp}'
    else:
        base = f'{base}_时间未识别'
    return f'{truncate_utf8(base, 180)}{detected}'


def unique_target(directory: Path, name: str) -> Path:
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(2, 1000):
        candidate = directory / f'{stem}_{index}{suffix}'
        if not candidate.exists():
            return candidate
    raise VideoNamingError('too_many_name_collisions')


def _load_name_catalog():
    try:
        catalog = load_catalog()
        return recordings.name_index(catalog), catalog
    except Exception:
        # 目录不可用不能阻断命名；只是没有台账名称可对齐
        return {}, None


# 证据截图取帧口径与录像资源 / 添加要素共用（见 overlay_identity.EVIDENCE_OFFSETS）：
# 开头可能是黑场或切换画面，多试几个时刻；ffmpeg 的灰占位帧已被过滤成 None。
EVIDENCE_OFFSETS = overlay_identity.EVIDENCE_OFFSETS


def _save_evidence_frame(path: Path, batch_dir: Path, media_id: str) -> str | None:
    frame = overlay_identity.grab_evidence_frame(path, offsets=EVIDENCE_OFFSETS)
    if frame is None:
        # 一帧都取不到：不写假截图，交给上层标 no_frame 并提示人工核对
        return None
    frame_dir = batch_dir / 'frames'
    frame_dir.mkdir(parents=True, exist_ok=True)
    target = frame_dir / f'{media_id}.jpg'
    ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
    if not ok:
        return None
    target.write_bytes(encoded.tobytes())
    try:
        return target.relative_to(settings.storage_root.resolve()).as_posix()
    except ValueError:
        return None


def batch_dir(batch_id: str) -> Path:
    return settings.storage_root.resolve() / 'video-tools' / batch_id


def write_batch_meta(batch_id: str, roots: list[Path], rename_folders: bool) -> None:
    directory = batch_dir(batch_id)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        'roots': [str(path.resolve()) for path in roots],
        'rename_folders': bool(rename_folders),
    }
    (directory / 'batch.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def read_batch_meta(batch_id: str) -> dict:
    path = batch_dir(batch_id) / 'batch.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def compact_time_dense(value: str | None) -> str:
    if not value:
        return ''
    try:
        return datetime.fromisoformat(value).strftime('%Y%m%d%H%M%S')
    except ValueError:
        return ''


def folder_target_name(camera_name: str, starts: list[str], ends: list[str]) -> str:
    """文件夹口径：摄像头名称_开始时间至结束时间（同目录视频必须同一摄像头）。"""
    start = compact_time_dense(min(starts))
    end = compact_time_dense(max(ends))
    return f'{safe_component(camera_name)}_{start}至{end}'


def finalize_folders(db: Session, batch_id: str) -> int:
    """所有视频处理完后，按目录内共同的摄像头名称重命名子文件夹。

    只处理输入根目录**下面**的文件夹（根目录本身不改），目录内视频名称不一致则跳过。
    """
    meta = read_batch_meta(batch_id)
    if not meta.get('rename_folders'):
        return 0
    jobs = list(db.scalars(select(AnalysisJob).where(AnalysisJob.module == batch_module(batch_id))))
    if not jobs or any(job.status != 'succeeded' for job in jobs):
        return 0

    entries: list[tuple[AnalysisJob, MediaAsset | None, InspectionReport, dict]] = []
    for job in jobs:
        report = db.scalar(select(InspectionReport).where(InspectionReport.job_id == job.id))
        media = db.get(MediaAsset, job.media_id)
        if report is None or not isinstance(report.result_json, dict) or media is None:
            return 0
        entries.append((job, media, report, dict(report.result_json)))

    roots = [Path(value).resolve() for value in meta.get('roots') or []]
    groups: dict[Path, list[tuple[AnalysisJob, MediaAsset | None, InspectionReport, dict]]] = {}
    for entry in entries:
        result = entry[3]
        original_path = result.get('original_path')
        if not original_path:
            return 0
        if result.get('folder_renamed_path'):
            continue
        folder = Path(original_path).parent
        if not any(folder != root and folder.is_relative_to(root) for root in roots):
            continue
        groups.setdefault(folder, []).append(entry)

    renamed: list[tuple[Path, Path]] = []
    changed = 0
    try:
        for folder, group in groups.items():
            if not folder.is_dir():
                continue
            cameras = {entry[3].get('camera_name') for entry in group}
            if len(cameras) != 1 or not next(iter(cameras)):
                logger.info('skip folder rename (mixed/no camera): %s', folder)
                continue
            starts = [entry[3].get('capture_time') for entry in group if entry[3].get('capture_time')]
            if not starts:
                continue
            ends: list[str] = []
            for entry in group:
                start = entry[3].get('capture_time')
                if not start:
                    continue
                try:
                    moment = datetime.fromisoformat(start)
                except ValueError:
                    continue
                duration = entry[3].get('duration_seconds') or 0
                ends.append((moment + timedelta(seconds=float(duration))).isoformat(timespec='seconds'))
            if not ends:
                continue
            name = folder_target_name(next(iter(cameras)), starts, ends)
            if name == folder.name:
                continue
            target = unique_target(folder.parent, name)
            folder.replace(target)
            renamed.append((folder, target))
            for _, media, report, result in group:
                result['folder_original_path'] = str(folder)
                result['folder_renamed_path'] = str(target)
                result['folder_renamed_name'] = target.name
                file_name = Path(result.get('renamed_path') or result.get('original_path')).name
                new_file = target / file_name
                result['renamed_path'] = str(new_file)
                report.result_json = result
                media.storage_relpath = str(new_file)
            changed += 1
        if changed:
            db.commit()
    except Exception:
        db.rollback()
        for old, new in reversed(renamed):
            try:
                if new.is_dir() and not old.exists():
                    new.replace(old)
            except OSError:
                logger.exception('rollback folder rename failed: %s -> %s', new, old)
        raise
    return changed


def process_media(media: MediaAsset, path: Path, batch_id: str) -> dict:
    """OCR 开头几秒 → 生成新文件名 → 原地重命名本地录像，返回可入报表的结果。

    截图先取、再改名：截图失败不会把文件改到一半。数据库提交失败时由 worker
    调 revert_rename 把名字改回去。
    """
    if not path.is_file():
        raise VideoNamingError('视频文件不存在')
    batch_dir = settings.storage_root.resolve() / 'video-tools' / batch_id
    index, catalog = _load_name_catalog()
    outcome = recordings.inspect_first_seconds(path, index, catalog)

    camera_name = outcome.get('camera_name') or None
    capture_time = outcome.get('start_local') or None
    time_source = outcome.get('start_source') or ''
    ocr_status = outcome.get('ocr_status') or 'not_checked'
    match_status = outcome.get('match_status') or 'not_checked'
    needs_review = not (camera_name and capture_time and time_source == 'ocr')
    warnings: list[str] = []
    if ocr_status in {'disabled', 'unavailable', 'error', 'no_frame'}:
        warnings.append(f'OCR {ocr_status}，未取得可靠角标文字')
    if not camera_name:
        warnings.append('未从画面角标识别到摄像头名称')
    if ocr_status == 'no_frame':
        warnings.append('开头几秒取不到可用画面，已按原文件名保留，请人工核对')
    if not capture_time:
        warnings.append('未识别到完整拍摄时间')
    elif time_source == 'filename':
        warnings.append('拍摄时间来自文件名兜底，未由角标确认')

    original_name = media.original_name or path.name
    original_path = path
    renamed_name = build_renamed_name(original_name, camera_name, capture_time, path.suffix)
    # 截图先在原文件上取，避免改名后截图失败
    frame_relpath = _save_evidence_frame(path, batch_dir, media.id)
    if frame_relpath is None:
        # 只有真的取不到画面才顺带影响 needs_review（此时本来也没有名称/时间）；
        # 单纯截图落盘失败只记提示，不改变已识别出来的结论。
        warnings.append('证据截图未能保存（未取到可用画面）')
        if ocr_status == 'no_frame':
            needs_review = True
    target = path
    if renamed_name != path.name:
        target = unique_target(path.parent, renamed_name)
        path.replace(target)
        media.storage_relpath = str(target)

    return {
        'batch_id': batch_id,
        'media_id': media.id,
        'original_name': original_name,
        'original_path': str(original_path),
        'renamed_name': target.name,
        'renamed_path': str(target),
        'camera_name': camera_name,
        'camera_uuid': outcome.get('camera_uuid'),
        'capture_time': capture_time,
        'time_source': time_source,
        'ocr_status': ocr_status,
        'match_status': match_status,
        'duration_seconds': outcome.get('duration_seconds'),
        'size_bytes': media.size_bytes,
        'frame_relpath': frame_relpath,
        'needs_review': needs_review,
        'warnings': warnings,
    }


def revert_rename(result: dict) -> None:
    """数据库没提交成功时，把已经改名的文件改回原名。"""
    original = Path(result.get('original_path') or '')
    renamed = Path(result.get('renamed_path') or '')
    if original == renamed or not renamed.is_file() or original.exists():
        return
    renamed.replace(original)


def _csv_cell(value) -> str:
    """避免以 = + - @ 开头的 OCR/文件名在 Excel 里被当成公式。"""
    text = str(value or '')
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text


def csv_text(rows: list[dict]) -> str:
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow(['序号', '原始文件名', '新文件名', '原路径', '新路径', '文件夹原名', '文件夹新名',
                     '摄像头名称', '拍摄时间', '时间来源', 'OCR状态', '匹配状态', '时长(秒)', '大小(MB)',
                     '处理状态', '需人工核对', '备注'])
    source_text = {'ocr': '画面角标', 'filename': '文件名兜底'}
    status_text = {'queued': '排队中', 'running': '处理中', 'succeeded': '已完成', 'failed': '失败'}
    ocr_text = {'ran': '已识别', 'disabled': 'OCR未启用', 'unavailable': 'OCR不可用',
                'error': 'OCR出错', 'no_frame': '未取到画面', 'not_checked': '未检查'}
    match_text = {'matched': '已匹配', 'unmatched': '未匹配', 'no_name': '无名称',
                  'ambiguous': '多重匹配', 'catalog_unavailable': '目录不可用',
                  'ocr_disabled': 'OCR未启用'}
    for index, row in enumerate(rows, 1):
        status = row.get('status') or ''
        writer.writerow([
            index,
            _csv_cell(row.get('original_name')),
            _csv_cell(row.get('renamed_name')),
            _csv_cell(row.get('original_path')),
            _csv_cell(row.get('renamed_path')),
            _csv_cell(row.get('folder_original_path')),
            _csv_cell(row.get('folder_renamed_name')),
            _csv_cell(row.get('camera_name')),
            _csv_cell(row.get('capture_time')),
            _csv_cell(source_text.get(row.get('time_source') or '', row.get('time_source') or '')),
            _csv_cell(ocr_text.get(row.get('ocr_status') or '', row.get('ocr_status') or '')),
            _csv_cell(match_text.get(row.get('match_status') or '', row.get('match_status') or '')),
            row.get('duration_seconds') if row.get('duration_seconds') is not None else '',
            round((row.get('size_bytes') or 0) / 1024 / 1024, 2),
            _csv_cell(status_text.get(status, status)),
            '是' if (row.get('needs_review') or status == 'failed') else '否',
            _csv_cell('；'.join(row.get('warnings') or []) or (row.get('error') or '')),
        ])
    return output.getvalue()
