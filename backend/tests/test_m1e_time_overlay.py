"""M1b time OCR fallback regression. Mock recognition; not measured accuracy."""
from types import SimpleNamespace
import cv2
import numpy as np
from app.overlay_ocr import _parse_time, _time_fallback_rows, analyze_overlay
from app.filename_hints import add_filename_hints


def report():
    return {'time': {}, 'camera': {}, 'quality': {'decode_status': 'ok'},
            'media': {'technical_metadata': {}}}


def test_formats_validate_not_guess_digits():
    examples = [
        ('２０２６－１０－２３ １４：５９：５９', '2026-10-23T14:59:59'),
        ('2026 / 10 / 23 14 : 59 : 59', '2026-10-23T14:59:59'),
        ('2026年10月23日14：59：59', '2026-10-23T14:59:59'),
        ('2026-10-23 14:59', None),
        ('2026-02-30 14:59:59', None),
        ('2026-10-2B 14:5?:59', None),
    ]
    for raw, expected in examples:
        assert _parse_time([{'text': raw, 'region': 'top_right'}])[0] == expected


def test_upper_right_fallback_avoids_extra_calls_after_timestamp():
    image = np.full((920, 1640, 3), 105, dtype=np.uint8)
    calls = []
    def fake(frame):
        calls.append(frame.shape)
        return [{'text': '2026-10-23' if len(calls) == 1 else
                 '2026-10-23 14:59:59', 'confidence': .9}]
    rows = _time_fallback_rows(image, fake)
    assert len(calls) == 2
    assert rows[0]['region'] == 'top_right'
    assert _parse_time(rows)[0] == '2026-10-23T14:59:59'


def test_cctv_name_and_time_recovery_stay_unbound(tmp_path):
    path = tmp_path / 'synthetic.png'
    cv2.imwrite(str(path), np.full((480, 800, 3), 100, dtype=np.uint8))
    calls = 0
    def fake(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return [{'text': '2026-10-23', 'confidence': .96}]
        if calls == 2:
            return [{'text': '3-DX-中仁路98号-球', 'confidence': .99}]
        return [{'text': '2026-10-23 14:59:59', 'confidence': .9}]
    data = report()
    analyze_overlay(SimpleNamespace(kind='image'), path, data, recognizer=fake)
    assert data['time']['time_status'] == 'observed_unverified'
    assert data['time']['ocr_time_diagnostics']['roi_fallback_attempted']
    assert data['camera']['overlay_name_raw'] == '3-DX-中仁路98号-球'
    assert data['camera']['camera_uuid'] is None


def test_unreadable_retains_ocr_and_separate_filename_hint(tmp_path):
    path = tmp_path / 'synthetic.png'
    cv2.imwrite(str(path), np.full((120, 200, 3), 100, dtype=np.uint8))
    data = report()
    analyze_overlay(SimpleNamespace(kind='image'), path, data,
                    recognizer=lambda _: [{'text': '2026-10-2B 14:5?:59', 'confidence': .8}])
    add_filename_hints(data, '3-DX-中仁路98号-向北_20261023145956至20261023153056_x.jpg')
    assert data['time']['time_status'] == 'unreadable'
    assert data['time']['ocr_time_diagnostics']['top_raw_texts']
    assert data['time']['filename_time_window_hint']['source'] == 'filename_unverified'
