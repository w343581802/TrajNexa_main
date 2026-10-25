"""M1b deterministic OCR tests use injected lines; they do NOT measure PaddleOCR accuracy."""
from types import SimpleNamespace
from pathlib import Path

import cv2
import numpy as np
from app.overlay_ocr import (_parse_time, _parse_name, _sample_offsets,
                             analyze_overlay, _recognize)
from app.worker import run_once
from app.config import settings


def _mock_report(kind='image', duration='3.0'):
    return {
        'time': {}, 'camera': {},
        'quality': {'decode_status': 'ok' if kind == 'image' else 'metadata_only'},
        'media': {'technical_metadata': {'duration_seconds': duration}},
    }


def test_time_accepts_complete_valid_calendar_and_split_fields():
    top = [{'text': '2026/10/25', 'region': 'top'},
           {'text': '20:15:03', 'region': 'top'}]
    assert _parse_time(top)[0] == '2026-10-25T20:15:03'
    assert _parse_time([{'text': '2026-02-30 20:15:03', 'region': 'top'}])[0] is None
    assert _parse_time([{'text': '20:15:03', 'region': 'top'}])[0] is None
    assert _parse_time([{'text': '2026年10月25日20:15:03', 'region': 'bottom'}])[0] == '2026-10-25T20:15:03'


def test_camera_only_explicit_label_no_scene_text_promoted():
    assert _parse_name([{'text': '商店招牌花园', 'region': 'top'}])[0] is None
    assert _parse_name([{'text': '摄像头：金水路西口02', 'region': 'top'}])[0] == '金水路西口02'


def test_static_image_time_name_are_unverified(tmp_path):
    image = np.full((60, 100, 3), 120, dtype=np.uint8)
    path = tmp_path / 'sample.png'
    assert cv2.imwrite(str(path), image)
    report = _mock_report()
    media = SimpleNamespace(kind='image')
    def fake_ocr(_strip):
        return [{'text': '摄像头:金水路-02', 'confidence': 0.97},
                {'text': '2026-10-25 20:15:03', 'confidence': 0.95}]
    analyze_overlay(media, path, report, recognizer=fake_ocr)
    assert report['time']['time_status'] == 'observed_unverified'
    assert report['time']['overlay_time_samples'][0]['local_time'] == '2026-10-25T20:15:03'
    assert report['time']['timezone'] is None
    assert report['camera']['overlay_name_raw'] == '金水路-02'
    assert report['camera']['camera_uuid'] is None
    assert report['camera']['match_status'] == 'labelled_candidate_unbound'


def test_video_multiple_frames_continuity_and_clock_conflict(tmp_path):
    report = _mock_report('video', '10')
    media = SimpleNamespace(kind='video')
    frame = np.full((100, 120, 3), 100, dtype=np.uint8)
    frame_reader = lambda p, offset: frame
    values = iter(['2026-10-25 20:15:00', '2026-10-25 20:15:05', '2026-10-25 20:15:09'])
    def fake_ocr(_strip):
        # top first; bottom has no content
        return [{'text': next(values), 'confidence': .99}, {'text': 'Camera:02', 'confidence': .99}]
    # Each sampled frame has 2 strips, so provide repeated timestamp for each pair.
    values = iter([x for t in ['2026-10-25 20:15:00', '2026-10-25 20:15:05',
                               '2026-10-25 20:15:09'] for x in (t, t)])
    analyze_overlay(media, tmp_path/'fake.mp4', report, recognizer=fake_ocr,
                    frame_reader=frame_reader)
    assert report['time']['time_status'] == 'consistent_unverified'
    assert len(report['time']['overlay_time_samples']) == 3
    assert report['camera']['match_status'] == 'labelled_candidate_unbound'
    report = _mock_report('video', '10')
    values = iter([x for t in ['2026-10-25 20:15:00', '2026-10-25 20:15:32',
                               '2026-10-25 20:15:09'] for x in (t, t)])
    analyze_overlay(media, tmp_path/'fake.mp4', report, recognizer=fake_ocr,
                    frame_reader=frame_reader)
    assert report['time']['time_status'] == 'inconsistent'


def test_ocr_missing_and_disabled_do_not_invent_data(tmp_path, monkeypatch):
    media = SimpleNamespace(kind='image')
    path = tmp_path/'x.png'
    cv2.imwrite(str(path), np.full((80, 80, 3), 128, dtype=np.uint8))
    report = _mock_report()
    monkeypatch.setattr(settings, 'ocr_enabled', False)
    analyze_overlay(media, path, report)
    assert report['time']['time_status'] == 'not_checked'
    assert report['time']['ocr_engine_status'] == 'disabled'
    assert report['camera']['camera_uuid'] is None
    report = _mock_report()
    def fail(_):
        raise ModuleNotFoundError('paddleocr')
    analyze_overlay(media, path, report, recognizer=fail)
    assert report['time']['time_status'] == 'ocr_engine_unavailable'
    assert report['camera']['match_status'] == 'ocr_engine_unavailable'


def test_existing_ingest_to_job_preserves_ocr_disabled(env):
    client, Session, _ = env
    img = np.full((90, 110, 3), 70, dtype=np.uint8)
    _, encoded = cv2.imencode('.png', img)
    media = client.post('/api/v1/media', files={'file': ('x.png', encoded.tobytes(), 'image/png')}).json()
    job = client.post(f"/api/v1/media/{media['id']}/inspect").json()
    with Session() as db:
        assert run_once(db)
    report = client.get(f"/api/v1/jobs/{job['id']}/report").json()
    assert report['schema_version'] == '0.1.2'
    assert report['result_json']['time']['ocr_engine_status'] == 'disabled'
    assert report['result_json']['camera']['camera_uuid'] is None
