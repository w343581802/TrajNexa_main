import hashlib
from io import BytesIO
from app.worker import run_once
from app.models import AnalysisJob, MediaAsset

PNG = b'\x89PNG\r\n\x1a\n' + b'test-demo-content'   # magic-only M0 fixture; M1 will decode fully

def test_health_upload_job_report(env):
    client, Session, root = env
    assert client.get('/api/v1/health').json()['status'] == 'ok'
    response = client.post('/api/v1/media', files={'file': ('camera.png', PNG, 'image/png')})
    assert response.status_code == 201, response.text
    media = response.json()
    assert media['sha256'] == hashlib.sha256(PNG).hexdigest()
    assert media['kind'] == 'image'
    assert len(client.get('/api/v1/media').json()) == 1
    job_data = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    assert job_data['status'] == 'queued'
    with Session() as db:
        assert run_once(db)
        assert not run_once(db)
    job = client.get(f"/api/v1/jobs/{job_data['id']}").json()
    assert job['status'] == 'succeeded' and job['attempt'] == 1
    report = client.get(f"/api/v1/jobs/{job['id']}/report").json()
    assert report['result_json']['time']['time_status'] == 'not_checked'
    assert report['result_json']['view_check']['status'] == 'not_checked'
    assert client.get(f"/api/v1/inspections/{report['id']}").status_code == 200
    assert client.post(f"/api/v1/jobs/{job['id']}/retry").status_code == 409

def test_reject_unknown_and_empty_and_large_upload(env, monkeypatch):
    client, _, root = env
    assert client.post('/api/v1/media', files={'file': ('x.txt', b'hello', 'image/png')}).status_code == 415
    assert client.post('/api/v1/media', files={'file': ('empty.png', b'', 'image/png')}).status_code == 415
    from app.config import settings
    monkeypatch.setattr(settings, 'max_upload_bytes', 5)
    assert client.post('/api/v1/media', files={'file': ('big.png', PNG, 'image/png')}).status_code == 413
    assert not list((root / 'storage' / 'originals').glob('*'))

def test_failed_job_retry_and_upload_filename_sanitization(env):
    client, Session, root = env
    media = client.post('/api/v1/media', files={'file': ('../../evil.png', PNG, 'image/png')}).json()
    assert media['original_name'] == 'evil.png'
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        m = db.get(MediaAsset, media['id'])
        (root / 'storage' / m.storage_relpath).unlink()
        assert run_once(db)
    failed = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert failed['status'] == 'failed'
    assert client.post(f"/api/v1/jobs/{job['id']}/retry").json()['status'] == 'queued'
    with Session() as db:
        assert run_once(db)
        assert db.get(AnalysisJob, job['id']).attempt == 2


def test_get_not_found(env):
    client, _, _ = env
    assert client.get('/api/v1/media/nonexistent').status_code == 404
    assert client.get('/api/v1/jobs/nonexistent').status_code == 404
    assert client.get('/api/v1/jobs/nonexistent/report').status_code == 404

def test_video_stub_and_multiple_reports_preserved(env):
    client, Session, _ = env
    mp4 = b'\x00\x00\x00\x18ftypisom' + b'tiny-synthetic-video-signature'
    media = client.post('/api/v1/media', files={'file': ('test.mp4', mp4, 'text/plain')}).json()
    assert media['kind'] == 'video'
    jobs = [client.post(f"/api/v1/media/{media['id']}/inspect").json()['id'] for _ in range(2)]
    with Session() as db:
        assert run_once(db)
        assert run_once(db)
    reports = [client.get(f'/api/v1/jobs/{j}/report').json() for j in jobs]
    assert reports[0]['id'] != reports[1]['id']
    assert reports[0]['result_json']['quality']['decode_status'] == 'failed'


def test_interrupted_job_recovered(env):
    client, Session, _ = env
    from app.worker import recover_interrupted_jobs
    media = client.post('/api/v1/media', files={'file': ('x.png', PNG, 'image/png')}).json()
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        obj = db.get(AnalysisJob, job['id'])
        obj.status = 'running'
        db.commit()
        recover_interrupted_jobs(db)
        assert db.get(AnalysisJob, job['id']).status == 'queued'
        assert run_once(db)
    assert client.get(f"/api/v1/jobs/{job['id']}").json()['status'] == 'succeeded'
