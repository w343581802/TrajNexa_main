"""M4a HTTP: explicit manual ROI selection from a case-owned uploaded image.

No video extraction, detector model, identity matching or GIS projection occurs.
The existing image and OCR report remain immutable.
"""
from __future__ import annotations

from datetime import datetime
import time
from pathlib import Path
from threading import BoundedSemaphore
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.m4_features import analyze_image_roi, FeatureInputError
from app.models import MediaAsset, InspectionReport, TargetObservation
from app.recording_api import fail
from app import m4_detection, m4_vision

router = APIRouter(prefix='/api/v1')


class ObservationInput(BaseModel):
    object_class: str
    bbox_normalized: list[float] = Field(min_length=4, max_length=4)
    # Demo 口径（design/26 约束 0）：不再要求授权勾选与 8 字说明，
    # 留空时自动生成一句来源说明，保证证据链可读但不卡演示。
    review_note: str = ''
    authorized: bool = False  # 仅为兼容旧前端保留，校验已取消


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


def _case_image(db: Session, media_id: str, prefix: str = 'm4') -> tuple[MediaAsset, Path, np.ndarray]:
    """取本案截图并校验哈希；三个新接口共用同一套前置检查。

    prefix 只为保持既有错误码兼容（M4a 前端与用例认 m4a-*）。
    """
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        fail(404, f'{prefix}-media-not-found', '截图素材不存在。')
    if asset.kind != 'image' or not asset.case_id:
        fail(422, f'{prefix}-case-image-required', '只处理已归属案件的截图。')
    root = settings.storage_root.resolve()
    file = (root / asset.storage_relpath).resolve()
    if not file.is_relative_to(root):
        fail(409, f'{prefix}-invalid-source', '原始截图路径越界。')
    try:
        image = m4_detection.verified_image(file, asset.sha256)
    except m4_detection.DetectionError as exc:
        code = str(exc).replace('m4b_', f'{prefix}_')
        status = 409 if code in (f'{prefix}_source_hash_mismatch', f'{prefix}_source_unavailable',
                                 f'{prefix}_source_decode_failed', f'{prefix}_invalid_frame_size') else 422
        fail(status, code.replace('_', '-'), '无法读取该截图；原图未修改。')
    return asset, file, image


@router.post('/media/{media_id}/observations', response_model=ObservationOut, status_code=201)
def create_observation(media_id: str, body: ObservationInput, db: Session = Depends(get_db)):
    ensure_enabled()
    if body.object_class not in ('person', 'vehicle', 'other'):
        fail(422, 'm4a-invalid-object-class', '目标类别只支持 person、vehicle、other。')
    note = body.review_note.strip()[:500]
    if not note:
        note = '截图框选目标，来源与身份待人工核查'
    media, source, _image = _case_image(db, media_id, prefix='m4a')
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
    authorized: bool = False  # 兼容旧前端；校验已取消
    min_confidence: float = Field(default=0.4, ge=0.2, le=1)


# Bound CPU inference requests and prevent multiple simultaneous heavyweight
# D-FINE sessions. This is a preview only: no detection DB rows are created.
_detection_gate = BoundedSemaphore(value=1)


@router.post('/media/{media_id}/detections/preview')
def preview_detections(media_id: str, body: DetectionPreviewInput,
                       db: Session = Depends(get_db)):
    """D-FINE 预览（旧路径保留）；新前端统一走 /detect?engine=dfine。"""
    ensure_enabled()
    return _run_dfine(db, media_id, body.min_confidence, with_ids=True)


def _run_dfine(db: Session, media_id: str, min_confidence: float, *, with_ids: bool) -> dict:
    if not settings.m4b_enabled:
        fail(503, 'm4b-disabled', 'D-FINE 识别已关闭。')
    if not settings.m4b_model_sha256:
        fail(503, 'm4b-model-not-installed',
             'D-FINE 权重未安装：把官方导出的 ONNX 放到 /models/dfine.onnx，'
             '并配置 M4B_MODEL_SHA256（见 design/28）。')
    asset, _file, image = _case_image(db, media_id, prefix='m4b')
    if not _detection_gate.acquire(blocking=False):
        fail(429, 'm4b-detector-busy', '模型正在处理其它请求，请稍后再试。')
    try:
        result = m4_detection.detect_image(
            image, model_path=settings.m4b_model_path,
            model_sha256=settings.m4b_model_sha256,
            source_sha256=asset.sha256,
            threshold=min_confidence,
        )
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
    if with_ids:
        result['media_id'] = asset.id
        result['case_id'] = asset.case_id
    return result


class DetectInput(BaseModel):
    engine: str = 'qwen3vl'  # dfine | qwen3vl
    min_confidence: float = Field(default=0.4, ge=0.2, le=1)


@router.post('/media/{media_id}/detect')
def detect(media_id: str, body: DetectInput, db: Session = Depends(get_db)):
    """统一识别入口：同一张截图上换引擎重跑，用来对比两个模型的效果。

    只返回待核实候选框；不写库、不改原图、不确认身份。
    """
    ensure_enabled()
    engine = (body.engine or '').strip().lower()
    started = time.monotonic()
    if engine == 'dfine':
        result = _run_dfine(db, media_id, body.min_confidence, with_ids=True)
        result['engine'] = 'dfine'
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
        return result
    if engine != 'qwen3vl':
        fail(422, 'm4-unknown-engine', '识别引擎只支持 dfine 或 qwen3vl。')
    if not settings.vision_enabled:
        fail(503, 'vision-disabled', 'Qwen3-VL 识别已关闭。')
    asset, _file, image = _case_image(db, media_id)
    try:
        candidates = m4_vision.detect_objects(
            image, base_url=settings.vision_base_url, model=settings.vision_model,
            timeout=settings.vision_timeout_seconds)
    except m4_vision.VisionError as exc:
        code = str(exc)
        status = 503 if code in ('m4d_model_unavailable', 'm4d_prompt_too_long',
                                 'm4d_model_thinking_only') else \
            422 if code == 'm4d_roi_too_small' else 502
        hint = ('确认本机 ollama serve 已启动且已拉取 ' + settings.vision_model + '。'
                if code != 'm4d_model_thinking_only' else
                '当前模型只输出推理不给结论：请改用 instruct 变体（' + settings.vision_model + '）。')
        fail(status, code.replace('_', '-'), '视觉模型没有给出可用候选：' + hint)
    return {
        'schema_version': '1.0',
        'engine': 'qwen3vl',
        'status': 'candidates_unverified',
        'model_family': 'Qwen3-VL',
        'model_id': settings.vision_model,
        'source_sha256': asset.sha256,
        'image_size': [image.shape[1], image.shape[0]],
        'candidate_count': len(candidates),
        'candidates': candidates,
        'media_id': asset.id,
        'case_id': asset.case_id,
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'note': '视觉模型候选框仅为建议；不确认身份、设备或地理位置。',
    }


class RoiFeatureInput(BaseModel):
    bbox_normalized: list[float] = Field(min_length=4, max_length=4)
    engine: str = 'qwen3vl'


@router.post('/media/{media_id}/roi-features')
def roi_features(media_id: str, body: RoiFeatureInput, db: Session = Depends(get_db)):
    """选中框之后的关键特征：画质 / 主色（确定性）+ 外观短语（视觉模型，可降级）。"""
    ensure_enabled()
    asset, source, image = _case_image(db, media_id)
    try:
        result = analyze_image_roi(source, asset.sha256, body.bbox_normalized)
    except FeatureInputError as exc:
        fail(422 if str(exc) in ('m4_invalid_bbox', 'm4_roi_too_small',
                                  'm4_image_too_large') else 409,
             str(exc).replace('_', '-'), '无法可靠分析该区域，请核查截图与框选。')
    engine = (body.engine or '').strip().lower()
    result['engine'] = engine
    if engine == 'qwen3vl':
        if not settings.vision_enabled:
            result['appearance']['semantic_status'] = 'disabled'
        else:
            try:
                features = m4_vision.describe_roi(
                    image, body.bbox_normalized, base_url=settings.vision_base_url,
                    model=settings.vision_model,
                    timeout=min(120, settings.vision_timeout_seconds))
                result['appearance']['semantic_attributes'] = {
                    'features_unverified': features}
                result['appearance']['semantic_status'] = 'vlm_unverified'
            except m4_vision.VisionError as exc:
                # 关键特征里的模型部分是加分项：失败只降级，不让整次选框失败
                result['appearance']['semantic_status'] = 'unavailable'
                result['appearance']['semantic_error'] = str(exc)
    return result
