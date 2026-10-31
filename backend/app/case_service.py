"""案件（Case）的创建与删除。

删除边界是这里最重要的约定：

* 只清理「上传到主系统的要素文件」——`media_assets` 中归属该案件的画面/片段，
  以及由这些文件派生的作业、预检报告、人工绑定/参考/比对记录。
* 不清理录像资源。录像文件属于录像资源侧，主系统只读：删除案件只移除该案件的
  「录像目录登记 + 扫描作业 + 扫描结果行」（见 `release_recording_resources`），
  磁盘上的录像文件、目录结构和文件名都不会被改动。
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    AnalysisJob,
    CameraBindingDecision,
    CameraViewReference,
    Case,
    InspectionReport,
    MediaAsset,
    RecordingClip,
    RecordingScanJob,
    RecordingSource,
    ViewComparisonRecord,
)

NAME_MIN = 2
NAME_MAX = 120
DESCRIPTION_MAX = 500


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_case_code(db: Session, now: datetime | None = None) -> str:
    """按天生成可读案件编号：CASE-YYYYMMDD-01；同日重复时顺延。"""
    now = now or utcnow()
    prefix = f'CASE-{now:%Y%m%d}-'
    existing = set(db.scalars(select(Case.code).where(Case.code.like(f'{prefix}%'))))
    seq = 1
    while f'{prefix}{seq:02d}' in existing:
        seq += 1
    return f'{prefix}{seq:02d}'


def create_case(db: Session, name: str, description: str = '', code: str | None = None) -> Case:
    case = Case(
        id=str(uuid4()),
        code=(code or '').strip() or make_case_code(db),
        name=name.strip(),
        description=description.strip(),
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def case_media_stats(db: Session, case_id: str) -> tuple[int, int]:
    count, total = db.execute(
        select(func.count(MediaAsset.id), func.coalesce(func.sum(MediaAsset.size_bytes), 0))
        .where(MediaAsset.case_id == case_id)
    ).one()
    return int(count), int(total)


def release_recording_resources(db: Session, case: Case) -> None:
    """删除案件时移除该案件的录像目录登记与扫描结果，但绝不碰录像文件。

    录像文件属于录像资源侧，主系统只读；这里删的是「主系统自己记的目录指针、
    扫描作业与扫描出来的片段行」，删除后案件消失，磁盘上的录像原样保留。
    """
    source_ids = list(db.scalars(
        select(RecordingSource.id).where(RecordingSource.case_id == case.id)))
    if not source_ids:
        return
    db.execute(delete(RecordingScanJob).where(RecordingScanJob.source_id.in_(source_ids)))
    db.execute(delete(RecordingClip).where(RecordingClip.source_id.in_(source_ids)))
    db.execute(delete(RecordingSource).where(RecordingSource.id.in_(source_ids)))


def _storage_file(relpath: str):
    root = settings.storage_root.resolve()
    target = (root / relpath).resolve()
    return target if target.is_relative_to(root) else None


def purge_case_media(db: Session, case: Case) -> dict:
    """删除案件内上传的要素文件及其派生记录，返回清理统计。

    先删库再删磁盘：数据库是权威状态，磁盘删除失败只做记录，不影响事务结果。
    """
    media_ids = list(db.scalars(select(MediaAsset.id).where(MediaAsset.case_id == case.id)))
    stats = {
        'case_id': case.id,
        'media_files_removed': 0,
        'bytes_removed': 0,
        'media_files_missing': 0,
        'recordings_preserved': True,
    }
    if media_ids:
        report_ids = list(db.scalars(
            select(InspectionReport.id).where(InspectionReport.media_id.in_(media_ids))
        ))
        reference_ids = list(db.scalars(
            select(CameraViewReference.id).where(CameraViewReference.media_id.in_(media_ids))
        ))
        if report_ids or reference_ids:
            conditions = []
            if report_ids:
                conditions.append(ViewComparisonRecord.inspection_id.in_(report_ids))
            if reference_ids:
                conditions.append(ViewComparisonRecord.reference_id.in_(reference_ids))
            db.execute(delete(ViewComparisonRecord).where(*conditions))
        if report_ids:
            db.execute(delete(CameraBindingDecision).where(
                CameraBindingDecision.inspection_id.in_(report_ids)))
            db.execute(delete(InspectionReport).where(InspectionReport.id.in_(report_ids)))
        db.execute(delete(CameraViewReference).where(CameraViewReference.id.in_(reference_ids)))
        db.execute(delete(AnalysisJob).where(AnalysisJob.media_id.in_(media_ids)))

        media_rows = list(db.scalars(select(MediaAsset).where(MediaAsset.id.in_(media_ids))))
        for media in media_rows:
            stats['bytes_removed'] += media.size_bytes
        paths = [(media.storage_relpath, media.size_bytes) for media in media_rows]
        db.execute(delete(MediaAsset).where(MediaAsset.id.in_(media_ids)))
    else:
        paths = []

    # 录像资源不随案件删除：这里只做占位，不碰任何录像文件。
    release_recording_resources(db, case)
    db.delete(case)
    db.commit()

    for relpath, _size in paths:
        path = _storage_file(relpath)
        if path is None:
            stats['media_files_missing'] += 1
            continue
        try:
            path.unlink(missing_ok=True)
            if path.exists():
                stats['media_files_missing'] += 1
            else:
                stats['media_files_removed'] += 1
        except OSError:
            # 文件被占用或权限不足时保持库内已删除，交给运维清理残留
            stats['media_files_missing'] += 1
    return stats
