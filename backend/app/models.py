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


class TrackingRun(Base):
    """B8-B：案件内的持久化追踪任务（一次从已登记录像出发的连续搜索）。

    anchor_json 保存服务器核实的起点快照（观察、录像段、OSD 秒、摄像头标签），
    scope_json 保存预算与参数；progress_json 保存已分析窗口，用于重启后续跑。
    """
    __tablename__ = 'tracking_runs'
    __table_args__ = (Index('ix_tracking_runs_case_status', 'case_id', 'status', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey('target_observations.id'))
    status: Mapped[str] = mapped_column(String(24), default='queued')
    anchor_json: Mapped[dict] = mapped_column(JSON, default=dict)
    scope_json: Mapped[dict] = mapped_column(JSON, default=dict)
    progress_json: Mapped[dict] = mapped_column(JSON, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrackingWindow(Base):
    """一次窗口分析的实测记录（做了什么、耗时、是否覆盖失败）。"""
    __tablename__ = 'tracking_windows'
    __table_args__ = (Index('ix_tracking_windows_run_created', 'run_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('tracking_runs.id'), index=True)
    camera_label: Mapped[str] = mapped_column(String(120))
    clip_id: Mapped[str] = mapped_column(String(36))
    window_start: Mapped[float] = mapped_column(Float)
    window_end: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), default='unknown')
    tracks: Mapped[int] = mapped_column(Integer, default=0)
    cost_json: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackingCandidate(Base):
    """候选轨迹：默认 candidate_unverified，只有人工审核事件能改成 confirmed/rejected。"""
    __tablename__ = 'tracking_candidates'
    __table_args__ = (
        UniqueConstraint('run_id', 'candidate_key', name='uq_tracking_candidate_key'),
        Index('ix_tracking_candidates_run_state', 'run_id', 'state'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('tracking_runs.id'), index=True)
    candidate_key: Mapped[str] = mapped_column(String(200))
    camera_label: Mapped[str] = mapped_column(String(120))
    map_camera_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    track_id: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(24), default='weak_candidate')
    identity_status: Mapped[str] = mapped_column(String(32), default='candidate_unverified')
    retrieval_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin_top1_top2: Mapped[float | None] = mapped_column(Float, nullable=True)
    window_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    window_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackingCandidateReview(Base):
    """AI 视觉复核记录：只补充证据，不影响排名与身份状态。"""
    __tablename__ = 'tracking_candidate_reviews'
    __table_args__ = (Index('ix_tracking_candidate_reviews_run', 'run_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('tracking_runs.id'), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey('tracking_candidates.id'), index=True)
    camera_label: Mapped[str] = mapped_column(String(120))
    overall: Mapped[str] = mapped_column(String(32), default='insufficient_evidence')
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackingReviewEvent(Base):
    """append-only 人工审核：不覆盖旧决定，撤销也是新事件。"""
    __tablename__ = 'tracking_review_events'
    __table_args__ = (Index('ix_tracking_reviews_run_created', 'run_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('tracking_runs.id'), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey('tracking_candidates.id'))
    decision: Mapped[str] = mapped_column(String(16))  # approve/reject/revoke
    reviewer: Mapped[str] = mapped_column(String(120), default='human_reviewer')
    note: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)



class TargetElementGroup(Base):
    """B8-B0 目标要素组：一次录入的主目标 + 0..N 关联对象（单/复合目标）。"""
    __tablename__ = 'target_element_groups'
    __table_args__ = (Index('ix_target_groups_case_created', 'case_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), index=True)
    media_id: Mapped[str] = mapped_column(ForeignKey('media_assets.id'))
    primary_object_id: Mapped[str] = mapped_column(String(36))
    source_json: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default='saved')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TargetElementObject(Base):
    __tablename__ = 'target_element_objects'
    __table_args__ = (Index('ix_target_objects_group', 'group_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey('target_element_groups.id'), index=True)
    object_class: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(16))
    bbox_norm_xyxy: Mapped[list] = mapped_column(JSON)
    m4_observation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source: Mapped[str] = mapped_column(String(24), default='human_selected')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetObjectRelation(Base):
    __tablename__ = 'target_object_relations'
    __table_args__ = (Index('ix_target_relations_group', 'group_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey('target_element_groups.id'), index=True)
    subject_id: Mapped[str] = mapped_column(String(36))
    predicate: Mapped[str] = mapped_column(String(24))
    object_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default='image_observed')
    temporal_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    note: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiPromptSetting(Base):
    __tablename__ = 'ai_prompt_settings'
    key: Mapped[str] = mapped_column(String(20), primary_key=True)
    prompt: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TargetAttributeRevision(Base):
    __tablename__ = 'target_attribute_revisions'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey('target_element_groups.id', ondelete='CASCADE'), index=True)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attribute_key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    old_observation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    old_review_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    new_observation_status: Mapped[str] = mapped_column(String(20))
    new_review_state: Mapped[str] = mapped_column(String(20))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class TargetObjectAttribute(Base):
    __tablename__ = 'target_object_attributes'
    __table_args__ = (Index('ix_target_attrs_group_subject', 'group_id', 'subject_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_id: Mapped[str] = mapped_column(ForeignKey('target_element_groups.id'), index=True)
    subject_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(40))
    attribute_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    observation_status: Mapped[str] = mapped_column(String(16), default='unknown')
    review_state: Mapped[str] = mapped_column(String(20), default='unknown')
    source: Mapped[str] = mapped_column(String(40), default='human')
    provenance_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class TargetSpatialObservation(Base):
    """L2 地面定位观测：由已审核标定推算，独立于身份认定，绝不写 human_confirmed。"""
    __tablename__ = 'target_spatial_observations'
    __table_args__ = (Index('ix_target_spatial_observations_case', 'case_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey('target_observations.id'), index=True)
    camera_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spatial_level: Mapped[str] = mapped_column(String(4), default='L1')
    quality: Mapped[str] = mapped_column(String(40), default='region_only_unverified')
    identity_status: Mapped[str] = mapped_column(String(32), default='candidate_unverified')
    ground_point: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uncertainty_radius_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TargetElementAnchor(Base):
    """添加要素后的定位结论（B8-P1）。

    定位点来自 Map 镜头与标定/视域模型，不是用户拖动出来的；时间框拖动只写 label_offset。
    轨迹回填复用 tracking_runs，本表只存 run 指针与状态，不复制轨迹点。
    """
    __tablename__ = 'target_element_anchors'
    __table_args__ = (
        Index('ix_target_element_anchors_case', 'case_id', 'created_at'),
        Index('ix_target_element_anchors_group', 'group_id', 'object_id'),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey('cases.id'), index=True)
    group_id: Mapped[str] = mapped_column(
        ForeignKey('target_element_groups.id', ondelete='CASCADE'), index=True)
    object_id: Mapped[str] = mapped_column(String(36))
    media_id: Mapped[str | None] = mapped_column(ForeignKey('media_assets.id'), nullable=True)
    camera_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    camera_label: Mapped[str] = mapped_column(String(120), default='')
    camera_source: Mapped[str] = mapped_column(String(24), default='none')
    capture_local: Mapped[str | None] = mapped_column(String(19), nullable=True)
    capture_source: Mapped[str] = mapped_column(String(24), default='none')
    capture_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    pixel_xy: Mapped[list | None] = mapped_column(JSON, nullable=True)
    frame_size: Mapped[list | None] = mapped_column(JSON, nullable=True)
    projection_level: Mapped[str] = mapped_column(String(4), default='L0')
    projection_quality: Mapped[str] = mapped_column(String(40), default='region_only_unverified')
    ground_point: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    uncertainty_radius_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    projection_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    label_offset: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    trace_status: Mapped[str] = mapped_column(String(16), default='none')
    trace_window: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)
