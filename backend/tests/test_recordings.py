"""录像资源接线：目录浏览、添加目录、扫描（只读开头几秒）、自动扫描与删除边界。

这些用例不需要真实视频：probe/OCR 全部替身，验证的是「按什么顺序读、读到的结果怎么落库、
什么时候跳过已扫文件、目录越界与删除案件时不碰录像文件」。
"""
import json

import numpy as np
from datetime import datetime, timedelta, timezone

import pytest

from app import overlay_identity, overlay_ocr, recordings, worker
from app.config import settings
from app.models import RecordingClip, RecordingScanJob, RecordingSource

CAM_A = '11111111-1111-4111-8111-111111111111'


def write_catalog(count=1):
    path = settings.storage_root / 'camera_catalog.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'schema_version': '1.0',
        'spatial_revision': 'demo-rev',
        'cameras': [{'camera_uuid': CAM_A, 'display_name': '东门球机 A',
                     'aliases': ['东门A'], 'active': True}],
    }, ensure_ascii=False), encoding='utf-8')


def make_tree(root, name='白天', count=2):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for index in range(count):
        path = folder / f'东门球机A_{20261025 + index:08d}180000.mp4'
        path.write_bytes(b'fake-video')
        files.append(path)
    return folder, files


@pytest.fixture()
def video_env(env, monkeypatch, tmp_path):
    client, Session, _ = env
    root = tmp_path / 'videos'
    root.mkdir()
    monkeypatch.setattr(settings, 'recordings_root', root)
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    # 测试读本地 JSON 台账，不连 Map 的 PostGIS
    monkeypatch.setattr(settings, 'map_db_readonly_url', '')
    write_catalog()
    return client, Session, root


def patch_ocr(monkeypatch, *, name='东门球机 A', local_time='2026-10-25T18:00:05', calls=None):
    calls = calls if calls is not None else {'frames': 0, 'reads': 0}

    def read_frame(path, offset):
        calls['frames'] += 1
        return 'frame'

    def read_overlay_texts(frame, recognizer=None):
        calls['reads'] += 1
        return [{'text': '2026-10-25 18:00:05', 'confidence': 0.99, 'region': 'top'},
                {'text': f'摄像头：{name}', 'confidence': 0.99, 'region': 'bottom'}]

    monkeypatch.setattr(overlay_ocr, 'read_frame', read_frame)
    monkeypatch.setattr(overlay_ocr, 'read_overlay_texts', read_overlay_texts)
    monkeypatch.setattr(overlay_ocr, 'parse_overlay_rows',
                        lambda rows: {'local_time': local_time, 'time_text': 'x',
                                      'name': name, 'name_status': 'labelled_candidate'})
    # 时长探测已移到 overlay_identity（录像资源 / 命名 / 添加要素共用），patch 在新家
    monkeypatch.setattr(overlay_identity, 'probe_duration', lambda path: 120.0)
    return calls


def add_source(client, case_id, path='白天', auto_scan=False):
    response = client.post('/api/v1/recordings/sources',
                           json={'case_id': case_id, 'path': path, 'auto_scan': auto_scan})
    assert response.status_code == 201, response.text
    return response.json()


def make_case(client, name='录像接线'):
    response = client.post('/api/v1/cases', json={'name': name})
    assert response.status_code == 201, response.text
    return response.json()


def test_source_accepts_host_path_container_path_and_file(video_env, monkeypatch):
    """录像资源也是「前端传路径 / 后端读本地文件 / 支持目录」，与视频命名工具同一套口径。"""
    client, _Session, root = video_env
    _folder, files = make_tree(root, '白天', count=1)
    case = make_case(client)
    monkeypatch.setattr(settings, 'media_input_path_map', 'G:\\测试数据\\视频=' + str(root))

    # 1) 宿主机路径：按映射换成录像根目录下的相对路径后登记
    host = client.post('/api/v1/recordings/sources',
                       json={'case_id': case['id'], 'path': r'G:\测试数据\视频\白天'})
    assert host.status_code == 201, host.text
    assert host.json()['path'] == '白天'

    # 2) 传单个录像文件：登记它所在目录
    by_file = client.post('/api/v1/recordings/sources',
                          json={'case_id': case['id'], 'path': str(files[0])})
    assert by_file.status_code == 201, by_file.text
    assert by_file.json()['path'] == '白天'

    # 3) 目录浏览也认宿主机路径
    browse = client.get('/api/v1/recordings/browse',
                        params={'path': r'G:\测试数据\视频\白天'})
    assert browse.status_code == 200, browse.text
    assert browse.json()['path'] == '白天'
    assert browse.json()['videos'][0]['name'] == files[0].name


def test_source_rejects_paths_outside_recording_root(video_env, tmp_path):
    """录像资源是只读输入：根目录外的路径一律拒绝，不能借它读别处。"""
    client, _Session, _root = video_env
    case = make_case(client)
    outside = tmp_path / '别的地方'
    outside.mkdir()
    response = client.post('/api/v1/recordings/sources',
                           json={'case_id': case['id'], 'path': str(outside)})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'recording_path_invalid'


def test_browse_lists_subdirs_and_videos(video_env):
    client, _Session, root = video_env
    make_tree(root, '白天', count=2)
    make_tree(root, '夜间', count=0)

    data = client.get('/api/v1/recordings/browse').json()
    assert data['path'] == ''
    assert data['root'] == str(root)
    names = {item['name']: item for item in data['dirs']}
    assert names['白天']['video_count'] == 2
    assert names['白天']['path'] == '白天'
    assert names['夜间']['video_count'] == 0
    assert data['videos'] == []

    inside = client.get('/api/v1/recordings/browse', params={'path': '白天'}).json()
    assert inside['parent'] == ''
    assert len(inside['videos']) == 2


def test_browse_rejects_escape_root(video_env):
    client, _Session, _root = video_env
    response = client.get('/api/v1/recordings/browse', params={'path': '../'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'recording_path_invalid'


def test_scan_reads_start_time_and_matches_camera(video_env, monkeypatch):
    client, Session, root = video_env
    make_tree(root, '白天', count=2)
    case = make_case(client)
    source = add_source(client, case['id'])
    calls = patch_ocr(monkeypatch)

    started = client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    assert started.status_code == 202
    assert started.json()['job']['status'] == 'queued'

    with Session() as db:
        assert worker.run_once(db) is True

    clips = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()
    assert len(clips) == 2
    assert {clip['start_local'] for clip in clips} == {'2026-10-25T18:00:05'}
    assert {clip['start_source'] for clip in clips} == {'ocr'}
    assert {clip['camera_uuid'] for clip in clips} == {CAM_A}
    assert {clip['match_status'] for clip in clips} == {'matched'}
    # 只读开头几秒：读到时间与名称就停，不会把整段录像读完
    assert calls['reads'] <= 4

    summary = client.get('/api/v1/recordings/summary', params={'case_id': case['id']}).json()
    assert summary['file_count'] == 2
    assert summary['matched_count'] == 2
    assert summary['camera_uuids'] == [CAM_A]
    assert summary['scanning'] is False


def test_rescan_skips_unchanged_files(video_env, monkeypatch):
    client, Session, root = video_env
    make_tree(root, '白天', count=1)
    case = make_case(client)
    source = add_source(client, case['id'])
    calls = patch_ocr(monkeypatch)

    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        worker.run_once(db)
    first_reads = calls['reads']
    assert first_reads >= 1

    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        worker.run_once(db)

    # 文件没变就不再抽帧、不再 OCR
    assert calls['reads'] == first_reads
    with Session() as db:
        assert db.query(RecordingClip).count() == 1
        job = db.query(RecordingScanJob).order_by(RecordingScanJob.created_at.desc()).first()
        assert job.status == 'succeeded'
        assert job.added_files == 0


def test_filename_start_accepts_underscored_stamp():
    """命名工具产出的 名称_20261023_145955.mp4 也要能兜出开始时间。"""
    assert recordings.filename_start('3-DX-中仁路98号-球_20261023_145955.mp4') == '2026-10-23T14:59:55'
    assert recordings.filename_start('3-DX-中仁路98号-球_20261023145956至20261023185127.mp4') == '2026-10-23T14:59:56'
    assert recordings.filename_start('没有时间的名字.mp4') is None
    # 非法日历（13 月 / 99 日）不猜
    assert recordings.filename_start('abc_20261399_999999.mp4') is None


def test_blank_frame_is_retried_with_accurate_seek(monkeypatch, tmp_path):
    """快速 seek 拿到灰占位帧时要退回精确 seek，而不是直接把灰帧交给 OCR。"""
    grey = np.full((180, 320, 3), 128, dtype=np.uint8)
    real = np.random.default_rng(0).integers(0, 255, (180, 320, 3), dtype=np.uint8)
    calls = []

    def fake_ffmpeg(path, offset, accurate=False):
        calls.append(bool(accurate))
        return grey if not accurate else real

    monkeypatch.setattr(overlay_ocr, '_ffmpeg_frame', fake_ffmpeg)
    frame = overlay_ocr._video_frame(tmp_path / 'x.mp4', 0.5)

    assert frame is real
    assert calls == [False, True]


def test_only_blank_frames_yields_no_frame(monkeypatch, tmp_path):
    """精确 seek 也只有灰帧时必须返回 None，让上层标成 no_frame 而不是「已识别」。"""
    grey = np.full((180, 320, 3), 128, dtype=np.uint8)
    monkeypatch.setattr(overlay_ocr, '_ffmpeg_frame', lambda *a, **k: grey)
    assert overlay_ocr._video_frame(tmp_path / 'x.mp4', 0.5) is None


def test_ocr_disabled_uses_filename_start_and_keeps_name_empty(video_env, monkeypatch):
    client, Session, root = video_env
    monkeypatch.setattr(settings, 'ocr_enabled', False)
    folder = root / '白天'
    folder.mkdir(parents=True)
    (folder / '东门_20261025180000.mp4').write_bytes(b'fake-video')
    case = make_case(client)
    source = add_source(client, case['id'])

    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        worker.run_once(db)

    clip = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()[0]
    assert clip['start_local'] == '2026-10-25T18:00:00'
    assert clip['start_source'] == 'filename'
    assert clip['camera_name'] is None
    assert clip['match_status'] == 'ocr_disabled'
    assert clip['ocr_status'] == 'disabled'


def test_auto_scan_enqueues_once_per_interval(video_env):
    client, Session, root = video_env
    make_tree(root, '白天', count=0)
    case = make_case(client)
    source = add_source(client, case['id'], auto_scan=True)

    with Session() as db:
        assert recordings.enqueue_auto_scans(db) == 1
        # 已经排队时不会再排第二次
        assert recordings.enqueue_auto_scans(db) == 0

    with Session() as db:
        worker.run_once(db)
        row = db.get(RecordingSource, source['id'])
        row.last_scan_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        # 未到间隔不排
        assert recordings.enqueue_auto_scans(db) == 0
        row.last_scan_at = datetime.now(timezone.utc) - timedelta(seconds=120)
        db.commit()
        assert recordings.enqueue_auto_scans(db) == 1


def test_delete_case_removes_registration_but_keeps_video_files(video_env, monkeypatch):
    client, Session, root = video_env
    folder, files = make_tree(root, '白天', count=1)
    case = make_case(client)
    source = add_source(client, case['id'])
    patch_ocr(monkeypatch)
    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        worker.run_once(db)

    response = client.delete(f"/api/v1/cases/{case['id']}")
    assert response.status_code == 200
    assert response.json()['recordings_preserved'] is True

    with Session() as db:
        assert db.query(RecordingSource).count() == 0
        assert db.query(RecordingClip).count() == 0
        assert db.query(RecordingScanJob).count() == 0
    # 录像文件一个不少，目录结构也没变
    assert files[0].is_file()
    assert files[0].parent == folder


def test_duration_recorded_but_no_end_time_field(video_env, monkeypatch):
    """扫描只落开始时间与容器时长；不推断结束时间（不出现 end_local 字段）。"""
    client, Session, root = video_env
    make_tree(root, '白天', count=1)
    case = make_case(client)
    source = add_source(client, case['id'])
    patch_ocr(monkeypatch)
    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        worker.run_once(db)
    clip = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()[0]
    assert clip['duration_seconds'] == 120.0
    assert 'end_local' not in clip
    assert 'end_time' not in clip

def test_ocr_no_name_uses_filename_fallback_then_manual_name(video_env, monkeypatch):
    """OCR 读不到名称时用文件名兜底；人工改过名称后，重扫不得回滚。"""
    client, Session, root = video_env
    folder = root / '白天'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / '东门球机 A_20261025180000.mp4'
    path.write_bytes(b'fake-video')
    case = make_case(client)
    source = add_source(client, case['id'])
    # OCR 能读时间，但名称行缺失
    monkeypatch.setattr(overlay_ocr, 'read_frame', lambda path, offset: 'frame')
    monkeypatch.setattr(overlay_ocr, 'read_overlay_texts',
                        lambda frame, recognizer=None: [{'text': '2026-10-25 18:00:05',
                                                        'confidence': 0.99, 'region': 'top'}])
    monkeypatch.setattr(overlay_ocr, 'parse_overlay_rows',
                        lambda rows: {'local_time': '2026-10-25T18:00:05', 'time_text': 'x',
                                      'name': None, 'name_status': ''})
    monkeypatch.setattr(overlay_identity, 'probe_duration', lambda path: 120.0)

    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        assert worker.run_once(db) is True
    clip = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()[0]
    assert clip['camera_name'] == '东门球机 A'
    assert clip['name_source'] == 'filename'
    assert clip['camera_uuid'] == CAM_A
    assert clip['match_status'] == 'matched'

    # 人工核对后把名称改成台账里没有的名字：立即重算匹配，且标记为人工结论
    renamed = client.post(f"/api/v1/recordings/clips/{clip['id']}/name",
                          json={'name': '东门球机 B'})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body['camera_name'] == '东门球机 B'
    assert body['name_source'] == 'manual'
    assert body['review_kind'] == 'confirmed'
    assert body['camera_uuid'] is None
    assert body['match_status'] in ('unmatched', 'ambiguous')

    # 再排一次扫描：文件未变会走 skip 分支，人工改过的名称不能被自动扫描回滚
    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        assert worker.run_once(db) is True
    after = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()[0]
    assert after['camera_name'] == '东门球机 B'
    assert after['name_source'] == 'manual'


def test_manual_name_auto_matches_camera_and_can_clear(video_env, monkeypatch):
    """人工填写的识别名称：命中台账就绑定，清空就解除绑定。"""
    client, Session, root = video_env
    folder = root / '正常'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'YD-某某路口_20261025180000.mp4').write_bytes(b'fake-video')
    case = make_case(client)
    source = add_source(client, case['id'], path='正常')
    patch_ocr(monkeypatch)
    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        assert worker.run_once(db) is True
    clips = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()
    clip = clips[0]

    bound = client.post(f"/api/v1/recordings/clips/{clip['id']}/name", json={'name': '东门A'})
    assert bound.status_code == 200, bound.text
    assert bound.json()['camera_uuid'] == CAM_A
    assert bound.json()['match_status'] == 'matched'
    assert bound.json()['name_source'] == 'manual'

    cleared = client.post(f"/api/v1/recordings/clips/{clip['id']}/name", json={'name': '  '})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()['camera_uuid'] is None
    assert cleared.json()['camera_name'] is None
    assert cleared.json()['match_status'] == 'no_name'


def test_rematch_keeps_manual_name(video_env, monkeypatch):
    """重新匹配摄像头：人工改过名称的片段整体跳过，不覆盖人工结论。"""
    client, Session, root = video_env
    folder = root / '白天'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '东门球机A_20261025180000.mp4').write_bytes(b'fake-video')
    case = make_case(client)
    source = add_source(client, case['id'])
    patch_ocr(monkeypatch)
    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        assert worker.run_once(db) is True
    clip = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()[0]
    assert clip['camera_uuid'] == CAM_A

    client.post(f"/api/v1/recordings/clips/{clip['id']}/name", json={'name': '手工核对的名称'})
    rematch = client.post(f"/api/v1/recordings/sources/{source['id']}/rematch")
    assert rematch.status_code == 202, rematch.text
    assert rematch.json()['human_kept'] == 1
    assert rematch.json()['updated'] == 0
    assert rematch.json()['source_id'] == source['id']

    after = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()[0]
    assert after['camera_name'] == '手工核对的名称'
    assert after['name_source'] == 'manual'


def test_clip_frame_reports_unavailable_for_unreadable_video(video_env, monkeypatch):
    """取不到画面时明确返回 409，不产出假截图。"""
    client, Session, root = video_env
    folder = root / '白天'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '东门球机A_20261025180000.mp4').write_bytes(b'fake-video')
    case = make_case(client)
    source = add_source(client, case['id'])
    patch_ocr(monkeypatch)
    client.post(f"/api/v1/recordings/sources/{source['id']}/scan")
    with Session() as db:
        assert worker.run_once(db) is True
    clip = client.get('/api/v1/recordings/clips', params={'case_id': case['id']}).json()[0]

    monkeypatch.setattr(overlay_identity, 'grab_evidence_frame', lambda path, offsets=None: None)
    response = client.get(f"/api/v1/recordings/clips/{clip['id']}/frame")
    assert response.status_code == 409, response.text
