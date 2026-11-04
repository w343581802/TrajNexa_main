"""B8-B0: composite target element groups (atomic save, case isolation, revision)."""
from uuid import uuid4

from app.models import Case, MediaAsset


def _case_and_media(Session):
    case_id, media_id = str(uuid4()), str(uuid4())
    with Session() as db:
        db.add(Case(id=case_id, code='C-' + case_id[:8], name='case'))
        db.add(MediaAsset(id=media_id, case_id=case_id, kind='image',
                          original_name='rider.jpg', mime_detected='image/jpeg',
                          sha256='0' * 64, storage_relpath='media/rider.jpg', size_bytes=1))
        db.commit()
    return case_id, media_id


def _payload(media_id, objects, primary=None, relations=None, attributes=None):
    return {'media_id': media_id, 'primary_object_id': primary,
            'objects': objects, 'relations': relations or [],
            'attributes': attributes or [],
            'source': {'capture_at': '2026-10-23T15:00:31', 'time_status': 'unverified',
                       'camera_name': '1-LT-桂康路松山巷-向北', 'camera_status': 'unverified'}}


def _person(oid='P1', role='primary'):
    return {'id': oid, 'object_class': 'person', 'role': role,
            'bbox_norm_xyxy': [0.3, 0.2, 0.5, 0.8]}


def _vehicle(oid='V1'):
    return {'id': oid, 'object_class': 'vehicle', 'role': 'associated',
            'bbox_norm_xyxy': [0.25, 0.35, 0.6, 0.85]}


def test_single_person_group_round_trips(env):
    client, Session, _ = env
    case_id, media_id = _case_and_media(Session)
    resp = client.post('/api/v1/cases/%s/target-groups' % case_id,
                       json=_payload(media_id, [_person()], primary='P1'))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body['primary_object_id'] == 'P1'
    assert body['objects'][0]['role'] == 'primary'
    assert body['schema'] == 'target-element-group/1'

    again = client.get('/api/v1/cases/%s/target-groups/%s' % (case_id, body['id']))
    assert again.status_code == 200
    assert again.json()['objects'][0]['bbox_norm_xyxy'] == [0.3, 0.2, 0.5, 0.8]


def test_rider_person_primary_vehicle_associated_with_relation(env):
    client, Session, _ = env
    case_id, media_id = _case_and_media(Session)
    payload = _payload(media_id, [_person(), _vehicle()], primary='P1',
                       relations=[{'subject_id': 'P1', 'predicate': 'riding',
                                   'object_id': 'V1', 'status': 'image_observed'}],
                       attributes=[{'subject_id': 'P1', 'name': 'top_color',
                                    'value': None, 'observation_status': 'unknown',
                                    'review_state': 'unknown', 'source': 'human'}])
    body = client.post('/api/v1/cases/%s/target-groups' % case_id, json=payload).json()
    assert [o['role'] for o in body['objects']] == ['primary', 'associated']
    assert body['relations'][0]['predicate'] == 'riding'
    assert body['relations'][0]['temporal_verified'] is False
    assert body['attributes'][0]['observation_status'] == 'unknown'


def test_two_primaries_are_rejected(env):
    client, Session, _ = env
    case_id, media_id = _case_and_media(Session)
    resp = client.post('/api/v1/cases/%s/target-groups' % case_id,
                       json=_payload(media_id, [_person('P1'), _person('P2')]))
    assert resp.status_code == 422
    assert resp.json()['error']['code'] == 'primary_object_required'


def test_invalid_bbox_and_cross_case_media_are_rejected(env):
    client, Session, _ = env
    case_id, media_id = _case_and_media(Session)
    bad = _person()
    bad['bbox_norm_xyxy'] = [0.5, 0.2, 0.4, 0.8]
    resp = client.post('/api/v1/cases/%s/target-groups' % case_id,
                       json=_payload(media_id, [bad], primary='P1'))
    assert resp.status_code == 422 and resp.json()['error']['code'] == 'invalid_bbox'

    other_case, _ = _case_and_media(Session)
    resp = client.post('/api/v1/cases/%s/target-groups' % other_case,
                       json=_payload(media_id, [_person()], primary='P1'))
    assert resp.status_code == 404 and resp.json()['error']['code'] == 'media_not_found'


def test_patch_swaps_primary_and_bumps_revision(env):
    client, Session, _ = env
    case_id, media_id = _case_and_media(Session)
    body = client.post('/api/v1/cases/%s/target-groups' % case_id,
                       json=_payload(media_id, [_person(), _vehicle()], primary='P1')).json()
    swapped = {
        'revision': body['revision'], 'primary_object_id': 'V1',
        'objects': [{'id': 'P1', 'object_class': 'person', 'role': 'associated',
                     'bbox_norm_xyxy': [0.3, 0.2, 0.5, 0.8]},
                    {'id': 'V1', 'object_class': 'vehicle', 'role': 'primary',
                     'bbox_norm_xyxy': [0.25, 0.35, 0.6, 0.85]}],
    }
    resp = client.patch('/api/v1/cases/%s/target-groups/%s' % (case_id, body['id']),
                        json=swapped)
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated['primary_object_id'] == 'V1'
    assert updated['revision'] == 2
    conflict = client.patch('/api/v1/cases/%s/target-groups/%s' % (case_id, body['id']),
                            json={**swapped, 'revision': 1})
    assert conflict.status_code == 409
