from datetime import datetime, timezone
from sqlalchemy import (String, Integer, Float, DateTime, BigInteger, ForeignKey, JSON, Text,
                        Index, Boolean, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class Case(Base):
    """案件：主系统里组织研判工作的最小单元，只持有要素文件（上传后的画面/片段）。

    录像资源属于独立的录像资源服务，案件不保存也不删除录像文件；
    删除案件时只清理 media_assets 中归属该案件的上传文件。
    """
    __tablename__ = 'cases'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class MediaAsset(Base):
    __tablename__ = 'media_assets'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 归属案件；历史素材可能没有归属（空值），删除案件时不会波及
    case_id: Mapped[str | None] = mapped_column(ForeignKey('cases.id'), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(12))
    original_name: Mapped[str] = mapped_column(String(255))
    mime_detected: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_relpath: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class AnalysisJob(Base):
    __tablename__ = 'analysis_jobs'
    __table_args__ = (Index('ix_jobs_status_created', 'status', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    media_id: Mapped[str] = mapped_column(ForeignKey('media_assets.id'))
    module: Mapped[str] = mapped_column(String(60), default='media_inspector_stub')
    status: Mapped[str] = mapped_column(String(20), default='queued')
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

class InspectionReport(Base):
    __tablename__ = 'inspection_reports'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('analysis_jobs.id'), unique=True)
    media_id: Mapped[str] = mapped_column(ForeignKey('media_assets.id'))
    schema_version: Mapped[str] = mapped_column(String(20))
    module_version: Mapped[str] = mapped_column(String(50))
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class CameraBindingDecision(Base):
    """Append-only human review, distinct from immutable media/OCR output."""
    __tablename__ = 'camera_binding_decisions'
    __table_args__ = (Index('ix_camera_binding_inspection_created', 'inspection_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey('inspection_reports.id'))
    decision: Mapped[str] = mapped_column(String(12))  # confirm/revoke
    camera_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    camera_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_revision: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_from_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CameraViewReference(Base):
    """Human-designated image reference, never overwrites original media or Map data."""
    __tablename__ = 'camera_view_references'
    __table_args__ = (Index('ix_view_refs_camera', 'camera_uuid', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    camera_uuid: Mapped[str] = mapped_column(String(36))
    media_id: Mapped[str] = mapped_column(ForeignKey('media_assets.id'))
    catalog_revision: Mapped[str] = mapped_column(String(120))
    catalog_sha256: Mapped[str] = mapped_column(String(64))
    media_sha256: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ViewComparisonRecord(Base):
    """Immutable comparison result, separate from the original inspector report."""
    __tablename__ = 'view_comparison_records'
    __table_args__ = (Index('ix_view_checks_inspection', 'inspection_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey('inspection_reports.id'))
    reference_id: Mapped[str] = mapped_column(ForeignKey('camera_view_references.id'))
    camera_uuid: Mapped[str] = mapped_column(String(36))
    catalog_revision: Mapped[str] = mapped_column(String(120))
    binding_decision_id: Mapped[str] = mapped_column(ForeignKey('camera_binding_decisions.id'))
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecordingSource(Base):
    """案件选中的录像目录。主系统只读这个目录，不复制、不改名、不删除录像文件。

    目录是相对 ``settings.recordings_root`` 的相对路径，避免把宿主机绝对路径写进库。
    ``auto_scan`` 打开后由 worker 按间隔做增量扫描（只处理新增或大小/修改时间变化的文件）。
    """
    __tablename__ = 'recording_sources'
    __table_args__ = (UniqueConstraint('case_id', 'path', name='uq_recording_source_case_path'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), index=True)
    path: Mapped[str] = mapped_column(String(512))
    label: Mapped[str] = mapped_column(String(255), default='')
    auto_scan: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default='idle')
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class RecordingScanJob(Base):
    """录像目录扫描作业：由 API 手动触发或 worker 按自动扫描间隔入队。"""
    __tablename__ = 'recording_scan_jobs'
    __table_args__ = (Index('ix_recording_jobs_status_created', 'status', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('recording_sources.id'), index=True)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, default=0)
    added_files: Mapped[int] = mapped_column(Integer, default=0)
    updated_files: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class RecordingClip(Base):
    """一段录像的扫描结果：开始时间（画面角标，读不到才退回文件名）+ 摄像头名称匹配。

    只记录「读到什么」：不推断结束时间。文件容器时长单独存 ``duration_seconds``，
    供研判真正读取这段录像时累计摄像头覆盖时长使用，不当作结束时间展示。
    """
    __tablename__ = 'recording_clips'
    __table_args__ = (
        UniqueConstraint('source_id', 'relpath', name='uq_recording_clip_source_path'),
        Index('ix_recording_clips_camera', 'camera_uuid', 'start_local'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey('recording_sources.id'), index=True)
    relpath: Mapped[str] = mapped_column(String(512))
    file_name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime_ns: Mapped[int] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_local: Mapped[str | None] = mapped_column(String(32), nullable=True)
    start_source: Mapped[str] = mapped_column(String(20), default='')
    camera_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    camera_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name_source: Mapped[str] = mapped_column(String(20), default='none')
    match_status: Mapped[str] = mapped_column(String(30), default='unscanned')
    ocr_status: Mapped[str] = mapped_column(String(20), default='not_checked')
    review_kind: Mapped[str] = mapped_column(String(20), default='none')
    reviewer_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    match_suggestions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetObservation(Base):
    """M4a append-only, human-selected ROI; no cross-camera identity assertion.

    Uploads are case-owned; the Map editor does not receive these rows.
    """
    __tablename__ = 'target_observations'
    __table_args__ = (
        Index('ix_target_observations_case_created', 'case_id', 'created_at'),
        Index('ix_target_observations_media_created', 'media_id', 'created_at'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), nullable=False)
    media_id: Mapped[str] = mapped_column(ForeignKey('media_assets.id'), nullable=False)
    object_class: Mapped[str] = mapped_column(String(24), nullable=False)
    analysis_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    review_note: Mapped[str] = mapped_column(String(500), nullable=False)
    review_state: Mapped[str] = mapped_column(String(32), default='pending')
    source_inspection_id: Mapped[str | None] = mapped_column(
        ForeignKey('inspection_reports.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
