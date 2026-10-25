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
