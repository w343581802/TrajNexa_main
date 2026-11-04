"""B8-B: persistent tracking runs bound to case-owned recordings.

These tests use a fake analyzer: they verify orchestration, persistence, case
isolation and review semantics, not detector/ReID accuracy.
"""
from pathlib import Path
from uuid import uuid4

import pytest

from app.models import (Case, MediaAsset, RecordingClip, RecordingSource,
                        TargetObservation, TrackingCandidate, TrackingRun)
from app import m6_run_worker
from app.config import settings


@pytest.fixture(autouse=True)
def _recordings_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'recordings_root', tmp_path)


def _seed(Session, tmp_path: Path, *, case_id=None, camera='2-2',
          start='2026-10-23 15:00:00', duration=60.0, match_status='matched'):
    case_id = case_id or str(uuid4())
    media_id = str(uuid4())
    obs_id = str(uuid4())
    source_id = str(uuid4())
    clip_id = str(uuid4())
    folder = tmp_path / 'recordings' / case_id
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / 'cam.mp4'
    video.write_bytes(b'not-a-real-video')
    st = video.stat()
    with Session() as db:
        db.add(Case(id=case_id, code='C-' + case_id[:8], name='case'))
        db.add(MediaAsset(id=media_id, case_id=case_id, kind='image',
                          original_name='a.jpg', mime_detected='image/jpeg',
                          sha256='0' * 64, storage_relpath='media/a.jpg', size_bytes=1))
        db.add(TargetObservation(id=obs_id, case_id=case_id, media_id=media_id,
                                 object_class='person',
                                 analysis_json={'bbox_pixels': [10, 20, 110, 220]},
                                 review_note='n', review_state='pending'))
        db.add(RecordingSource(id=source_id, case_id=case_id, path='recordings/' + case_id))
        db.add(RecordingClip(id=clip_id, source_id=source_id, relpath='recordings/' + case_id + '/cam.mp4',
                             file_name='cam.mp4', size_bytes=st.st_size, mtime_ns=st.st_mtime_ns,
                             duration_seconds=duration, start_local=start, start_source='overlay',
                             camera_name=camera, match_status=match_status, ocr_status='ok'))
        db.commit()
    return {'case_id': case_id, 'observation_id': obs_id, 'clip_id': clip_id,
            'media_id': media_id}


class FakeAnalyzer:
    """No models: returns two tracks and close scores so the top is only a weak candidate."""

    def __init__(self, scores=(0.66, 0.63)):
        self.scores = scores
        self.calls = []

    def analyze(self, plan, out_dir):
        self.calls.append(plan['key'])
        out_dir.mkdir(parents=True, exist_ok=True)
        for i in (1, 2):
            (out_dir / ('%04d.jpg' % i)).write_bytes(b'jpg')
        return {
            'status': 'ok',
            'tracks': [
                {'track_id': 'a:1', 'observations': [{'frame_id': 0, 'bbox': [1, 1, 9, 19]}]},
                {'track_id': 'a:2', 'observations': [{'frame_id': 0, 'bbox': [20, 1, 30, 19]}]},
            ],
            'cost': {'frames_saved': 2, 'frames_requested': 2},
            'evidence_refs': ['tracking_runs/w/0001.jpg'],
            'frames_dir': str(out_dir),
        }

    def score(self, result):
        return [{'track_id': 'a:1', 'retrieval_score': self.scores[0]},
                {'track_id': 'a:2', 'retrieval_score': self.scores[1]}]


def _create(client, seed, **overrides):
    body = {'observation_id': seed['observation_id'], 'clip_id': seed['clip_id'],
            'offset_seconds': 1.5, 'segment_seconds': 6.0, 'max_windows': 2}
    body.update(overrides)
    return client.post('/api/v1/cases/%s/tracking-runs' % seed['case_id'], json=body)


def test_run_persists_and_candidates_stay_unverified(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    resp = _create(client, seed)
    assert resp.status_code == 201, resp.text
    run = resp.json()
    assert run['status'] == 'queued'
    assert run['anchor']['camera_label'] == '2-2'
    assert run['anchor']['anchor_seconds'] == 54000.0 + 1.5
    assert run['identity_established'] is False

    with Session() as db:
        result = m6_run_worker.execute_run(db, run['id'], analyzer=FakeAnalyzer()
                                           , storage_root=tmp)
    assert result.status == 'awaiting_review'

    candidates = client.get('/api/v1/cases/%s/tracking-runs/%s/candidates'
                            % (seed['case_id'], run['id'])).json()
    assert len(candidates) == 1, candidates
    assert candidates[0]['identity_status'] == 'candidate_unverified'
    assert candidates[0]['state'] == 'weak_candidate'
    assert candidates[0]['review_required'] is True

    detail = client.get('/api/v1/cases/%s/tracking-runs/%s'
                        % (seed['case_id'], run['id'])).json()
    assert detail['candidates'] == 1
    assert detail['windows'] == 1
    assert detail['identity_established'] is False


def test_review_is_append_only_and_can_confirm(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    run = _create(client, seed).json()
    with Session() as db:
        m6_run_worker.execute_run(db, run['id'], analyzer=FakeAnalyzer(), storage_root=tmp)
    candidate = client.get('/api/v1/cases/%s/tracking-runs/%s/candidates'
                           % (seed['case_id'], run['id'])).json()[0]

    url = '/api/v1/cases/%s/tracking-runs/%s/reviews' % (seed['case_id'], run['id'])
    approved = client.post(url, json={'candidate_id': candidate['id'],
                                       'decision': 'approve', 'reviewer': 'reviewer-a'})
    assert approved.status_code == 201, approved.text
    assert approved.json()['review']['decision'] == 'approve'
    assert approved.json()['run']['status'] == 'queued'  # approved -> continue search
    after = client.get('/api/v1/cases/%s/tracking-runs/%s/candidates'
                       % (seed['case_id'], run['id'])).json()[0]
    assert after['identity_status'] == 'human_confirmed'

    revoked = client.post(url, json={'candidate_id': candidate['id'],
                                      'decision': 'revoke', 'reviewer': 'reviewer-b'})
    assert revoked.status_code == 201
    assert revoked.json()['review']['decision'] == 'revoke'
    events = client.get(url).json()
    assert [e['decision'] for e in events] == ['approve', 'revoke']
    final = client.get('/api/v1/cases/%s/tracking-runs/%s/candidates'
                       % (seed['case_id'], run['id'])).json()[0]
    assert final['identity_status'] == 'candidate_unverified'


def test_cross_case_access_is_404(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    other = _seed(Session, tmp)
    run = _create(client, seed).json()
    assert client.get('/api/v1/cases/%s/tracking-runs/%s'
                      % (other['case_id'], run['id'])).status_code == 404
    assert client.get('/api/v1/cases/%s/tracking-runs/%s/candidates'
                      % (other['case_id'], run['id'])).status_code == 404


def test_cannot_start_from_another_cases_recording(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    other = _seed(Session, tmp)
    resp = _create(client, seed, clip_id=other['clip_id'])
    assert resp.status_code == 422, resp.text
    assert resp.json()['error']['code'] == 'clip_not_found'


def test_cancel_stops_further_review(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    run = _create(client, seed).json()
    cancelled = client.post('/api/v1/cases/%s/tracking-runs/%s/cancel'
                            % (seed['case_id'], run['id']))
    assert cancelled.status_code == 200
    assert cancelled.json()['status'] == 'cancelled'
    review = client.post('/api/v1/cases/%s/tracking-runs/%s/reviews'
                         % (seed['case_id'], run['id']),
                         json={'candidate_id': 'x', 'decision': 'approve'})
    assert review.status_code == 409


def test_resume_does_not_duplicate_windows(env):
    client, Session, tmp = env
    seed = _seed(Session, tmp)
    run = _create(client, seed).json()
    analyzer = FakeAnalyzer()
    with Session() as db:
        m6_run_worker.execute_run(db, run['id'], analyzer=analyzer, storage_root=tmp)
    with Session() as db:
        row = db.get(TrackingRun, run['id'])
        row.status = 'queued'; db.commit()
        m6_run_worker.execute_run(db, run['id'], analyzer=analyzer, storage_root=tmp)
    assert len(analyzer.calls) == 1
    windows = client.get('/api/v1/cases/%s/tracking-runs/%s/windows'
                         % (seed['case_id'], run['id'])).json()
    assert len(windows) == 1


def test_window_plan_seeks_relative_to_clip_start():
    """窗口起点是当日绝对秒；取帧必须换算成该段录像内的偏移。"""
    from types import SimpleNamespace

    clip = SimpleNamespace(
        id='clip-1', relpath='a/b.mp4', camera_name='2-1',
        start_local='2026-10-23T15:00:00', duration_seconds=60.0,
    )
    run = SimpleNamespace(
        anchor_json={'camera_label': '2-1', 'anchor_seconds': 54000.0},
        scope_json={'segment_seconds': 4.0, 'gap_seconds': 2.0, 'max_windows': 1},
    )
    plans = m6_run_worker.plan_windows(run, [clip], set())
    assert plans and plans[0]['start_seconds'] == 54002.0
    assert plans[0]['seek_seconds'] == 2.0
    assert plans[0]['duration_seconds'] == 4.0


def test_planner_skips_unmatched_camera_clip(env):
    """未匹配摄像头的段只有 OCR 名称，不能作为后续窗口。"""
    client, Session, tmp = env
    seed = _seed(Session, tmp, match_status='unmatched')
    with Session() as db:
        assert m6_run_worker._case_clips(db, seed['case_id']) == []


def test_window_budget_counts_windows_already_analyzed():
    """max_windows 是整次任务的预算，审核通过后不会无限追加窗口。"""
    from types import SimpleNamespace

    clip = SimpleNamespace(
        id='clip-1', relpath='a/b.mp4', camera_name='2-1',
        start_local='2026-10-23T15:00:00', duration_seconds=60.0,
        match_status='matched',
    )
    run = SimpleNamespace(
        anchor_json={'camera_label': '2-1', 'anchor_seconds': 54000.0},
        scope_json={'segment_seconds': 4.0, 'gap_seconds': 2.0, 'max_windows': 1},
    )
    assert m6_run_worker.plan_windows(run, [clip], {'2-1@54002.0'}) == []


def test_one_execution_advances_one_camera():
    """每次执行只分析下一个镜头；预算控制整次任务的总窗口数。"""
    from types import SimpleNamespace

    def clip(camera, cid):
        return SimpleNamespace(
            id=cid, relpath='a/%s.mp4' % cid, camera_name=camera,
            start_local='2026-10-23T15:00:00', duration_seconds=60.0,
            match_status='matched',
        )

    run = SimpleNamespace(
        anchor_json={'camera_label': '2-2', 'anchor_seconds': 54000.0},
        scope_json={'segment_seconds': 4.0, 'gap_seconds': 2.0, 'max_windows': 3},
    )
    clips = [clip('2-1', 'c1'), clip('2-2', 'c2')]
    plans = m6_run_worker.plan_windows(run, clips, set())
    assert len(plans) == 1 and plans[0]['camera_label'] == '2-1'
    next_plans = m6_run_worker.plan_windows(run, clips, {plans[0]['key']})
    assert len(next_plans) == 1 and next_plans[0]['camera_label'] == '2-2'
