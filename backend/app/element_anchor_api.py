"""B8-P1 HTTP：添加要素后的定位点、时间框标签与轨迹回填。

边界：
- 局部截图不产生锚点（只写目标特征），接口返回 skipped 而不是伪造点位。
- 定位点不可拖动；PATCH 只接受 label_offset。
- 轨迹回填复用 tracking_runs，本接口只代理状态与片段。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app import element_anchor as service
from app import target_groups as groups
from app.element_anchor import AnchorError
from app.models import Case, TargetElementAnchor
from app.recording_api import fail

router = APIRouter(prefix='/api/v1')

_NOT_FOUND = ('case_not_found', 'target_group_not_found', 'element_anchor_not_found',
              'target_object_not_found', 'media_not_found')
_CONFLICT = ('camera_name_ambiguous', 'position_is_not_draggable')


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except AnchorError as exc:
        status = 404 if exc.code in _NOT_FOUND else 409 if exc.code in _CONFLICT else 422
        fail(status, exc.code, exc.message)
    except groups.TargetGroupError as exc:
        status = 404 if exc.code in _NOT_FOUND else 422
        fail(status, exc.code, exc.message)


@router.post('/cases/{case_id}/elements/{group_id}/anchor')
def create_element_anchor(case_id: str, group_id: str, payload: dict,
                          db: Session = Depends(get_db)) -> dict:
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found', 'case_not_found')
    return _guard(service.create_anchor, db, case_id, group_id, payload)


@router.get('/cases/{case_id}/elements/anchors')
def list_element_anchors(case_id: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found', 'case_not_found')
    return _guard(service.list_anchors, db, case_id)


@router.patch('/cases/{case_id}/elements/anchors/{anchor_id}')
def patch_element_anchor(case_id: str, anchor_id: str, payload: dict,
                         db: Session = Depends(get_db)) -> dict:
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found', 'case_not_found')
    return _guard(service.patch_anchor, db, case_id, anchor_id, payload)


@router.delete('/cases/{case_id}/elements/anchors/{anchor_id}')
def delete_element_anchor(case_id: str, anchor_id: str, db: Session = Depends(get_db)) -> dict:
    """撤掉一个定位点（例如截图改成局部截图）：只删锚点，目标组与特征保留。"""
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found', 'case_not_found')
    return _guard(service.delete_anchor, db, case_id, anchor_id)


@router.get('/cases/{case_id}/elements/{group_id}/trace')
def element_trace(case_id: str, group_id: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found', 'case_not_found')
    row = db.scalars(select(TargetElementAnchor).where(
        TargetElementAnchor.case_id == case_id,
        TargetElementAnchor.group_id == group_id).order_by(
        TargetElementAnchor.created_at)).first()
    return _guard(service.trace_view, db, case_id, group_id, row)


@router.get('/cases/{case_id}/elements')
def list_elements(case_id: str, db: Session = Depends(get_db)) -> dict:
    """刷新后重建要素：目标组 + 属性 + 锚点 + 回填状态。"""
    if db.get(Case, case_id) is None:
        fail(404, 'case_not_found', 'case_not_found')
    anchors = {row.group_id: row for row in db.scalars(
        select(TargetElementAnchor).where(TargetElementAnchor.case_id == case_id)
        .order_by(TargetElementAnchor.created_at)).all()}
    items = []
    for summary in _guard(groups.list_groups, db, case_id, 200):
        detail = _guard(groups.group_detail, db, case_id, summary['id'])
        anchor = anchors.get(summary['id'])
        items.append({'group': detail,
                      'anchor': service._out(anchor) if anchor is not None else None})
    return {'case_id': case_id, 'items': items}