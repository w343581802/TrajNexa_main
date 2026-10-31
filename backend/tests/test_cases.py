from app.models import AnalysisJob, InspectionReport, MediaAsset
from app.worker import run_once

PNG = b'\x89PNG\r\n\x1a\n' + b'case-demo-content'


def _create_case(client, name='西片夜间轨迹研判', description='聚焦西片三路夜间轨迹'):
    response = client.post('/api/v1/cases', json={'name': name, 'description': description})
    assert response.status_code == 201, response.text
    return response.json()


def test_case_create_list_and_media_binding(env):
    client, _, _ = env
    first = _create_case(client)
    second = _create_case(client, name='地铁A口遗失物')
    assert first['code'].startswith('CASE-')
    assert first['code'] != second['code']
    assert first['media_count'] == 0 and first['media_bytes'] == 0

    media = client.post('/api/v1/media', files={'file': ('overlay.png', PNG, 'image/png')},
                        data={'case_id': first['id']}).json()
    assert media['case_id'] == first['id']
    unassigned = client.post('/api/v1/media', files={'file': ('loose.png', PNG, 'image/png')}).json()
    assert unassigned['case_id'] is None

    listed = {item['id']: item for item in client.get('/api/v1/cases').json()}
    assert listed[first['id']]['media_count'] == 1
    assert listed[first['id']]['media_bytes'] == len(PNG)
    assert listed[second['id']]['media_count'] == 0

    scoped = client.get('/api/v1/media', params={'case_id': first['id']}).json()
    assert [item['id'] for item in scoped] == [media['id']]
    assert len(client.get('/api/v1/media').json()) == 2


def test_delete_case_removes_only_uploaded_element_files(env):
    client, Session, root = env
    case = _create_case(client)
    media = client.post('/api/v1/media', files={'file': ('element.png', PNG, 'image/png')},
                        data={'case_id': case['id']}).json()
    keep = client.post('/api/v1/media', files={'file': ('other.png', PNG, 'image/png')}).json()

    with Session() as db:
        element_path = root / 'storage' / db.get(MediaAsset, media['id']).storage_relpath
        other_path = root / 'storage' / db.get(MediaAsset, keep['id']).storage_relpath
    assert element_path.is_file() and other_path.is_file()

    # 录像资源不在主系统存储里：删除案件不能碰到任何录像目录/文件
    recordings = root / 'recordings' / 'CAM-01'
    recordings.mkdir(parents=True)
    clip = recordings / 'CAM-01_20261025_180000.mp4'
    clip.write_bytes(b'fake-recording-bytes')

    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        assert run_once(db)

    body = client.delete(f"/api/v1/cases/{case['id']}").json()
    assert body['recordings_preserved'] is True
    assert body['media_files_removed'] == 1
    assert body['bytes_removed'] == len(PNG)
    assert body['media_files_missing'] == 0
    assert '录像资源未做任何改动' in body['message']

    # 只清理该案件的要素文件与本系统内的派生记录
    assert not element_path.exists()
    assert other_path.is_file()
    with Session() as db:
        assert db.get(MediaAsset, media['id']) is None
        assert db.get(AnalysisJob, job['id']) is None
        assert db.query(InspectionReport).count() == 0
    assert client.get('/api/v1/media', params={'case_id': case['id']}).json() == []
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 404
    assert client.delete(f"/api/v1/cases/{case['id']}").status_code == 404

    # 录像文件原样保留
    assert clip.read_bytes() == b'fake-recording-bytes'


def test_delete_case_without_media_is_safe(env):
    client, _, _ = env
    case = _create_case(client)
    body = client.delete(f"/api/v1/cases/{case['id']}").json()
    assert body['media_files_removed'] == 0 and body['bytes_removed'] == 0
    assert client.get('/api/v1/cases').json() == []


def test_case_validation_and_unknown_upload_target(env):
    client, _, root = env
    assert client.post('/api/v1/cases', json={'name': ' '}).status_code == 422
    assert client.post('/api/v1/cases', json={'name': 'x' * 121}).status_code == 422
    assert client.post('/api/v1/cases',
                       json={'name': '案件名称', 'description': 'x' * 501}).status_code == 422
    assert client.delete('/api/v1/cases/not-exist').status_code == 404

    response = client.post('/api/v1/media', files={'file': ('x.png', PNG, 'image/png')},
                           data={'case_id': 'not-exist'})
    assert response.status_code == 422
    assert not list((root / 'storage' / 'originals').glob('*'))
