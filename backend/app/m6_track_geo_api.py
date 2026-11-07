"""B8-SA5 HTTP：真实案件的逐帧轨迹 → 实验空间轨迹段（读取时现算）。

**这是唯一一条把「真实录像里追出来的框」变成地图线的通道**，因此闸门写死：

- 输出恒为 `experimental_estimate` + `persisted=False`，不写 `target_spatial_observations`；
- 身份状态照抄 candidate（`candidate_unverified` / `verified` 由审核事件决定），
  本接口不提升、不否决；
- Map 参数不全就不给坐标，只回 `camera_only` 的像素轨迹与降级原因；
- 每次读取按当前 `spatial_revision` 现算，Map 更新后旧点自然消失，不会遗留假坐标。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import map_db_spatial
from app import m6_track_geo_projection as geo
from app.config import settings
from app.db import get_db
from app.models import Case, TrackSample, TrackingCandidate, TrackingRun
from app.recording_api import fail

router = APIRouter(prefix='/api/v1')

_CACHE_TTL_SECONDS = 60.0
_cache: dict = {'at': 0.0, 'cameras': {}, 'revision': None, 'error': None}


def _camera_index() -> tuple[dict, str | None, str | None]:
    """Map 快照 → 镜头索引（按显示名与 map_camera_id）；60 秒 TTL，失败只降级不 500。"""
    now = time.monotonic()
    if _cache['at'] and now - _cache['at'] < _CACHE_TTL_SECONDS:
        return dict(_cache['cameras']), _cache['revision'], _cache['error']
    cameras: dict = {}
    revision = None
    error = None
    try:
        payload = map_db_spatial.read_map_spatial(dsn=settings.map_db_readonly_url)
    except map_db_spatial.SpatialReadError as exc:
        error = str(exc)
    else:
        revision = payload.get('spatial_revision')
        for camera in payload.get('cameras') or []:
            for key in (camera.get('display_name'), str(camera.get('map_camera_id') or '')):
                if key:
                    cameras[str(key)] = camera
    _cache.update({'at': now, 'cameras': cameras, 'revision': revision, 'error': error})
    return dict(cameras), revision, error


def _reset_cache() -> None:
    """测试与手动刷新用：清空快照缓存。"""
    _cache.update({'at': 0.0, 'cameras': {}, 'revision': None, 'error': None})


@router.get('/cases/{case_id}/tracking-runs/{run_id}/track-segments')
def list_track_segments(case_id: str, run_id: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found')
    run = db.get(TrackingRun, run_id)
    if run is None or run.case_id != case_id:
        fail(404, 'tracking_run_not_found')

    candidates = db.scalars(select(TrackingCandidate)
                            .where(TrackingCandidate.run_id == run.id)
                            .order_by(TrackingCandidate.created_at)).all()
    if not candidates:
        return {'case_id': case_id, 'run_id': run.id, 'layer': 'experimental_estimate',
                'persisted': False, 'spatial_revision': None, 'spatial_error': None,
                'tracks': [],
                'note': '该任务还没有候选轨迹；先完成一次研判窗口'}

    rows = db.scalars(select(TrackSample)
                      .where(TrackSample.run_id == run.id)
                      .order_by(TrackSample.track_id, TrackSample.timestamp_seconds)).all()
    by_track: dict[str, list] = {}
    for row in rows:
        by_track.setdefault(row.track_id, []).append(row)

    cameras, revision, spatial_error = _camera_index()
    tracks = []
    for candidate in candidates:
        samples = by_track.get(candidate.track_id) or []
        camera = cameras.get(candidate.camera_label) or cameras.get(
            str(candidate.map_camera_id or ''))
        playback = {'clip_id': (candidate.evidence_refs or [None])[0],
                    'time_basis': 'clip_window',
                    'time_quality': (run.anchor_json or {}).get('time_quality') or 'uncertain'}
        result = geo.build_segments(
            camera, samples, playback=playback,
            evidence={'candidate_key': candidate.candidate_key,
                      'camera_label': candidate.camera_label,
                      'map_camera_id': candidate.map_camera_id})
        tracks.append({
            'candidate_key': candidate.candidate_key,
            'track_id': candidate.track_id,
            'camera_label': candidate.camera_label,
            'map_camera_id': candidate.map_camera_id,
            'identity_status': candidate.identity_status,
            'state': candidate.state,
            'retrieval_score': candidate.retrieval_score,
            'window_start': candidate.window_start,
            'window_end': candidate.window_end,
            'sample_count': len(samples),
            **result,
        })

    return {
        'case_id': case_id,
        'run_id': run.id,
        'layer': 'experimental_estimate',
        'persisted': False,
        'spatial_revision': revision,
        'spatial_error': spatial_error,
        'tracks': tracks,
        'summary': {
            'tracks': len(tracks),
            'samples': len(rows),
            'with_coordinates': sum(1 for t in tracks if t.get('camera_ready')),
        },
        'note': ('逐帧框 + Map 相机参数的实验估计轨迹；未经验证，不写正式 L2/L3，'
                 '也不作为身份证据。身份状态只看候选审核结果。'),
    }