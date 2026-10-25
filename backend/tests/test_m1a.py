import shutil
import subprocess
from io import BytesIO

import cv2
import numpy as np
import pytest
from app.worker import run_once


def test_real_image_preflight(env):
    client, Session, _ = env
    rgb = np.full((60, 90, 3), 120, dtype=np.uint8)
    ok, buffer = cv2.imencode('.png', rgb)
    assert ok
    media = client.post('/api/v1/media', files={'file': ('scene.png', buffer.tobytes(), 'image/png')}).json()
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        assert run_once(db)
    data = client.get(f"/api/v1/jobs/{job['id']}/report").json()['result_json']
    assert data['media']['technical_metadata']['width'] == 90
    assert data['media']['technical_metadata']['height'] == 60
    assert data['quality']['decode_status'] == 'ok'
    assert data['quality']['measurements']['median_luminance_0_255'] == 120.0
    assert data['camera']['match_status'] == 'not_checked'
    assert data['time']['time_status'] == 'not_checked'
    assert data['view_check']['status'] == 'not_checked'


def test_corrupt_image_returns_report_not_fake_observation(env):
    client, Session, _ = env
    damaged = b'\x89PNG\r\n\x1a\n' + b'damaged'
    media = client.post('/api/v1/media', files={'file': ('damaged.png', damaged, 'image/png')}).json()
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        assert run_once(db)
    result = client.get(f"/api/v1/jobs/{job['id']}/report").json()['result_json']
    assert result['quality']['decode_status'] == 'failed'
    assert 'image_decode_failed' in result['quality']['issues']


def test_real_video_metadata_probe(env, tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg/FFprobe not installed in this environment')
    output = tmp_path / 'synthetic.mp4'
    subprocess.run([
        'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x48:r=10',
        '-frames:v', '10', '-c:v', 'mpeg4', '-y', str(output),
    ], check=True, timeout=15)
    client, Session, _ = env
    media = client.post('/api/v1/media', files={'file': ('synthetic.mp4', output.read_bytes(), 'video/mp4')}).json()
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        assert run_once(db)
    result = client.get(f"/api/v1/jobs/{job['id']}/report").json()['result_json']
    assert result['quality']['decode_status'] == 'metadata_only'
    assert result['media']['technical_metadata']['streams'][0]['width'] == 64
    assert result['media']['technical_metadata']['streams'][0]['height'] == 48
    assert result['time']['metadata_time_is_capture_time'] is False
