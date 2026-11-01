"""M4a HTTP: explicit manual ROI selection from a case-owned uploaded image.

No video extraction, detector model, identity matching or GIS projection occurs.
The existing image and OCR report remain immutable.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
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
from app import detectors, m4_vision, media_image
from app.m5_vision_registry import (public_vision_models, resolve_vision_model,
                                    VisionRegistryError)
from app.m5_vision_transport import chat_for

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
        image = media_image.verified_image(file, asset.sha256)
    except media_image.MediaImageError as exc:
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
_rebucca_gate = BoundedSemaphore(value=1)  # image-only experiments; shared model service


@router.post('/media/{media_id}/detections/preview')
def preview_detections(media_id: str, body: DetectionPreviewInput,
                       db: Session = Depends(get_db)):
    """D-FINE 预览（旧路径保留）；新前端统一走 /detect?engine=dfine。"""
    ensure_enabled()
    return _run_dfine(db, media_id, body.min_confidence, with_ids=True)


def _run_dfine(db: Session, media_id: str, min_confidence: float, *, with_ids: bool) -> dict:
    """兼容旧路径的预览入口：改用本地默认检测器，不再走独立 D-FINE 实现。"""
    models = detectors.list_models()
    if not models:
        fail(503, 'm4b-model-not-installed',
             '未安装检测器：请在 backend/config/detectors.json 注册模型并准备权重。')
    configured = settings.m5_workspace_detector_id.strip()
    chosen = configured if configured in {row['id'] for row in models} else models[0]['id']
    asset, _file, image = _case_image(db, media_id, prefix='m4b')
    if not _detection_gate.acquire(blocking=False):
        fail(429, 'm4b-detector-busy', '模型正在处理其它请求，请稍后再试。')
    try:
        result = detectors.detect_flat(image, [chosen], min_confidence)
    finally:
        _detection_gate.release()
    out = {
        'schema_version': '1.0',
        'engine': 'local',
        'status': 'candidates_unverified',
        'model_family': next(row['name'] for row in models if row['id'] == chosen),
        'model_sha256': next(row['model_sha256'] for row in models if row['id'] == chosen),
        'source_sha256': asset.sha256,
        'image_size': [image.shape[1], image.shape[0]],
        'candidate_count': len(result['candidates']),
        'candidates': result['candidates'],
        'model_runs': result['model_runs'],
        'note': '候选框未确认身份，需人工复核。',
    }
    if with_ids:
        out['media_id'] = asset.id
        out['case_id'] = asset.case_id
    return out

class DetectInput(BaseModel):
    engine: str = 'qwen3vl'  # auto(workspace) | rebucca | dfine | vlm | legacy Qwen
    min_confidence: float = Field(default=0.4, ge=0.2, le=1)
    model_ids: list[str] = Field(default_factory=list, max_length=5)
    vision_model_id: str | None = None  # allowlisted model catalog ID for engine=vlm


VISION_MODELS = {
    'qwen3vl': ('Qwen3-VL 2B', 'vision_model'),
    'qwen3vl4b': ('Qwen3-VL 4B', 'vision_model_large'),
}


@router.get('/ai/vision-models')
def available_vision_models():
    """List configured Ollama vision models, never an arbitrary URL from UI."""
    try:
        models = public_vision_models(settings)
    except VisionRegistryError:
        fail(503, 'm5-vision-registry-invalid',
             '视觉模型配置有误，请核对本机模型配置。')
    return {'enabled': any(row['enabled'] for row in models), 'models': models}


@router.get('/ai/image-models')
def available_image_models():
    """本机检测器目录（backend/config/detectors.json）；不返回路径或凭据。"""
    if not detectors.enabled():
        return {'enabled': False, 'models': []}
    try:
        models = detectors.list_models()
    except detectors.DetectorError as exc:
        fail(503, str(exc.code).replace('_', '-'),
             '检测器配置有误；其他识别引擎和人工框选仍可使用。')
    return {'enabled': bool(models), 'models': models}


def _run_local(db: Session, media_id: str, body: DetectInput, started: float) -> dict:
    """本机检测器推理（引擎层自 Rebucca 移植，进程内直接调用，无 HTTP sidecar）。"""
    if not detectors.enabled():
        fail(503, 'local-detectors-disabled',
             '未启用本机检测器：请在 backend/config/detectors.json 注册模型。')
    if not body.model_ids:
        fail(422, 'local-model-selection-required', '请至少选择一个检测模型。')
    asset, _source, image = _case_image(db, media_id, prefix='m5')
    if not _rebucca_gate.acquire(blocking=False):
        fail(429, 'local-detector-busy', '检测模型正在处理其他图片，请稍后再试。')
    try:
        result = detectors.detect_flat(image, body.model_ids, body.min_confidence)
    except detectors.DetectorError as exc:
        code = exc.code
        status = 422 if code in ('invalid_model_selection', 'invalid_threshold') else 503
        fail(status, code.replace('_', '-').replace(':', '-'),
             '本机检测未成功；不会将错误当作无目标，也不会修改原图。')
    finally:
        _rebucca_gate.release()
    return {
        'schema_version': '1.0',
        'engine': 'local',
        'status': 'candidates_unverified',
        'model_family': '本机多模型检测',
        'source_sha256': asset.sha256,
        'image_size': [image.shape[1], image.shape[0]],
        'candidate_count': len(result['candidates']),
        'candidates': result['candidates'],
        'model_runs': result['model_runs'],
        'media_id': asset.id,
        'case_id': asset.case_id,
        'elapsed_seconds': round(time.monotonic() - started, 2),
        'note': '多个检测模型的候选不会自动合并或确认身份，选中后由人工复核。',
    }

@router.post('/media/{media_id}/detect')
def detect(media_id: str, body: DetectInput, db: Session = Depends(get_db)):
    """统一识别入口：同一张截图上换引擎重跑，用来对比各个模型的效果。

    只返回待核实候选框；不写库、不改原图、不确认身份。
    """
    ensure_enabled()
    engine = (body.engine or '').strip().lower()
    started = time.monotonic()
    if engine == 'auto':
        # 业务模式：服务端固定一个检测器，浏览器里的实验选择不会影响研判流程。
        models = detectors.list_models() if detectors.enabled() else []
        if models:
            configured = settings.m5_workspace_detector_id.strip()
            selected = configured or models[0]['id']
            if selected not in {row['id'] for row in models}:
                fail(503, 'local-workspace-model-missing',
                     '工作台默认检测模型未配置，请手动框选。')
            result = _run_local(db, media_id, DetectInput(
                engine='local', model_ids=[selected],
                min_confidence=body.min_confidence), started)
            result['effective_profile'] = 'workspace_server_default'
            return result
        if settings.vision_enabled:
            engine = 'qwen3vl'
        else:
            fail(503, 'local-workspace-model-missing',
                 '没有可用的业务默认检测模型，可继续人工框选目标。')
    if engine in ('local', 'rebucca'):
        return _run_local(db, media_id, body, started)
    if engine == 'dfine':
        result = _run_dfine(db, media_id, body.min_confidence, with_ids=True)
        result['elapsed_seconds'] = round(time.monotonic() - started, 2)
        return result
    if engine == 'vlm':
        try:
            chosen = resolve_vision_model(settings, body.vision_model_id or '')
        except VisionRegistryError:
            fail(422, 'm5-unknown-vision-model', '请从已配置的大模型列表中选择模型。')
        label, model = chosen['name'], chosen['model_id']
        chat_fn = chat_for(chosen)
        vision_active = chosen['enabled']
    elif engine in VISION_MODELS:
        label, setting_name = VISION_MODELS[engine]
        model = getattr(settings, setting_name)
        chat_fn = None
        vision_active = bool(settings.vision_enabled)
    else:
        fail(422, 'm4-unknown-engine',
             '识别引擎只支持 local、qwen3vl、qwen3vl4b、vlm。')
    if not vision_active:
        fail(503, 'vision-disabled', '所选视觉模型服务未启用。')
    asset, _file, image = _case_image(db, media_id)
    try:
        candidates = m4_vision.detect_objects(
            image, base_url=settings.vision_base_url, model=model,
            timeout=settings.vision_timeout_seconds, chat_fn=chat_fn)
    except m4_vision.VisionError as exc:
        code = str(exc)
        status = 503 if code in ('m4d_model_unavailable', 'm4d_prompt_too_long',
                                 'm4d_model_thinking_only') else \
            422 if code == 'm4d_roi_too_small' else 502
        hint = ('确认本机 ollama serve 已启动且已拉取 ' + model + '。'
                if code != 'm4d_model_thinking_only' else
                '当前模型只输出推理不给结论：请改用 instruct 变体（' + model + '）。')
        fail(status, code.replace('_', '-'), '视觉模型没有给出可用候选：' + hint)
    return {
        'schema_version': '1.0',
        'engine': engine,
        'status': 'candidates_unverified',
        'model_family': label,
        'model_id': model,
        'source_sha256': asset.sha256,
        'image_size': [image.shape[1], image.shape[0]],
        'tiling': 1,  # 视觉模型整幅推理：切片会成倍拖慢（2s → 8s），先不切
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
    object_class: str = 'person'  # image-selected category, not identity
    prompt_override: str | None = Field(default=None, max_length=2400)
    vision_model_id: str | None = None  # configured model selection for ROI review


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
    if body.object_class not in ('person', 'vehicle', 'other'):
        fail(422, 'm5-invalid-object-class', '目标类别只能选择人员、车辆或其他。')
    result['engine'] = engine
    if not body.vision_model_id and not settings.vision_enabled:
        result['appearance']['semantic_status'] = 'disabled'
        return result
    # An explicit M5 selection wins; otherwise keep existing M4 behavior.
    # Do not silently fall back when the user explicitly selected a model.
    if body.vision_model_id:
        try:
            chosen = resolve_vision_model(settings, body.vision_model_id)
        except VisionRegistryError:
            fail(422, 'm5-unknown-vision-model', '请从已配置的大模型列表中选择模型。')
        if not chosen['enabled']:
            result['appearance']['semantic_status'] = 'disabled'
            return result
        models = [chosen]
    else:
        names = [getattr(settings, VISION_MODELS[engine][1])] if engine in VISION_MODELS \
            else [settings.vision_feature_model, settings.vision_model]
        models = [{'model_id': name, 'provider': 'ollama'} for name in dict.fromkeys(names)]
    last_error = 'm4d_model_unavailable'
    for entry in models:
        model = entry['model_id']
        try:
            features = m4_vision.describe_roi(
                image, body.bbox_normalized, base_url=settings.vision_base_url,
                model=model, timeout=min(120, settings.vision_timeout_seconds),
                object_class=body.object_class,
                prompt_override=body.prompt_override,
                chat_fn=chat_for(entry))
        except m4_vision.VisionError as exc:
            last_error = str(exc)
            if last_error == 'm4d_model_unavailable':
                continue  # 4B 没拉就用 2B 兜底
            break
        prompt_used = m4_vision.effective_feature_prompt(
            body.object_class, body.prompt_override)
        result['appearance']['semantic_attributes'] = {
            'features_unverified': features, 'semantic_model': model,
            'object_class': body.object_class,
            'prompt_sha256': sha256(prompt_used.encode('utf-8')).hexdigest(),
            'prompt_scope': 'single_image_experiment' if body.prompt_override else 'builtin_default',
        }
        result['appearance']['semantic_status'] = 'vlm_unverified'
        return result
    # 关键特征里的模型部分是加分项：失败只降级，不让整次选框失败
    result['appearance']['semantic_status'] = 'unavailable'
    result['appearance']['semantic_error'] = last_error
    return result
