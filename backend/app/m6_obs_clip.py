"""B8-D: 从导入的目标照片（M1c 预检报告）自动定位案件录像段。

用户只提供 observation_id：照片角标识别出的时间与摄像头决定“看哪段录像”，
不再要求用户手工挑 clip。找不到覆盖该时刻的段就明确失败，不猜。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InspectionReport, RecordingClip, RecordingSource, TargetObservation


class ObservationClipError(ValueError):
    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def observation_time_and_camera(db: Session, observation: TargetObservation):
    report = (db.get(InspectionReport, observation.source_inspection_id)
              if observation.source_inspection_id else None)
    if report is None:
        raise ObservationClipError('observation_time_unresolved',
                                   '目标照片没有角标时间/摄像头预检结果')
    data = report.result_json or {}
    samples = ((data.get('time') or {}).get('overlay_time_samples') or [])
    local = None
    for sample in samples:
        if isinstance(sample, dict) and sample.get('local_time'):
            local = sample['local_time']
            break
    local = local or ((data.get('identity') or {}).get('capture_time'))
    camera = data.get('camera') or {}
    identity = data.get('identity') or {}
    camera_uuid = camera.get('camera_uuid') or None
    camera_name = identity.get('camera_name') or camera.get('overlay_name_raw') or None
    if not local:
        raise ObservationClipError('observation_time_unresolved', '照片角标无法读出时间')
    if not camera_uuid and not camera_name:
        raise ObservationClipError('observation_camera_unresolved', '照片角标无法读出摄像头')
    try:
        observed = datetime.fromisoformat(str(local).replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        raise ObservationClipError('observation_time_unresolved', '照片时间格式不可用') from None
    return observed, camera_uuid, camera_name


def resolve_observation_clip(db: Session, case_id: str, observation: TargetObservation):
    """返回 (clip, 照片时刻在段内的偏移秒)。"""
    observed, camera_uuid, camera_name = observation_time_and_camera(db, observation)
    rows = db.execute(select(RecordingClip).join(
        RecordingSource, RecordingClip.source_id == RecordingSource.id)
        .where(RecordingSource.case_id == case_id,
               RecordingClip.start_local.is_not(None))).scalars().all()
    wanted = (camera_name or '').strip().casefold()
    best, best_gap = None, None
    for clip in rows:
        if camera_uuid and clip.camera_uuid and clip.camera_uuid != camera_uuid:
            continue
        if not camera_uuid and wanted and (clip.camera_name or '').strip().casefold() != wanted:
            continue
        if not (clip.match_status or '').startswith(('matched', 'suggested')):
            continue
        try:
            start = datetime.fromisoformat(str(clip.start_local).replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            continue
        duration = float(clip.duration_seconds or 0.0)
        offset = (observed - start).total_seconds()
        if offset < -1.0 or (duration > 0 and offset > duration):
            continue
        gap = abs(offset)
        if best is None or gap < best_gap:
            best, best_gap = (clip, max(0.0, offset)), gap
    if best is None:
        raise ObservationClipError('no_recording_for_observation',
                                   '案件录像里没有覆盖该照片时刻的片段')
    return best
