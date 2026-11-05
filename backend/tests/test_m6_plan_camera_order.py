from types import SimpleNamespace

from app import m6_run_worker
from app.models import RecordingClip


def _clip(camera, clip_id):
    return RecordingClip(id=clip_id, camera_name=camera,
                         relpath='x/' + clip_id + '.mp4', file_name=clip_id + '.mp4',
                         size_bytes=1, mtime_ns=1, duration_seconds=600,
                         start_local='2026-10-23T14:59:55', start_source='overlay',
                         camera_uuid='u-' + clip_id, match_status='matched')


def _run(camera_order):
    return SimpleNamespace(
        anchor_json={'camera_label': 'A-起始', 'anchor_seconds': 54005.0},
        scope_json={'segment_seconds': 8, 'gap_seconds': 2, 'max_windows': 4,
                    'direction': 'forward', 'camera_order': camera_order})


def test_camera_order_overrides_alphabetical_default():
    clips = [_clip('A-起始', 'a'), _clip('B-二号', 'b'), _clip('C-三号', 'c')]
    plan = m6_run_worker.plan_windows(_run(['C-三号', 'A-起始']), clips, set())
    assert plan and plan[0]['camera_label'] == 'C-三号'


def test_without_camera_order_keeps_alphabetical_behaviour():
    clips = [_clip('B-二号', 'b'), _clip('A-起始', 'a')]
    plan = m6_run_worker.plan_windows(_run([]), clips, set())
    assert plan and plan[0]['camera_label'] == 'B-二号'
