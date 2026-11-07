"""B8-S HTTP：主系统侧的自动标定入口（转发 Map 的作业 / 候选 / 审核）。

闸门：
- ``B8_AUTO_CALIBRATION_ENABLED`` 关闭时整个路由 404，不返回半套数据；
- ``MAP_API_BASE_URL`` 未配置时按「功能未启用」处理，避免前端把 503 当成重试即可；
- 通过与否完全由 Map 决定：本层不缓存候选、不改候选状态，只做错误归一。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import b8_gates
from app import m6_auto_calibration_adapter as adapter
from app.recording_api import fail

router = APIRouter(prefix='/api/v1',
                   dependencies=[Depends(b8_gates.require_auto_calibration)])


class JobInput(BaseModel):
    camera_uuid: str
    actor: str = ''
    method: str = 'pose_parameters'


class ReviewInput(BaseModel):
    action: str
    actor: str
    note: str = ''
    holdout_residual_m: dict | None = None
    max_holdout_p90_m: float | None = None


def _forward(exc: adapter.AutoCalibrationUnavailable):
    code = exc.code
    if code in ('camera_not_found', 'candidate_not_found', 'job_not_found'):
        fail(404, code, exc.message or code)
    if code == 'map_api_not_configured':
        fail(404, 'auto_calibration_unavailable', '未配置 Map 接口地址（MAP_API_BASE_URL）')
    if code in ('map_api_unreachable', 'map_api_timeout'):
        fail(503, code, 'Map 接口不可达，自动标定暂时不可用')
    if code in ('holdout_residual_missing', 'holdout_p90_exceeds_limit'):
        fail(409, code, '候选缺少独立留出真值，不能通过审核')
    if code in ('camera_uuid_required', 'review_action_invalid', 'reviewer_required',
                'auto_calibration_method_unsupported', 'reference_size_invalid'):
        fail(422, code, code)
    fail(502, code or 'map_api_error', exc.message or 'Map 接口返回错误')


def _call(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except adapter.AutoCalibrationUnavailable as exc:
        _forward(exc)
        raise  # pragma: no cover - fail() 总是抛 HTTPException


@router.get('/spatial/auto-calibration/options/{camera_uuid}')
def calibration_options(camera_uuid: str) -> dict:
    return _call(adapter.options, camera_uuid)


@router.post('/spatial/auto-calibration/jobs')
def create_calibration_job(payload: JobInput) -> dict:
    return _call(adapter.request_job, payload.camera_uuid,
                 actor=payload.actor, method=payload.method)


@router.get('/spatial/auto-calibration/jobs')
def calibration_jobs(camera_uuid: str = '') -> dict:
    return _call(adapter.jobs, camera_uuid)


@router.get('/spatial/auto-calibration/jobs/{job_id}')
def calibration_job(job_id: str) -> dict:
    return _call(adapter.job, job_id)


@router.get('/spatial/auto-calibration/candidates/{camera_uuid}')
def calibration_candidates(camera_uuid: str, review_status: str = '') -> dict:
    return _call(adapter.candidates, camera_uuid, review_status=review_status)


@router.post('/spatial/auto-calibration/candidates/{candidate_id}/review')
def review_calibration_candidate(candidate_id: str, payload: ReviewInput) -> dict:
    return _call(adapter.review, candidate_id, action=payload.action,
                 actor=payload.actor, note=payload.note,
                 holdout=payload.holdout_residual_m,
                 max_p90_m=payload.max_holdout_p90_m)