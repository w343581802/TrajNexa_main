from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, BigInteger, ForeignKey, JSON, Text, Index, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class MediaAsset(Base):
    __tablename__ = 'media_assets'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
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
    """Append-only human review; original OCR reports remain immutable."""
    __tablename__ = 'camera_binding_decisions'
    __table_args__ = (Index('ix_camera_binding_inspection_created', 'inspection_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    inspection_id: Mapped[str] = mapped_column(ForeignKey('inspection_reports.id'))
    decision: Mapped[str] = mapped_column(String(12))
    camera_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    camera_name_snapshot: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_revision: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_from_ocr: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
