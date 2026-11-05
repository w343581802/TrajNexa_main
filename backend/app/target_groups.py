"""B8-B0 target element groups: one primary target + 0..N associated objects.

The browser supplies IDs, classes, normalized boxes and optional M4 observation IDs.
All rows are case-scoped; media must belong to the same case. No identity assertion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Case, MediaAsset, TargetElementGroup, TargetElementObject,
                        TargetObjectAttribute, TargetObjectRelation)

OBJECT_CLASSES = ('person', 'vehicle', 'other')
ROLES = ('primary', 'associated')
PREDICATES = ('riding', 'passenger', 'pushing', 'nearby', 'carrying', 'unknown')
RELATION_STATUS = ('image_observed', 'model_proposed', 'human_verified', 'rejected')
ATTR_STATUS = ('known', 'unknown', 'not_applicable')
ATTR_REVIEW = ('human_verified', 'model_proposed', 'human_rejected', 'unknown')
MAX_OBJECTS = 8


def _object_row_id(group_id: str, client_id: str) -> str:
    """对象主键用组内哈希：客户端 id 只在组内唯一，不能当全局主键。"""
    try:
        # PATCH 回传的是已有行 id；复用，避免同一对象每次编辑换主键。
        return str(UUID(client_id))
    except (ValueError, TypeError):
        return str(uuid5(NAMESPACE_URL, f"target-object:{group_id}:{client_id}"))


class TargetGroupError(ValueError):
    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _case(db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise TargetGroupError('case_not_found')
    return case


def _bbox(value) -> list[float]:
    if not (isinstance(value, list) and len(value) == 4):
        raise TargetGroupError('invalid_bbox')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
        raise TargetGroupError('invalid_bbox')
    x1, y1, x2, y2 = (float(v) for v in value)
    if not all(0.0 <= v <= 1.0 for v in (x1, y1, x2, y2)) or x1 >= x2 or y1 >= y2:
        raise TargetGroupError('invalid_bbox')
    return [x1, y1, x2, y2]


def _validate(db: Session, case_id: str, payload: dict) -> tuple[MediaAsset, dict, list, list, list, dict]:
    _case(db, case_id)
    if not isinstance(payload, dict):
        raise TargetGroupError('invalid_request')
    media_id = str(payload.get('media_id') or '').strip()
    media = db.get(MediaAsset, media_id)
    if media is None or media.case_id != case_id:
        raise TargetGroupError('media_not_found')
    raw_objects = payload.get('objects')
    if not (isinstance(raw_objects, list) and 1 <= len(raw_objects) <= MAX_OBJECTS):
        raise TargetGroupError('objects_required')
    objects, seen = [], set()
    for item in raw_objects:
        if not isinstance(item, dict):
            raise TargetGroupError('invalid_object')
        oid = str(item.get('id') or '').strip()
        cls = str(item.get('object_class') or '').strip()
        role = str(item.get('role') or 'associated').strip()
        if not oid or len(oid) > 36 or oid in seen:
            raise TargetGroupError('invalid_object_id')
        if cls not in OBJECT_CLASSES:
            raise TargetGroupError('invalid_object_class')
        if role not in ROLES:
            raise TargetGroupError('invalid_object_role')
        seen.add(oid)
        objects.append({'id': oid, 'object_class': cls, 'role': role,
                        'bbox': _bbox(item.get('bbox_norm_xyxy')),
                        'm4_observation_id': (str(item.get('m4_observation_id')).strip()
                                              if item.get('m4_observation_id') else None)})
    primary = str(payload.get('primary_object_id') or '').strip()
    primaries = [o for o in objects if o['role'] == 'primary']
    if primary:
        if primary not in seen:
            raise TargetGroupError('primary_object_missing')
        for obj in objects:
            obj['role'] = 'primary' if obj['id'] == primary else 'associated'
        primaries = [o for o in objects if o['role'] == 'primary']
    elif len(primaries) != 1:
        raise TargetGroupError('primary_object_required')
    else:
        primary = primaries[0]['id']

    raw_rel = payload.get('relations') or []
    if not isinstance(raw_rel, list) or len(raw_rel) > 16:
        raise TargetGroupError('invalid_relation')
    relations = []
    for item in raw_rel:
        if not isinstance(item, dict):
            raise TargetGroupError('invalid_relation')
        subject = str(item.get('subject_id') or '').strip()
        obj = str(item.get('object_id') or '').strip()
        pred = str(item.get('predicate') or '').strip()
        if subject not in seen or obj not in seen or subject == obj:
            raise TargetGroupError('relation_object_missing')
        if pred not in PREDICATES:
            raise TargetGroupError('invalid_predicate')
        relations.append({'subject_id': subject, 'object_id': obj, 'predicate': pred,
                          'status': (str(item.get('status') or 'image_observed')
                                     if item.get('status') in RELATION_STATUS else 'image_observed'),
                          'temporal_verified': False,
                          'note': str(item.get('note') or '')[:500]})
    raw_attr = payload.get('attributes') or []
    if not isinstance(raw_attr, list) or len(raw_attr) > 64:
        raise TargetGroupError('invalid_attribute')
    attributes = []
    for item in raw_attr:
        if not isinstance(item, dict):
            raise TargetGroupError('invalid_attribute')
        subject = str(item.get('subject_id') or '').strip()
        name = str(item.get('name') or '').strip()
        status = str(item.get('observation_status') or 'unknown')
        review = str(item.get('review_state') or 'unknown')
        if subject not in seen or not name or len(name) > 40:
            raise TargetGroupError('invalid_attribute')
        if status not in ATTR_STATUS or review not in ATTR_REVIEW:
            raise TargetGroupError('invalid_attribute_status')
        value = item.get('value')
        if status == 'unknown':
            value = None
        elif value is not None:
            value = str(value)[:200]
        attributes.append({'subject_id': subject, 'name': name, 'attribute_key': str(item.get('attribute_key') or name)[:60], 'value': value,
                           'observation_status': status, 'review_state': review,
                           'source': str(item.get('source') or 'human')[:40],
                           'provenance': item.get('provenance') if isinstance(item.get('provenance'), dict) else {}})
    source = payload.get('source') if isinstance(payload.get('source'), dict) else {}
    return media, {'primary': primary}, objects, relations, attributes, source


def _write_children(db: Session, group_id: str, objects, relations, attributes) -> None:
    mapping = {obj['id']: _object_row_id(group_id, obj['id']) for obj in objects}
    for obj in objects:
        db.add(TargetElementObject(id=mapping[obj['id']], group_id=group_id,
                                   object_class=obj['object_class'], role=obj['role'],
                                   bbox_norm_xyxy=obj['bbox'],
                                   m4_observation_id=obj['m4_observation_id']))
    for rel in relations:
        db.add(TargetObjectRelation(id=str(uuid4()), group_id=group_id,
                                    subject_id=mapping[rel['subject_id']], predicate=rel['predicate'],
                                    object_id=mapping[rel['object_id']], status=rel['status'],
                                    temporal_verified=False, note=rel['note']))
    for attr in attributes:
        db.add(TargetObjectAttribute(id=str(uuid4()), group_id=group_id,
                                     subject_id=mapping[attr['subject_id']], name=attr['name'],
                                     value=attr['value'],
                                     observation_status=attr['observation_status'],
                                     review_state=attr['review_state'], attribute_key=attr['attribute_key'],
                                     source=attr['source'],
                                     provenance_json=attr['provenance']))


def create_group(db: Session, case_id: str, payload: dict) -> TargetElementGroup:
    media, primary, objects, relations, attributes, source = _validate(db, case_id, payload)
    group_id = str(uuid4())
    group = TargetElementGroup(id=group_id, case_id=case_id, media_id=media.id,
                               primary_object_id=_object_row_id(group_id, primary['primary']),
                               source_json=source, revision=1, status='saved')
    db.add(group)
    db.flush()
    _write_children(db, group.id, objects, relations, attributes)
    db.commit()
    db.refresh(group)
    return group


def _group(db: Session, case_id: str, group_id: str) -> TargetElementGroup:
    group = db.get(TargetElementGroup, group_id)
    if group is None or group.case_id != case_id:
        raise TargetGroupError('target_group_not_found')
    return group


def group_detail(db: Session, case_id: str, group_id: str) -> dict:
    group = _group(db, case_id, group_id)
    objects = db.scalars(select(TargetElementObject).where(
        TargetElementObject.group_id == group.id).order_by(TargetElementObject.created_at)).all()
    relations = db.scalars(select(TargetObjectRelation).where(
        TargetObjectRelation.group_id == group.id).order_by(TargetObjectRelation.created_at)).all()
    attributes = db.scalars(select(TargetObjectAttribute).where(
        TargetObjectAttribute.group_id == group.id).order_by(TargetObjectAttribute.created_at)).all()
    return {
        'schema': 'target-element-group/1', 'id': group.id, 'case_id': group.case_id,
        'media_id': group.media_id, 'primary_object_id': group.primary_object_id,
        'revision': group.revision, 'status': group.status, 'source': group.source_json or {},
        'objects': [{'id': o.id, 'object_class': o.object_class, 'role': o.role,
                     'bbox_norm_xyxy': o.bbox_norm_xyxy,
                     'm4_observation_id': o.m4_observation_id, 'source': o.source}
                    for o in objects],
        'relations': [{'id': r.id, 'subject_id': r.subject_id, 'predicate': r.predicate,
                       'object_id': r.object_id, 'status': r.status,
                       'temporal_verified': r.temporal_verified, 'note': r.note}
                      for r in relations],
        'attributes': [{'id': a.id, 'subject_id': a.subject_id, 'name': a.name,
                        'value': a.value, 'observation_status': a.observation_status,
                        'review_state': a.review_state, 'source': a.source, 'attribute_key': a.attribute_key,
                        'provenance': a.provenance_json or {}}
                       for a in attributes],
        'created_at': group.created_at, 'updated_at': group.updated_at,
    }


def list_groups(db: Session, case_id: str, limit: int = 50) -> list[dict]:
    _case(db, case_id)
    rows = db.scalars(select(TargetElementGroup)
                      .where(TargetElementGroup.case_id == case_id)
                      .order_by(TargetElementGroup.created_at.desc()).limit(limit)).all()
    out = []
    for group in rows:
        out.append({'id': group.id, 'media_id': group.media_id,
                    'primary_object_id': group.primary_object_id,
                    'revision': group.revision, 'created_at': group.created_at})
    return out


def patch_group(db: Session, case_id: str, group_id: str, payload: dict) -> TargetElementGroup:
    group = _group(db, case_id, group_id)
    expected = payload.get('revision')
    if expected is not None and int(expected) != group.revision:
        raise TargetGroupError('revision_conflict')
    merged = {'media_id': group.media_id,
              'primary_object_id': payload.get('primary_object_id') or group.primary_object_id}
    current = group_detail(db, case_id, group_id)
    merged['objects'] = payload.get('objects') or [
        {k: o[k] for k in ('id', 'object_class', 'role', 'bbox_norm_xyxy', 'm4_observation_id')}
        for o in current['objects']]
    merged['relations'] = payload.get('relations', current['relations'])
    merged['attributes'] = payload.get('attributes', current['attributes'])
    merged['source'] = payload.get('source') if isinstance(payload.get('source'), dict) else current.get('source', {})
    media, primary, objects, relations, attributes, source = _validate(db, case_id, merged)
    for table in (TargetElementObject, TargetObjectRelation, TargetObjectAttribute):
        for row in db.scalars(select(table).where(table.group_id == group.id)):
            db.delete(row)
    db.flush()
    _write_children(db, group.id, objects, relations, attributes)
    group.primary_object_id = _object_row_id(group.id, primary['primary'])
    group.source_json = source
    group.revision += 1
    group.updated_at = _utcnow()
    db.commit()
    db.refresh(group)
    return group