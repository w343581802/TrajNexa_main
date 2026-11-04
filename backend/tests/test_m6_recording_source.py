"""B8-B: decode a real time window from a registered clip file."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from app import m6_recording_source


def _video(path: Path, *, seconds=3.0, fps=10.0):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), fps,
                             (160, 120))
    total = int(seconds * fps)
    for i in range(total):
        frame = np.full((120, 160, 3), (i * 7) % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert path.exists() and path.stat().st_size > 0


def test_extracts_requested_window(tmp_path):
    video = tmp_path / 'clip.mp4'
    _video(video)
    out = tmp_path / 'frames'
    result = m6_recording_source.extract_window_frames(video, 0.5, 1.5, 5.0, out)
    assert result['status'] == 'ok'
    assert result['frames'] >= 3
    assert len(list(out.glob('*.jpg'))) == result['frames']
    assert result['requested_start_seconds'] == 0.5
    assert result['seek_drift_seconds'] is not None


def test_window_outside_recording_is_explicit(tmp_path):
    video = tmp_path / 'clip.mp4'
    _video(video)
    with pytest.raises(m6_recording_source.RecordingFrameError) as exc:
        m6_recording_source.extract_window_frames(video, 100.0, 106.0, 5.0,
                                                  tmp_path / 'frames')
    assert exc.value.code == 'no_frames_in_window'


def test_seconds_of_day_parses_osd_formats():
    assert m6_recording_source.seconds_of_day('2026-10-23 15:00:00') == 54000.0
    assert m6_recording_source.seconds_of_day('15:00:00') == 54000.0
    assert m6_recording_source.seconds_of_day(None) is None
    assert m6_recording_source.seconds_of_day('not a time') is None
