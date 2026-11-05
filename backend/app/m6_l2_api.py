"""B8-S3 HTTP: 已审核标定下的目标地面点（L2），失败返回降级结果。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app import m6_l2_observation as l2
from app.models import Case, TargetObservation, TargetSpatialObservation
from app.recording_api import fail
from app import map_db_spatial
from app.config import settings

router = APIRouter(prefix='/api/v1')


def _out(row) -> dict:
    return {'id': row.id, 'case_id': row.case_id, 'observation_id': row.observation_id,
            'camera_uuid': row.camera_uuid, 'calibration_id': row.calibration_id,
            'calibration_revision': row.calibration_revision,
            'spatial_level': row.spatial_level, 'quality': row.quality,
            'identity_status': row.identity_status, 'ground_point': row.ground_point,
            'uncertainty_radius_m': row.uncertainty_radius_m,
            'payload': row.payload, 'created_at': row.created_at.isoformat()}


@router.post('/cases/{case_id}/observations/{observation_id}/ground-projection')
def project_ground_point(case_id: str, observation_id: str, payload: dict,
                         db: Session = Depends(get_db)) -> dict:
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found')
    observation = db.get(TargetObservation, observation_id)
    if observation is None or observation.case_id != case_id:
        fail(404, 'observation_not_found')
    pixel = payload.get('ground_contact_pixel')
    if not (isinstance(pixel, list) and len(pixel) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pixel)):
        fail(422, 'ground_contact_pixel_required',
             '必须由用户或复核明确的接地点像素，不能用检测框几何中心代替')
    calibration = payload.get('calibration')
    if not isinstance(calibration, dict):
        calibration_id = str(payload.get('calibration_id') or '').strip()
        if not calibration_id:
            fail(422, 'calibration_or_calibration_id_required')
        try:
            calibration = map_db_spatial.read_ground_calibration(
                dsn=settings.map_db_readonly_url, calibration_id=calibration_id)
        except map_db_spatial.SpatialReadError as exc:
            status = 404 if str(exc) == 'calibration_not_published' else 409
            fail(status, str(exc), str(exc))
    frame_size = payload.get('frame_size')
    contact = payload.get('contact') if isinstance(payload.get('contact'), dict) else None
    evaluation = l2.evaluate(observation_id=observation_id,
                             camera_uuid=calibration.get('camera_uuid'),
                             calibration=calibration, pixel=pixel,
                             frame_size=frame_size, contact=contact)
    row = l2.persist(db, case_id=case_id, observation_id=observation_id,
                     evaluation=evaluation)
    return {'observation': _out(row)}


@router.get('/cases/{case_id}/spatial-observations')
def list_spatial_observations(case_id: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found')
    rows = db.scalars(select(TargetSpatialObservation)
                      .where(TargetSpatialObservation.case_id == case_id)
                      .order_by(TargetSpatialObservation.created_at.desc()).limit(200)).all()
    return {'items': [_out(row) for row in rows]}
