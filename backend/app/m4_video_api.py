"""Read-only sampled keyframes for a scanned, case-owned recording clip.

Never persists video frames or invents a tracklet, identity or geographic path.
"""
from __future__ import annotations

from threading import BoundedSemaphore

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app import m4_video, m4_detection
from app.recording_api import fail

router = APIRouter(prefix='/api/v1')
_gate = BoundedSemaphore(1)


class ClipSampleRequest(BaseModel):
    offsets_seconds: list[float] = Field(min_length=1, max_length=6)
    include_detection: bool = False


def enabled():
    if not settings.m4c_enabled or not settings.m4a_enabled:
        fail(503, 'm4c-disabled', 'M4c 视频观测预览未启用。')


def sample_failure(exc: m4_video.KeyframeError):
    code = str(exc)
    if code == 'm4c_clip_not_found':
        status = 404
    elif code in ('m4c_clip_unavailable', 'm4c_clip_changed_rescan_required'):
        status = 409
    elif code in ('m4c_invalid_offset', 'm4c_invalid_offsets',
                  'm4c_duplicate_offsets', 'm4c_offset_outside_clip'):
        status = 422
    elif code == 'm4c_detector_not_configured':
        status = 503
    else:
        status = 409
    fail(status, code.replace('_', '-'),
         '无法读取该案件录像的可靠画面，请核查录像资源及时间偏移。')


@router.post('/cases/{case_id}/recordings/clips/{clip_id}/keyframes/preview')
def preview_clip_keyframes(case_id: str, clip_id: str, body: ClipSampleRequest,
                           db: Session = Depends(get_db)):
    enabled()
    try:
        clip, path = m4_video.resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
        # Validate all offsets before any video decoding or model load.
        offsets = m4_video.checked_offsets(body.offsets_seconds, clip)
    except m4_video.KeyframeError as exc:
        sample_failure(exc)
    if body.include_detection and not (
        settings.m4b_enabled and settings.m4b_model_sha256
    ):
        fail(503, 'm4c-detector-not-configured', '需要配置经过 SHA-256 校验的 D-FINE ONNX 模型。')
    if not _gate.acquire(blocking=False):
        fail(429, 'm4c-preview-busy', '已有录像采样任务，请稍后重试。')
    try:
        result = m4_video.preview_keyframes(clip=clip, path=path, offsets=offsets,
                                             include_detection=body.include_detection)
        result['case_id'] = case_id
        return result
    except m4_video.KeyframeError as exc:
        sample_failure(exc)
    except m4_detection.DetectionError as exc:
        fail(503, str(exc).replace('_', '-'),
             'D-FINE 检测不可用；不会生成模拟框。')
    finally:
        _gate.release()


@router.get('/cases/{case_id}/recordings/clips/{clip_id}/keyframes/image')
def get_clip_keyframe_image(case_id: str, clip_id: str,
                            offset_seconds: float = Query(0.5, ge=0, le=21600),
                            db: Session = Depends(get_db)):
    enabled()
    if not _gate.acquire(blocking=False):
        fail(429, 'm4c-preview-busy', '已有录像采样任务，请稍后重试。')
    try:
        clip, path = m4_video.resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
        offset = m4_video.checked_offset(offset_seconds, clip)
        frame = m4_video.fetch_frame(path, clip, offset)
        if frame is None:
            fail(409, 'm4c-frame-unavailable', '指定时间偏移无法解出有效画面。')
        content = m4_video.jpeg_preview(frame)
        return Response(content, media_type='image/jpeg', headers={
            'Cache-Control': 'private, no-store',
            'X-Frame-Time-Semantics': 'requested-seek-offset-not-verified-pts',
        })
    except m4_video.KeyframeError as exc:
        sample_failure(exc)
    finally:
        _gate.release()
