"""Opt-in M4d demo API: one case-owned frame -> unverified visual attributes."""
from __future__ import annotations

from threading import BoundedSemaphore

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app import m4_video, m4d_frame_review
from app.m4d_case_guard import M4dClipError
from app.m4d_local_client import LocalChatClient
from app.recording_api import fail

router = APIRouter(prefix="/api/v1")
_gate = BoundedSemaphore(1)
PROMPT = ('Describe only directly visible attributes in this frame. Return JSON with exactly '
          '{"attributes":[{"name":"clothing_upper","state":"visible","value":"..."}]}. '
          'Allowed names: clothing_upper, clothing_lower, carried_item, visible_motion. '
          'Allowed states: visible, unknown, not_visible, uncertain. '
          'For non-visible states use null value. Do not infer identity, face recognition, '
          'age, ethnicity, or whether this is the same person as another frame.')


class FrameReviewInput(BaseModel):
    offset_seconds: float = Field(ge=0, le=21600, allow_inf_nan=False)


def make_client():
    return LocalChatClient(settings.m4d_base_url, settings.m4d_timeout_seconds)


@router.post("/cases/{case_id}/recordings/clips/{clip_id}/vlm/preview")
def preview_frame_attributes(case_id: str, clip_id: str, body: FrameReviewInput,
                             db: Session = Depends(get_db)):
    if not settings.m4d_enabled:
        fail(503, "m4d-disabled", "M4d 本地模型分析未启用。")
    if not settings.m4d_model.strip():
        fail(503, "m4d-model-required", "请先配置本地视觉模型名称。")
    if not _gate.acquire(blocking=False):
        fail(429, "m4d-busy", "已有模型分析任务正在运行。")
    try:
        try:
            client = make_client()
        except ValueError:
            fail(503, "m4d-local-model-config-invalid", "本地模型地址或超时配置无效。")
        review = m4d_frame_review.review_case_clip_frame(
            db, case_id=case_id, clip_id=clip_id, offset_seconds=body.offset_seconds,
            prompt=PROMPT, model=settings.m4d_model, client=client)
        if (review.source.case_id != case_id or review.source.media_id != clip_id or
                abs(review.source.frame_offset_ms - round(body.offset_seconds * 1000)) > 1):
            fail(409, "m4d-source-mismatch", "模型分析来源与当前录像不一致。")
        return {
            "case_id": case_id, "clip_id": clip_id,
            "requested_offset_seconds": review.source.frame_offset_ms / 1000,
            "image_sha256": review.source.image_sha256,
            "frame_time_verified": False, "identity_established": False,
            "vlm_status": review.vlm.status,
            "vlm_error_code": review.vlm.error_code,
            "attributes_status": review.attributes.status,
            "attributes": [
                {"name": item.name, "state": item.state, "value": item.value,
                 "source": item.source}
                for item in review.attributes.candidates
            ],
            "requires_human_review": True,
        }
    except M4dClipError as exc:
        fail(404 if str(exc) == "clip_unavailable" else 422,
             str(exc).replace("_", "-"), "录像不可用或不属于当前案件。")
    except m4_video.KeyframeError as exc:
        code = str(exc)
        fail(404 if code == "m4c_clip_not_found" else 422 if "offset" in code else 409,
             code.replace("_", "-"), "录像关键帧不可用，请核查录像和偏移。")
    except m4d_frame_review.FrameReviewError:
        fail(409, "m4d-frame-unavailable", "未能提取有效画面。")
    finally:
        _gate.release()
