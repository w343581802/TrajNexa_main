from pathlib import Path

from app import video_naming, video_naming_api
from app.config import settings
from app.models import AnalysisJob, InspectionReport, MediaAsset


def fake_outcome(**overrides):
    value = {
        'duration_seconds': 31.0,
        'start_local': '2026-10-23T14:59:57',
        'start_source': 'ocr',
        'camera_name': '3-DX-中仁路98号-球',
        'camera_uuid': '11111111-1111-1111-1111-111111111111',
        'match_status': 'matched',
        'ocr_status': 'ran',
        'suggestions': [],
    }
    value.update(overrides)
    return value


def make_video(path: Path, payload: bytes = b'video-bytes') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def make_media(path: Path, name: str = 'raw.mp4') -> MediaAsset:
    return MediaAsset(id='media-1', case_id=None, kind='video', original_name=name,
                      mime_detected='video/mp4', sha256='',
                      storage_relpath=str(path), size_bytes=path.stat().st_size)


def test_build_renamed_name_uses_ocr_name_and_time():
    assert video_naming.build_renamed_name(
        'raw.mp4', '3-DX-中仁路98号-球', '2026-10-23T14:59:57'
    ) == '3-DX-中仁路98号-球_20261023_145957.mp4'


def test_build_renamed_name_marks_missing_ocr_and_time():
    name = video_naming.build_renamed_name('raw.mp4', None, None)
    assert name == 'raw_OCR未识别_时间未识别.mp4'


def test_safe_component_blocks_path_characters():
    assert video_naming.safe_component('a/b\\c:*?') == 'a_b_c'


def test_process_media_renames_local_file_in_place(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'storage')
    monkeypatch.setattr(video_naming, '_load_name_catalog', lambda: ({}, None))
    monkeypatch.setattr(video_naming.recordings, 'inspect_first_seconds',
                        lambda *args, **kwargs: fake_outcome())
    monkeypatch.setattr(video_naming, '_save_evidence_frame', lambda *args: None)
    batch_id = '22222222-2222-2222-2222-222222222222'
    source = make_video(tmp_path / 'videos' / 'raw.mp4')
    media = make_media(source)

    result = video_naming.process_media(media, source, batch_id)

    assert result['renamed_name'] == '3-DX-中仁路98号-球_20261023_145957.mp4'
    assert result['camera_name'] == '3-DX-中仁路98号-球'
    assert result['capture_time'] == '2026-10-23T14:59:57'
    assert result['needs_review'] is False
    renamed = Path(result['renamed_path'])
    assert renamed.is_file() and not source.exists()
    assert renamed.parent == source.parent
    assert media.storage_relpath == str(renamed)

    video_naming.revert_rename(result)
    assert source.is_file() and not renamed.exists()


def test_process_media_keeps_name_when_ocr_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'storage')
    monkeypatch.setattr(video_naming, '_load_name_catalog', lambda: ({}, None))
    monkeypatch.setattr(video_naming.recordings, 'inspect_first_seconds',
                        lambda *args, **kwargs: fake_outcome(
                            camera_name=None, camera_uuid=None, start_local=None,
                            start_source='', ocr_status='unavailable', match_status='no_name'))
    monkeypatch.setattr(video_naming, '_save_evidence_frame', lambda *args: None)
    batch_id = '33333333-3333-3333-3333-333333333333'
    source = make_video(tmp_path / 'videos' / '原始.mp4')
    media = make_media(source, '原始.mp4')

    result = video_naming.process_media(media, source, batch_id)

    assert result['needs_review'] is True
    assert result['renamed_name'] == '原始_OCR未识别_时间未识别.mp4'
    assert Path(result['renamed_path']).is_file()
    assert any('OCR' in warning for warning in result['warnings'])


def test_csv_has_bom_and_chinese_headers():
    text = video_naming.csv_text([{
        'original_name': 'a.mp4', 'renamed_name': 'b.mp4',
        'original_path': '/data/videos/a.mp4', 'renamed_path': '/data/videos/b.mp4',
        'camera_name': 'c', 'capture_time': '2026-10-23T14:59:57', 'time_source': 'ocr',
        'ocr_status': 'ran', 'match_status': 'matched', 'duration_seconds': 31,
        'size_bytes': 1024 * 1024, 'status': 'succeeded', 'needs_review': False,
        'warnings': [],
    }])
    assert text.startswith('\ufeff')
    assert '原始文件名' in text and '新文件名' in text
    assert '原路径' in text and '新路径' in text
    assert '/data/videos/b.mp4' in text


def test_create_batch_scans_directory(env, tmp_path: Path):
    client, Session, _ = env
    root = tmp_path / 'videos'
    make_video(root / 'a.mp4')
    make_video(root / 'nested' / 'b.mkv')
    make_video(root / 'notes.txt')

    response = client.post('/api/v1/video-tools/naming/batches',
                           json={'paths': [str(root)], 'recursive': True})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body['total'] == 2 and body['status'] == 'processing'
    assert [item['original_name'] for item in body['items']] == ['a.mp4', 'b.mkv']
    assert body['items'][0]['original_path'].endswith('a.mp4')

    with Session() as db:
        from app.models import AnalysisJob, MediaAsset
        assert db.query(MediaAsset).filter(MediaAsset.id.in_(
            item['media_id'] for item in body['items'])).count() == 2
        assert db.query(AnalysisJob).filter(AnalysisJob.module.like('video_naming:%')).count() == 2


def test_create_batch_accepts_single_file(env, tmp_path: Path):
    client, _, _ = env
    path = make_video(tmp_path / 'clip.mp4')
    response = client.post('/api/v1/video-tools/naming/batches', json={'paths': [str(path)]})
    assert response.status_code == 202, response.text
    assert response.json()['total'] == 1


def test_create_batch_rejects_missing_path(env):
    client, _, _ = env
    response = client.post('/api/v1/video-tools/naming/batches',
                           json={'paths': ['/no/such/video.mp4']})
    assert response.status_code == 404
    assert response.json()['error']['code'] == 'source_not_found'


def test_create_batch_rejects_non_video(env, tmp_path: Path):
    client, _, _ = env
    path = tmp_path / 'a.txt'
    path.write_text('not a video')
    response = client.post('/api/v1/video-tools/naming/batches', json={'paths': [str(path)]})
    assert response.status_code == 415
    assert response.json()['error']['code'] == 'unsupported_video'


def test_create_batch_rejects_empty_directory(env, tmp_path: Path):
    client, _, _ = env
    root = tmp_path / 'empty'
    root.mkdir()
    response = client.post('/api/v1/video-tools/naming/batches', json={'paths': [str(root)]})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'no_videos'


def test_batch_table_endpoint_returns_csv(env, tmp_path: Path):
    client, _, _ = env
    path = make_video(tmp_path / 'a.mp4')
    created = client.post('/api/v1/video-tools/naming/batches',
                          json={'paths': [str(path)]}).json()
    response = client.get(created['table_url'])
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    assert '原始文件名' in response.text and '新路径' in response.text


def test_translate_host_path_to_writable_mount(monkeypatch):
    monkeypatch.setattr(settings, 'video_naming_path_map', r'G:\测试数据\视频=/data/video-library')
    assert video_naming_api._translate_host_path(r'G:\测试数据\视频\正常\镜头1') == \
        '/data/video-library/正常/镜头1'
    assert video_naming_api._translate_host_path('/data/video-library/正常') == \
        '/data/video-library/正常'


def test_finalize_folders_renames_folder_when_camera_consistent(env, tmp_path: Path):
    client, Session, _ = env
    root = tmp_path / '正常'
    sub = root / 'YD-桂康路中仁路T口2-1_20261023150000至20261023153059'
    make_video(sub / 'a.mp4')
    make_video(sub / 'b.mp4')
    body = client.post('/api/v1/video-tools/naming/batches',
                       json={'paths': [str(root)], 'rename_folders': True}).json()
    assert body['total'] == 2

    with Session() as db:
        jobs = list(db.query(AnalysisJob).filter(AnalysisJob.module ==
                    video_naming.batch_module(body['batch_id'])))
        assert len(jobs) == 2
        for index, job in enumerate(jobs):
            job.status = 'succeeded'
            db.add(InspectionReport(
                id=f'report-{index}', job_id=job.id, media_id=job.media_id,
                schema_version='video-naming-1', module_version='video-naming-0.1',
                result_json={
                    'original_path': str(sub / f'{index}.mp4'),
                    'renamed_path': str(sub / f'T口2-2_{index}.mp4'),
                    'camera_name': 'T口2-2',
                    'capture_time': f'2026-10-23T15:00:{1 + index * 30:02d}',
                    'duration_seconds': 30,
                }))
        db.commit()
        assert video_naming.finalize_folders(db, body['batch_id']) == 1
        folders = [path.name for path in root.iterdir()]
        assert folders == ['T口2-2_20261023150001至20261023150101']


def test_manual_rename_renames_file_on_disk(env, tmp_path: Path):
    client, Session, _ = env
    path = make_video(tmp_path / 'a.mp4')
    created = client.post('/api/v1/video-tools/naming/batches',
                          json={'paths': [str(path)]}).json()
    media_id = created['items'][0]['media_id']
    with Session() as db:
        job = db.query(AnalysisJob).filter(AnalysisJob.media_id == media_id).one()
        job.status = 'succeeded'
        db.add(InspectionReport(
            id='manual-report', job_id=job.id, media_id=media_id,
            schema_version='video-naming-1', module_version='video-naming-0.1',
            result_json={
                'original_name': 'a.mp4', 'original_path': str(path),
                'renamed_name': 'a_OCR未识别_时间未识别.mp4', 'renamed_path': str(path),
                'needs_review': True, 'warnings': ['未从画面角标识别到摄像头名称'],
            }))
        db.commit()

    response = client.post(
        f"/api/v1/video-tools/naming/batches/{created['batch_id']}/items/{media_id}/rename",
        json={'name': '人工命名'})
    assert response.status_code == 200
    item = response.json()
    assert item['renamed_name'] == '人工命名.mp4'
    assert item['needs_review'] is False
    assert (tmp_path / '人工命名.mp4').is_file()
    assert not path.exists()


def test_manual_rename_rejects_extension_change(env, tmp_path: Path):
    client, Session, _ = env
    path = make_video(tmp_path / 'a.mp4')
    created = client.post('/api/v1/video-tools/naming/batches',
                          json={'paths': [str(path)]}).json()
    media_id = created['items'][0]['media_id']
    with Session() as db:
        job = db.query(AnalysisJob).filter(AnalysisJob.media_id == media_id).one()
        job.status = 'succeeded'
        db.commit()
    response = client.post(
        f"/api/v1/video-tools/naming/batches/{created['batch_id']}/items/{media_id}/rename",
        json={'name': '改名.exe'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'extension_changed'


def test_no_frame_keeps_name_and_flags_review(tmp_path: Path, monkeypatch):
    """取不到可用画面时：保留原名口径，明确标 OCR 状态，不许假装识别成功。"""
    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'storage')
    monkeypatch.setattr(video_naming, '_load_name_catalog', lambda: ({}, None))
    monkeypatch.setattr(video_naming.recordings, 'inspect_first_seconds',
                        lambda *args, **kwargs: fake_outcome(
                            start_local=None, start_source='', camera_name=None,
                            camera_uuid=None, match_status='no_name', ocr_status='no_frame'))
    monkeypatch.setattr(video_naming, '_save_evidence_frame', lambda *args: None)
    source = make_video(tmp_path / 'videos' / 'raw.mp4')

    result = video_naming.process_media(make_media(source), source, 'b-1')

    assert result['ocr_status'] == 'no_frame'
    assert result['camera_name'] is None and result['capture_time'] is None
    assert result['renamed_name'] == 'raw_OCR未识别_时间未识别.mp4'
    assert result['needs_review'] is True
    assert result['frame_relpath'] is None
    assert any('取不到可用画面' in w for w in result['warnings'])
    assert any('证据截图' in w for w in result['warnings'])


def test_evidence_frame_tries_later_offsets(tmp_path: Path, monkeypatch):
    """前几处取不到画面时要继续往后试，别只试 0.5/2.0 就放弃。"""
    taken = []

    def fake_read_frame(path, offset):
        taken.append(offset)
        return None if offset < 4.0 else object()

    monkeypatch.setattr(settings, 'storage_root', tmp_path / 'storage')
    monkeypatch.setattr(video_naming.overlay_identity.overlay_ocr, 'read_frame', fake_read_frame)

    frames = []

    class FakeCv2:
        IMWRITE_JPEG_QUALITY = 1

        @staticmethod
        def imencode(suffix, frame, params):
            return True, type('E', (), {'tobytes': lambda self: b'jpg'})()

    monkeypatch.setattr(video_naming, 'cv2', FakeCv2)
    relpath = video_naming._save_evidence_frame(tmp_path / 'x.mp4', tmp_path / 'batch', 'm-1')

    assert taken == [0.5, 2.0, 4.0]
    assert relpath is None or relpath.endswith('m-1.jpg')
    assert (tmp_path / 'batch' / 'frames' / 'm-1.jpg').is_file()


def test_evidence_frame_returns_none_when_all_offsets_fail(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(video_naming.overlay_identity.overlay_ocr, 'read_frame', lambda *a: None)
    assert video_naming._save_evidence_frame(tmp_path / 'x.mp4', tmp_path / 'batch', 'm-2') is None
    assert not (tmp_path / 'batch' / 'frames' / 'm-2.jpg').exists()
