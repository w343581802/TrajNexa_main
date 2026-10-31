"""录像资源接线：只读扫描服务端录像目录。

扫描口径（按产品要求固定）：
* 只读每段录像**开头几秒**的画面角标，取「摄像头名称 + 开始时间」；
* **不推断结束时间**：结束时间在研判真正读取这段录像时按读到的内容累计摄像头时长；
* 容器时长（``duration_seconds``）只是技术字段，供后续累计覆盖时长用，不当作结束时间；
* 摄像头名称只做台账**规范化精确匹配且唯一命中**才写 ``camera_uuid``；
  多条同名或名称相近时只给建议，绝不猜身份（与 M1c 口径一致）。

安全边界：
* 目录必须落在 ``settings.recordings_root`` 之内，越界一律拒绝；
* 扫描只做目录遍历、``stat``、ffprobe / ffmpeg 读帧，**不写、不改名、不删除**任何录像文件。
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import media_inputs, overlay_identity
from app.camera_catalog import CatalogError, canonical_name, load_catalog
from app.camera_name_match import suggest_names
from app.config import settings
from app.models import RecordingClip, RecordingScanJob, RecordingSource
# 抽帧 / 角标识别 / 文件名兜底都在 overlay_identity 里，这里仍对外导出，老调用方不用改。
from app.overlay_identity import filename_start, probe_duration  # noqa: F401

VIDEO_SUFFIXES = media_inputs.VIDEO_SUFFIXES
# 只看开头几秒，读到「时间 + 名称」就提前结束，避免拖慢批量扫描。
FIRST_SECONDS_OFFSETS = overlay_identity.IDENTITY_OFFSETS
MAX_LISTED_DIRS = 200
_SAFE_REL = re.compile(r'^[^\\\x00]*$')


class RecordingPathError(ValueError):
    """目录超出录像根目录或不可访问。"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def recordings_root() -> Path:
    return Path(settings.recordings_root).resolve()


def resolve_rel(rel: str | None) -> Path:
    """把相对路径解析成绝对路径，并确保仍在录像根目录内。"""
    rel = (rel or '').strip().replace('\\', '/').strip('/')
    if rel and not _SAFE_REL.match(rel):
        raise RecordingPathError('目录路径不合法')
    root = recordings_root()
    target = (root / rel).resolve()
    if target != root and not target.is_relative_to(root):
        raise RecordingPathError('目录超出录像根目录')
    return target


def relative_path(path: Path) -> str:
    root = recordings_root()
    if path.resolve() == root:
        return ''
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:  # 解析到根目录之外（例如软链接指向别处）
        raise RecordingPathError('目录超出录像根目录') from exc


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def _scandir(path: Path):
    try:
        return sorted(path.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        return []


def list_dir(rel: str | None) -> dict:
    """列出一级子目录与当前目录里的视频文件，供前端「添加视频」挑目录。"""
    base = resolve_rel(rel)
    if not base.is_dir():
        raise RecordingPathError('目录不存在或不可访问')
    dirs = []
    for child in _scandir(base):
        if len(dirs) >= MAX_LISTED_DIRS:
            break
        if child.name.startswith('.') or not child.is_dir():
            continue
        videos = [item for item in _scandir(child) if item.is_file() and is_video(item)]
        dirs.append({'name': child.name, 'path': relative_path(child), 'video_count': len(videos)})
    videos = [
        {'name': item.name, 'path': relative_path(item),
         'size_bytes': item.stat().st_size if item.is_file() else 0}
        for item in _scandir(base)
        if item.is_file() and is_video(item)
    ][:100]
    root = recordings_root()
    return {
        'root': str(root),
        'path': relative_path(base),
        'parent': '' if base == root else relative_path(base.parent),
        'dirs': dirs,
        'videos': videos,
    }


def collect_videos(base: Path, limit: int) -> list[Path]:
    """递归收集视频文件（稳定顺序、跳过隐藏目录、带上限）。

    只收 ``base`` 之内的文件：文件软链接如果指向根目录之外，一律跳过，避免读到根目录外的内容。
    """
    found: list[Path] = []
    base_real = base.resolve()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(name for name in dirnames if not name.startswith('.'))
        for name in sorted(filenames):
            if name.startswith('.'):
                continue
            path = Path(dirpath) / name
            if not is_video(path):
                continue
            try:
                if not path.resolve().is_relative_to(base_real):
                    continue
            except OSError:
                continue
            found.append(path)
            if len(found) >= limit:
                return found
    return found


def name_index(catalog) -> dict[str, list[dict]]:
    """canonical(名称/别名) -> 台账镜头列表；只收启用镜头。"""
    index: dict[str, list[dict]] = {}
    for camera in catalog.cameras:
        if camera.get('active') is False:
            continue
        for value in [camera.get('display_name'), *(camera.get('aliases') or [])]:
            key = canonical_name(value or '')
            if not key:
                continue
            bucket = index.setdefault(key, [])
            if all(item['camera_uuid'] != camera['camera_uuid'] for item in bucket):
                bucket.append(camera)
    return index


def match_camera(names: list[str], index: dict[str, list[dict]], catalog) -> dict:
    """名称 -> 台账匹配结论：唯一精确命中才绑定 UUID，其余只给建议。"""
    unique_names = sorted({name for name in names if name})
    result = {'camera_name': unique_names[0] if unique_names else None,
              'camera_uuid': None, 'match_status': 'no_name', 'suggestions': []}
    if not unique_names:
        return result
    if catalog is None:
        result['match_status'] = 'catalog_unavailable'
        return result
    keys = {canonical_name(name) for name in unique_names}
    hits: dict[str, dict] = {}
    for key in keys:
        for camera in index.get(key, []):
            hits[camera['camera_uuid']] = camera
    if len(hits) == 1:
        camera = next(iter(hits.values()))
        result['camera_uuid'] = camera['camera_uuid']
        result['camera_name'] = camera.get('display_name') or result['camera_name']
        result['match_status'] = 'matched'
        return result
    if len(hits) > 1:
        result['match_status'] = 'ambiguous'
        result['suggestions'] = [
            {'camera_uuid': item['camera_uuid'], 'display_name': item.get('display_name') or '',
             'score': 1.0, 'reviewed': bool(item.get('reviewed_at'))}
            for item in hits.values()
        ][:5]
        return result
    active = [item for item in (catalog.cameras if catalog else []) if item.get('active') is not False]
    suggestions = suggest_names(unique_names, active)
    result['match_status'] = 'unmatched'
    result['suggestions'] = [
        {'camera_uuid': item['camera_uuid'], 'display_name': item['display_name'],
         'score': item['score'], 'reviewed': item['reviewed']}
        for item in suggestions
    ]
    return result


def inspect_first_seconds(path: Path, index: dict[str, list[dict]], catalog) -> dict:
    """读开头几秒：开始时间（角标优先、文件名兜底）+ 摄像头名称匹配。

    识别本身交给 overlay_identity（录像资源 / 视频命名 / 添加要素共用同一套口径），
    这里只负责把「候选名称」拿去台账匹配。
    """
    identity = overlay_identity.identify_video(path)
    outcome = {
        'duration_seconds': identity.duration_seconds,
        'start_local': identity.capture_time,
        'start_source': identity.time_source,
        'camera_name': None,
        'camera_uuid': None,
        'name_source': identity.name_source if identity.name_source != 'none' else 'ocr',
        'match_status': 'unscanned',
        'ocr_status': identity.ocr_status,
        'suggestions': [],
        'warnings': identity.warnings,
    }
    outcome.update(match_camera(identity.names, index, catalog))
    if identity.name_source != 'none':
        outcome['name_source'] = identity.name_source
    if outcome['match_status'] == 'no_name' and outcome['ocr_status'] == 'disabled':
        outcome['match_status'] = 'ocr_disabled'
    return outcome


def scan_source(db: Session, job: RecordingScanJob) -> dict:
    """扫描一个录像目录：只处理新增或大小/修改时间变化的文件。"""
    source = db.get(RecordingSource, job.source_id)
    if source is None:
        raise LookupError('录像目录不存在')
    base = resolve_rel(source.path)
    if not base.is_dir():
        raise RecordingPathError('录像目录不存在或不可访问')
    catalog = None
    index: dict[str, list[dict]] = {}
    try:
        catalog = load_catalog()
        index = name_index(catalog)
    except CatalogError:
        catalog = None
    files = collect_videos(base, settings.recordings_scan_max_files)
    existing = {
        clip.relpath: clip
        for clip in db.scalars(select(RecordingClip).where(RecordingClip.source_id == source.id))
    }
    job.total_files = len(files)
    job.processed_files = job.added_files = job.updated_files = 0
    source.last_status = 'running'
    source.last_error = None
    db.commit()
    added = updated = skipped = 0
    for position, path in enumerate(files, start=1):
        try:
            stat = path.stat()
        except OSError:
            job.processed_files = position
            continue
        relpath = relative_path(path)
        clip = existing.get(relpath)
        # 人工审核过的名称绑定不随自动扫描回滚：confirmed 保留人工结论，rejected 留待用户重审
        reviewed = clip is not None and clip.review_kind in ('confirmed', 'rejected')
        if clip is not None and clip.size_bytes == stat.st_size and clip.mtime_ns == stat.st_mtime_ns:
            # 文件没变就只顺手补救一类坏数据：旧版本会把容器 format 时长当成整天数存库，
            # 例如 30 分钟视频被写成 3.8 小时。正常记录不重复 probe，扫描开销不涨。
            if clip.duration_seconds is None or clip.duration_seconds >= 7200:
                # 与 identify_video 同一套时长链：先 ffprobe，读不出再用文件名「开始至结束」兜底
                refreshed = (overlay_identity.probe_duration(path)
                             or overlay_identity.filename_duration(path.name))
                if refreshed is not None:
                    clip.duration_seconds = refreshed
                    clip.scanned_at = utcnow()
                    updated += 1
            skipped += 1
            job.processed_files = position
            continue
        try:
            info = inspect_first_seconds(path, index, catalog)
        except Exception:
            # 单段失败不影响整批：记成 failed，下次扫描会重试（大小/时间未变也重试）
            info = {'duration_seconds': None, 'start_local': None, 'start_source': '',
                    'camera_name': None, 'camera_uuid': None,
                    'match_status': 'scan_error', 'ocr_status': 'error', 'suggestions': []}
        if clip is None:
            clip = RecordingClip(id=str(uuid4()), source_id=source.id,
                                 relpath=relpath, file_name=path.name,
                                 size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns,
                                 scanned_at=utcnow())
            db.add(clip)
            existing[relpath] = clip
            added += 1
        else:
            updated += 1
        clip.size_bytes = stat.st_size
        clip.mtime_ns = stat.st_mtime_ns
        clip.duration_seconds = info['duration_seconds']
        clip.start_local = info['start_local']
        clip.start_source = info['start_source']
        clip.ocr_status = info['ocr_status']
        clip.scanned_at = utcnow()
        if not reviewed:
            # 只有未人工审核的记录才接受自动名称匹配；人工决定（confirm/reject）保持原样
            clip.name_source = info.get('name_source', 'ocr')
            clip.camera_name = info['camera_name']
            clip.camera_uuid = info['camera_uuid']
            clip.match_status = info['match_status']
            clip.match_suggestions = info['suggestions'] or None
        job.processed_files = position
        job.added_files = added
        job.updated_files = updated
        if position % 5 == 0:
            db.commit()
    db.commit()
    source.last_scan_at = utcnow()
    source.last_status = 'succeeded'
    source.last_error = None
    db.commit()
    return {'total': len(files), 'added': added, 'updated': updated, 'skipped': skipped}


def enqueue_auto_scans(db: Session) -> int:
    """给开启了自动扫描、且已过扫描间隔的目录排一次增量扫描。"""
    interval = max(1, int(settings.recordings_scan_interval_seconds))
    created = 0
    sources = db.scalars(select(RecordingSource).where(RecordingSource.auto_scan.is_(True))).all()
    for source in sources:
        active = db.scalar(
            select(RecordingScanJob.id).where(
                RecordingScanJob.source_id == source.id,
                RecordingScanJob.status.in_(('queued', 'running'))).limit(1))
        if active:
            continue
        if source.last_scan_at is not None:
            last = source.last_scan_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (utcnow() - last).total_seconds() < interval:
                continue
        db.add(RecordingScanJob(id=str(uuid4()), source_id=source.id, status='queued'))
        created += 1
    if created:
        db.commit()
    return created


def case_summary(db: Session, case_id: str) -> dict:
    """案件维度的录像资源摘要：文件数、已读时间/名称数、匹配到的镜头 UUID。"""
    sources = db.scalars(
        select(RecordingSource).where(RecordingSource.case_id == case_id)
        .order_by(RecordingSource.created_at)).all()
    source_ids = [source.id for source in sources]
    clips = db.scalars(
        select(RecordingClip).where(RecordingClip.source_id.in_(source_ids))).all() if source_ids else []
    uuids = sorted({clip.camera_uuid for clip in clips if clip.camera_uuid})
    return {
        'case_id': case_id,
        'source_count': len(sources),
        'file_count': len(clips),
        'time_count': sum(1 for clip in clips if clip.start_local),
        'name_count': sum(1 for clip in clips if clip.camera_name),
        'matched_count': sum(1 for clip in clips if clip.match_status == 'matched'),
        'camera_uuids': uuids,
        'last_scan_at': max((source.last_scan_at for source in sources
                             if source.last_scan_at is not None), default=None),
        'scanning': any(source.last_status == 'running' for source in sources),
    }


def rematch_source(db: Session, source_id: str) -> dict:
    """重新匹配摄像头：拿已存的 OCR / 文件名兜底名称，对照当前台账重跑一次精确匹配。

    已有的人员审核结论（confirm/reject）不动；其余 clip 按新台账更新。
    不重新读视频文件、不跑 OCR，只重算名称到摄像头的绑定关系。
    """
    source = db.get(RecordingSource, source_id)
    if source is None:
        raise LookupError('录像目录不存在')
    try:
        catalog = load_catalog()
        index = name_index(catalog)
    except CatalogError:
        raise ValueError('catalog_unavailable') from None
    clips = db.scalars(select(RecordingClip).where(RecordingClip.source_id == source.id)).all()
    updated = unchanged = human_kept = 0
    for clip in clips:
        if clip.review_kind in ('confirmed', 'rejected'):
            human_kept += 1
            continue
        name = (clip.camera_name or '').strip()
        name_source = clip.name_source or 'none'
        if not name:
            label = overlay_identity.filename_camera_label(clip.file_name)
            if label:
                name = label
                name_source = 'filename'
        before = (clip.camera_uuid, clip.match_status)
        outcome = match_camera([name], index, catalog)
        clip.name_source = name_source
        clip.camera_name = outcome['camera_name']
        clip.camera_uuid = outcome['camera_uuid']
        clip.match_status = outcome['match_status']
        clip.match_suggestions = outcome['suggestions'] or None
        after = (clip.camera_uuid, clip.match_status)
        if before != after:
            updated += 1
        else:
            unchanged += 1
    db.commit()
    return {'source_id': source.id, 'updated': updated,
            'unchanged': unchanged, 'human_kept': human_kept}


def clip_frame(db: Session, clip_id: str) -> Path:
    """取这段录像的开头画面当人工核对的截图，缓存在 storage 下。

    只读录像文件，不写回、不改名、不删除任何录像。
    """
    clip = db.get(RecordingClip, clip_id)
    if clip is None:
        raise LookupError('录像片段不存在')
    source = db.get(RecordingSource, clip.source_id)
    if source is None:
        raise LookupError('录像目录不存在')
    # relpath 是相对录像根目录的路径（含源目录名），直接按根目录解析最稳
    path = resolve_rel(clip.relpath)
    if not path.is_file():
        raise LookupError('录像文件不存在或不可访问')
    cache_dir = settings.storage_root.resolve() / 'recording-frames'
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f'{clip.id}-v2.jpg'
    if target.is_file() and target.stat().st_mtime >= path.stat().st_mtime:
        return target
    # 开头常有花屏：沿 PREVIEW_OFFSETS 往后找一帧能看的，实在没有才判取不到
    frame = overlay_identity.grab_evidence_frame(path, offsets=overlay_identity.PREVIEW_OFFSETS)
    if frame is None:
        raise ValueError('frame_unavailable')
    import cv2
    ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
    if not ok:
        raise ValueError('frame_unavailable')
    target.write_bytes(encoded.tobytes())
    return target


def set_clip_name(db: Session, clip_id: str, *, name: str, note: str | None = None) -> RecordingClip:
    """人工核对后写入识别名称：改完立即按台账重新匹配摄像头。

    人工结论用 review_kind='confirmed' 标记，之后的自动扫描、重新匹配都不会覆盖；
    只改扫描结果，不动录像文件与文件名。
    """
    clip = db.get(RecordingClip, clip_id)
    if clip is None:
        raise LookupError('录像片段不存在')
    clean = (name or '').strip()
    clean_note = (note or '').strip() or None
    if not clean:
        clip.camera_name = None
        clip.camera_uuid = None
        clip.name_source = 'none'
        clip.match_status = 'no_name'
        clip.match_suggestions = None
    else:
        try:
            catalog = load_catalog()
            index = name_index(catalog)
        except CatalogError:
            catalog, index = None, {}
        outcome = match_camera([clean], index, catalog)
        clip.camera_name = outcome['camera_name'] or clean
        clip.camera_uuid = outcome['camera_uuid']
        clip.match_status = outcome['match_status']
        clip.match_suggestions = outcome['suggestions'] or None
        clip.name_source = 'manual'
    clip.review_kind = 'confirmed'
    clip.reviewer_note = clean_note
    db.commit()
    db.refresh(clip)
    return clip