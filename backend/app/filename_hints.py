"""Filename-extracted hints are distinct from image pixels and never verified capture facts."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import re

_WINDOW = re.compile(r'^(?P<label>[^_]{2,120})_(?P<start>\d{14})至(?P<end>\d{14})(?:_|\.|$)')


def add_filename_hints(report: dict, original_name: str | None) -> None:
    name = Path(original_name or '').name
    m = _WINDOW.match(name)
    if not m:
        return
    try:
        start = datetime.strptime(m['start'], '%Y%m%d%H%M%S')
        end = datetime.strptime(m['end'], '%Y%m%d%H%M%S')
    except ValueError:
        return
    if start > end:
        return
    report['time']['filename_time_window_hint'] = {
        'start_local_unverified': start.isoformat(),
        'end_local_unverified': end.isoformat(),
        'timezone': None,
        'source': 'filename_unverified',
    }
    report['camera']['filename_name_hint'] = {
        'text': m['label'], 'source': 'filename_unverified', 'camera_uuid': None,
    }


def compare_ocr_with_filename(report: dict) -> None:
    """Independent source consistency check; NEVER substitute filename for OCR."""
    t = report.get('time', {})
    hint = t.get('filename_time_window_hint') or {}
    if hint.get('source') != 'filename_unverified':
        return
    try:
        start = datetime.fromisoformat(hint['start_local_unverified'])
        end = datetime.fromisoformat(hint['end_local_unverified'])
        if start > end or start.tzinfo or end.tzinfo:
            return
    except (KeyError, TypeError, ValueError):
        return
    samples = [x for x in t.get('overlay_time_samples', []) if x.get('local_time')]
    if not samples:
        t['filename_cross_check'] = {'status': 'ocr_time_unavailable', 'filename_verified': False}
        return
    conflicts = []
    for sample in samples:
        try:
            ocr_dt = datetime.fromisoformat(sample['local_time'])
        except (TypeError, ValueError):
            continue
        if not start <= ocr_dt <= end:
            conflicts.append(sample)
    cross = {'status': 'outside_window' if conflicts else 'within_window',
             'source': 'filename_unverified', 'filename_verified': False,
             'camera_clock_verified': False}
    if conflicts:
        # A one-digit day was possibly lost in OCR. This is only a
        # human-review hint; the source pixel OCR remains unchanged.
        raw = conflicts[0].get('time_text') or ''
        pattern = re.search(
            r'(?<!\d)((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})[/ T](\d{1,2}):(\d{2}):(\d{2})(?!\d)',
            raw)
        if pattern and start.date() == end.date():
            year, month, day, hour, minute, second = pattern.groups()
            if (len(day) == 1 and len(str(start.day)) == 2 and
                str(start.day).endswith(day) and
                (int(year), int(month)) == (start.year, start.month)):
                try:
                    possible = datetime(start.year, start.month, start.day,
                                        int(hour), int(minute), int(second))
                    if start <= possible <= end:
                        cross['possible_date_repair_for_review'] = {
                            'candidate_local_unverified': possible.isoformat(),
                            'reason': 'possible_missing_day_digit_in_ocr',
                            'requires_human_confirmation': True,
                            'sources': ['ocr_text_unverified', 'filename_unverified']}
                except ValueError:
                    pass
        t['time_status'] = 'source_conflict_needs_review'
    t['filename_cross_check'] = cross
