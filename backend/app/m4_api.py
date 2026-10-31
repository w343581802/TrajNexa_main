"""M4a HTTP: explicit manual ROI selection from a case-owned uploaded image.

No video extraction, detector model, identity matching or GIS projection occurs.
The existing image and OCR report remain immutable.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import BoundedSemaphore
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.m4_features import analyze_image_roi, FeatureInputError
from app.models import MediaAsset, InspectionReport, TargetObservation
from app.recording_api import fail
from app import m4_detection

router = APIRouter(prefix='/api/v1')


class ObservationInput(BaseModel):
    object_class: str
    bbox_normalized: list[float] = Field(min_length=4, max_length=4)
    review_note: str
    authorized: bool = False


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    case_id: str
    media_id: str
    object_class: str
    analysis_json: dict
    review_note: str
    review_state: str
    created_at: datetime
    source_inspection_id: str | None


def ensure_enabled():
    if not settings.m4a_enabled:
        fail(503, 'm4a-disabled', 'M4a 人工区域观测功能未启用，请先在受控部署中配置开关。')


@router.post('/media/{media_id}/observations', response_model=ObservationOut, status_code=201)
def create_observation(media_id: str, body: ObservationInput, db: Session = Depends(get_db)):
    ensure_enabled()
    if not body.authorized:
        fail(422, 'm4a-authorization-required', '请确认本次处理属于获授权的案件任务。')
    note = body.review_note.strip()
    if not 8 <= len(note) <= 500:
        fail(422, 'm4a-note-required', '请填写至少 8 个字符的区域选取依据。')
    if body.object_class not in ('person', 'vehicle', 'other'):
        fail(422, 'm4a-invalid-object-class', '目标类别只支持 person、vehicle、other。')
    media = db.get(MediaAsset, media_id)
    if media is None:
        fail(404, 'm4a-media-not-found', '来源素材不存在。')
    if media.kind != 'image' or not media.case_id:
        fail(422, 'm4a-case-image-required', '仅能分析已归属案件的截图。')
    root = settings.storage_root.resolve()
    source = (root / media.storage_relpath).resolve()
    if not source.is_relative_to(root):
        fail(409, 'm4a-invalid-source', '素材存储路径无效。')
    try:
        result = analyze_image_roi(source, media.sha256, body.bbox_normalized)
    except FeatureInputError as exc:
        fail(422 if str(exc) in ('m4_invalid_bbox', 'm4_roi_too_small',
                                  'm4_image_too_large') else 409,
             str(exc).replace('_', '-'), '无法可靠分析该区域，请核查截图与框选。')
    report = db.scalar(select(InspectionReport)
                       .where(InspectionReport.media_id == media.id)
                       .order_by(InspectionReport.created_at.desc(),
                                 InspectionReport.id.desc()).limit(1))
    # Never promote OCR timestamp or camera name to verified identity/position.
    if report:
        identity = report.result_json.get('identity') or {}
        result['source_context'] = {
            'inspection_id': report.id,
            'time_candidate': identity.get('capture_time'),
            'time_source': identity.get('time_source'),
            'time_verified': False,
            'camera_verified': False,
        }
    else:
        result['source_context'] = {
            'inspection_id': None, 'time_candidate': None,
            'time_source': None, 'time_verified': False,
            'camera_verified': False,
        }
    row = TargetObservation(
        id=str(uuid4()), case_id=media.case_id, media_id=media.id,
        object_class=body.object_class, analysis_json=result,
        review_note=note, review_state='pending',
        source_inspection_id=report.id if report else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get('/media/{media_id}/observations', response_model=list[ObservationOut])
def list_observations(media_id: str, limit: int = Query(50, ge=1, le=100),
 offset: int = Query(0, ge=0, le=100000), db: Session = Depends(get_db)):
    ensure_enabled()
    return list(db.scalars(select(TargetObservation).where(
        TargetObservation.media_id == media_id).order_by(
        TargetObservation.created_at.desc(), TargetObservation.id.desc()).offset(offset).limit(limit)))


@router.get('/cases/{case_id}/observations', response_model=list[ObservationOut])
def list_case_observations(case_id: str, limit: int = Query(50, ge=1, le=100),
 offset: int = Query(0, ge=0, le=100000), db: Session = Depends(get_db)):
    ensure_enabled()
    return list(db.scalars(select(TargetObservation).where(
        TargetObservation.case_id == case_id).order_by(
        TargetObservation.created_at.desc(), TargetObservation.id.desc()).offset(offset).limit(limit)))


@router.get('/observations/{observation_id}', response_model=ObservationOut)
def get_observation(observation_id: str, db: Session = Depends(get_db)):
    """Reopen a stored manual ROI without rerunning OCR or altering evidence."""
    ensure_enabled()
    row = db.get(TargetObservation, observation_id)
    if row is None:
        fail(404, 'm4a-observation-not-found', '该截图观测不存在或所属案件已删除。')
    return row


class DetectionPreviewInput(BaseModel):
    authorized: bool = False
    min_confidence: float = Field(default=0.4, ge=0.2, le=1)


# Bound CPU inference requests and prevent multiple simultaneous heavyweight
# D-FINE sessions. This is a preview only: no detection DB rows are created.
_detection_gate = BoundedSemaphore(value=1)


@router.post('/media/{media_id}/detections/preview')
def preview_detections(media_id: str, body: DetectionPreviewInput,
                       db: Session = Depends(get_db)):
    ensure_enabled()
    if not settings.m4b_enabled:
        fail(503, 'm4b-disabled', 'M4b 自动检测预览未启用。')
    if not body.authorized:
        fail(422, 'm4b-authorization-required', '需要确认已获授权处理该案件截图。')
    if not settings.m4b_model_sha256:
        fail(503, 'm4b-model-sha256-required', '必须配置本地 ONNX 模型 SHA-256。')
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        fail(404, 'm4b-media-not-found', '截图素材不存在。')
    if asset.kind != 'image' or not asset.case_id:
        fail(422, 'm4b-case-image-required', '只处理已归属案件的截图。')
    root = settings.storage_root.resolve()
    file = (root / asset.storage_relpath).resolve()
    if not file.is_relative_to(root):
        fail(409, 'm4b-invalid-source', '原始截图路径越界。')
    if not _detection_gate.acquire(blocking=False):
        fail(429, 'm4b-detector-busy', '模型正在处理其它预览，请稍后再试。')
    try:
        image = m4_detection.verified_image(file, asset.sha256)
        result = m4_detection.detect_image(
            image, model_path=settings.m4b_model_path,
            model_sha256=settings.m4b_model_sha256,
            source_sha256=asset.sha256,
            threshold=body.min_confidence,
        )
        result['media_id'] = asset.id
        result['case_id'] = asset.case_id
        return result
    except m4_detection.DetectionError as exc:
        code = str(exc)
        if code in ('m4b_source_hash_mismatch', 'm4b_source_unavailable',
                    'm4b_source_decode_failed', 'm4b_invalid_frame_size'):
            status = 409
        elif code in ('m4b_model_unavailable', 'm4b_runtime_unavailable',
                      'm4b_model_sha256_required', 'm4b_model_digest_mismatch',
                      'm4b_model_load_failed', 'm4b_incompatible_model'):
            status = 503
        else:
            status = 422 if code == 'm4b_source_image_too_large' else 500
        fail(status, code.replace('_', '-'),
             '无法生成可靠的自动检测候选；原图未修改。')
    finally:
        _detection_gate.release()
