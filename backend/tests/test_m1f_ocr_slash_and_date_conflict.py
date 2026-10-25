"""M1 safe OCR date-source comparison. Does not test actual PaddleOCR model."""
from app.filename_hints import add_filename_hints, compare_ocr_with_filename
from app.overlay_ocr import _parse_time


def _report(raw, filename):
    report = {'time': {'time_status': 'observed_unverified',
                       'overlay_time_samples': [{'local_time': _parse_time(
                           [{'text': raw, 'region': 'top'}])[0],
                           'time_text': raw}]},
              'camera': {}}
    add_filename_hints(report, filename)
    compare_ocr_with_filename(report)
    return report


def test_slash_separator_recognized_but_missing_day_stays_unverified():
    ocr = '2026-10-3/14:59:59'
    assert _parse_time([{'text': ocr, 'region': 'top'}])[0] == '2026-10-03T14:59:59'
    report = _report(ocr, 'CAM_20261023145956至20261023153056_clip.jpg')
    result = report['time']
    assert result['time_status'] == 'source_conflict_needs_review'
    assert result['overlay_time_samples'][0]['time_text'] == ocr
    assert result['filename_cross_check']['status'] == 'outside_window'
    possible = result['filename_cross_check']['possible_date_repair_for_review']
    assert possible['candidate_local_unverified'] == '2026-10-23T14:59:59'
    assert possible['requires_human_confirmation']


def test_source_agreement_remains_unverified():
    report = _report('2026-10-23 14:59:59',
                     'CAM_20261023145956至20261023153056_clip.jpg')
    assert report['time']['time_status'] == 'observed_unverified'
    assert report['time']['filename_cross_check']['status'] == 'within_window'
    assert 'possible_date_repair_for_review' not in report['time']['filename_cross_check']


def test_conflicting_filename_cannot_rewrite_original_ocr_time():
    report = _report('2026-10-23 14:59:59',
                     'CAM_20261101100000至20261101102000_clip.jpg')
    assert report['time']['time_status'] == 'source_conflict_needs_review'
    assert report['time']['overlay_time_samples'][0]['local_time'] == '2026-10-23T14:59:59'
    assert 'possible_date_repair_for_review' not in report['time']['filename_cross_check']


def test_bad_calendar_day_rejected():
    assert _parse_time([{'text': '2026-10-32/14:59:59', 'region': 'top'}])[0] is None
