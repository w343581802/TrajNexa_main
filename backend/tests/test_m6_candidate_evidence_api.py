"""B8: read-only candidate evidence image (full frame / reproducible crop)."""
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from app.models import (Case, MediaAsset, TargetObservation, TrackingCandidate,
                        TrackingCandidateReview, TrackingRun)


def _seed(Session, tmp: Path, *, details=None, reviews=(), refs=None):
    case_id = str(uuid4())
    media_id = str(uuid4())
    obs_id = str(uuid4())
    run_id = str(uuid4())
    candidate_id = str(uuid4())
    frame = (tmp / 'storage' / 'tracking_runs' / run_id / 'w_cam_54000' / '0001.jpg')
    frame.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(frame), np.full((120, 160, 3), 90, np.uint8))
    with Session() as db:
        db.add(Case(id=case_id, code='C-' + case_id[:8], name='case'))
        db.add(MediaAsset(id=media_id, case_id=case_id, kind='image',
                          original_name='a.jpg', mime_detected='image/jpeg',
                          sha256='0' * 64, storage_relpath='media/a.jpg', size_bytes=1))
        db.add(TargetObservation(id=obs_id, case_id=case_id, media_id=media_id,
                                 object_class='person',
                                 analysis_json={'bbox_pixels': [10, 20, 110, 220]},
                                 review_note='n', review_state='pending'))
        db.add(TrackingRun(id=run_id, case_id=case_id, observation_id=obs_id,
                           status='awaiting_review', anchor_json={}))
        db.add(TrackingCandidate(
            id=candidate_id, run_id=run_id, candidate_key=candidate_id,
            camera_label='1-LT-桂康路松山巷', track_id='t1',
            state='weak_candidate', identity_status='candidate_unverified',
            evidence_refs=refs if refs is not None else ['tracking_runs/%s/w_cam_54000/0001.jpg' % run_id],
            details_json=details or {}))
        for index, review in enumerate(reviews):
            db.add(TrackingCandidateReview(
                id=str(uuid4()), run_id=run_id, candidate_id=candidate_id,
                camera_label='1-LT-桂康路松山巷', overall='insufficient_evidence',
                review_json=review))
        db.commit()
    return {'case_id': case_id, 'run_id': run_id, 'candidate_id': candidate_id}


def _url(seed):
    return ('/api/v1/cases/%s/tracking-runs/%s/candidates/%s/evidence/image'
            % (seed['case_id'], seed['run_id'], seed['candidate_id']))


def test_full_frame_is_returned_with_provenance(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp, details={'frame_no': 1})
    resp = client.get(_url(seed), params={'authorized': 'true', 'kind': 'full'})
    assert resp.status_code == 200, resp.text
    assert resp.headers['content-type'] == 'image/jpeg'
    assert resp.headers['x-evidence-kind'] == 'full'
    assert resp.headers['x-evidence-frame'] == '1'
    assert cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (120, 160)


def test_crop_uses_details_bbox(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp, details={'bbox_pixels': [20, 10, 80, 60], 'frame_no': 1})
    resp = client.get(_url(seed), params={'authorized': 'true', 'kind': 'crop'})
    assert resp.status_code == 200, resp.text
    assert resp.headers['x-evidence-bbox'] == '16,6,84,64'
    image = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape[0] > 0 and image.shape[1] > 0


def test_crop_falls_back_to_latest_review_bbox(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp, reviews=({'bbox_pixels': [30, 20, 100, 90]},
                                        {'bbox_pixels': [40, 25, 90, 70]}))
    resp = client.get(_url(seed), params={'authorized': 'true', 'kind': 'crop'})
    assert resp.status_code == 200, resp.text
    assert resp.headers['x-evidence-bbox'] == '36,21,94,74'


def test_old_evidence_ref_without_window_subdir_is_resolved_by_basename(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp, details={'bbox_pixels': [10, 10, 50, 50]},
                 refs=['tracking_runs/%s/0001.jpg' % 'x'])
    resp = client.get(_url(seed), params={'authorized': 'true', 'kind': 'full'})
    assert resp.status_code == 200, resp.text
    assert cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR) is not None


def test_missing_bbox_returns_explicit_conflict_not_fake_crop(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    resp = client.get(_url(seed), params={'authorized': 'true', 'kind': 'crop'})
    assert resp.status_code == 409, resp.text
    assert resp.json()['error']['code'] == 'candidate-crop-bbox-missing'


def test_requires_explicit_authorization(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    assert client.get(_url(seed)).status_code == 422
    assert client.get(_url(seed), params={'authorized': 'false'}).status_code == 422


def test_candidate_must_belong_to_the_case(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    other = _seed(Session, tmp)
    resp = client.get(_url(seed).replace(seed['case_id'], other['case_id']),
                      params={'authorized': 'true'})
    assert resp.status_code == 404
