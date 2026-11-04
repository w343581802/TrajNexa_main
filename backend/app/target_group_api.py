"""B8-B0 target element group HTTP (case-scoped, atomic save)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app import target_groups as service
from app.target_groups import TargetGroupError
from app.recording_api import fail

router = APIRouter(prefix='/api/v1')


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TargetGroupError as exc:
        status = 404 if exc.code in ('case_not_found', 'target_group_not_found',
                                    'media_not_found') else 409 if exc.code == 'revision_conflict' else 422
        fail(status, exc.code, exc.message)


@router.post('/cases/{case_id}/target-groups', status_code=201)
def create_target_group(case_id: str, payload: dict, db: Session = Depends(get_db)):
    group = _guard(service.create_group, db, case_id, payload)
    return service.group_detail(db, case_id, group.id)


@router.get('/cases/{case_id}/target-groups')
def list_target_groups(case_id: str, limit: int = Query(50, ge=1, le=200),
                       db: Session = Depends(get_db)):
    return {'items': _guard(service.list_groups, db, case_id, limit)}


@router.get('/cases/{case_id}/target-groups/{group_id}')
def get_target_group(case_id: str, group_id: str, db: Session = Depends(get_db)):
    return _guard(service.group_detail, db, case_id, group_id)


@router.patch('/cases/{case_id}/target-groups/{group_id}')
def update_target_group(case_id: str, group_id: str, payload: dict,
                        db: Session = Depends(get_db)):
    _guard(service.patch_group, db, case_id, group_id, payload)
    return service.group_detail(db, case_id, group_id)
