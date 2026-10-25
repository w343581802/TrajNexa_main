"""No real CCTV media: regression tests with synthetic OCR lines and filenames."""
from app.overlay_ocr import _parse_name
from app.filename_hints import add_filename_hints


def test_unlabelled_bottom_device_code_is_unverified_hint():
    sample = '2-AB-示例路8号-固定'
    assert _parse_name([{'text': sample, 'region': 'bottom'}]) == (sample, 'structured_osd_candidate')
    assert _parse_name([{'text': sample, 'region': 'top'}])[0] is None
    assert _parse_name([{'text': '商店招牌', 'region': 'bottom'}])[0] is None


def test_export_filename_is_not_ocr():
    report = {'time': {'time_status': 'not_checked'}, 'camera': {'match_status': 'not_checked'}}
    add_filename_hints(report, '2-AB-示例路8号-向北_20261023145956至20261023153056_0522.jpg')
    assert report['time']['time_status'] == 'not_checked'
    assert report['time']['filename_time_window_hint']['start_local_unverified'] == '2026-10-23T14:59:56'
    assert report['camera']['filename_name_hint']['source'] == 'filename_unverified'
