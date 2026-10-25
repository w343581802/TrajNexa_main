"""M1b overlay text inspection (time and camera-label hints, never camera identity).

PaddleOCR is an OPTIONAL CPU dependency. The ingestion path works without it.
No captured time or device identity is asserted from overlay characters alone.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import unicodedata

import cv2
import numpy as np

from app.config import settings

# Require a full calendar date and a clock: accepting HH:MM alone would invent date.
_DATE_TIME = re.compile(
    r'(?<!\d)((?:19|20)\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})\s*(?:日|T|/)?\s*'
    r'(\d{1,2})[:：](\d{2})[:：](\d{2})(?!\d)'
)
_NAME_PREFIX = re.compile(r'(?i)(?:摄像头|监控点|监控|通道|点位|camera|channel|cam)\s*[:：#_\-]?\s*(.+)')
# Conservative bottom-OSD code hint, not verified camera identity.
_DEVICE_CODE = re.compile(
    r'(?i)^(?:\d{1,4}\s*[-—－]\s*)?[A-Z]{1,6}\s*[-—－]\s*'
    r'[\u4e00-\u9fff\w ]{2,60}\s*[-—－]\s*'
    r'[\u4e00-\u9fffA-Z0-9]{1,20}$'
)
_TIMESTAMP_TOLERANCE_SECONDS = 3.0


@lru_cache(maxsize=1)
def _get_ocr_engine():
    # v3+ API. Import only in the worker process, not in the FastAPI HTTP handler.
    from paddleocr import PaddleOCR
    return PaddleOCR(use_doc_orientation_classify=False,
                     use_doc_unwarping=False,
                     use_textline_orientation=False,
                     engine='paddle')


def _recognize(image: np.ndarray) -> list[dict]:
    """Convert PaddleOCR 3.x predict() result to small JSON-safe text records."""
    engine = _get_ocr_engine()
    rows = []
    for page in engine.predict(image):
        data = getattr(page, 'json', None)
        if callable(data):
            data = data()
        if not isinstance(data, dict):
            continue
        body = data.get('res', data)
        if not isinstance(body, dict):
            continue
        texts = body.get('rec_texts') or []
        scores = body.get('rec_scores') or []
        for value, score in zip(texts, scores):
            s = str(value).strip()
            if s:
                rows.append({'text': s[:160], 'confidence': round(float(score), 4)})
    return rows[:40]


def _read_overlay_strips(image: np.ndarray, recognizer=None) -> list[dict]:
    """Top/bottom strips reduce scene-text noise; mid-screen overlays need later ROI config."""
    recognizer = recognizer or _recognize
    h, w = image.shape[:2]
    if not h or not w:
        return []
    if w > 1920:
        image = cv2.resize(image, (1920, max(1, round(h * 1920 / w))))
        h, w = image.shape[:2]
    band = max(1, int(h * 0.22))
    rows = []
    for label, strip in [('top', image[:band]), ('bottom', image[-band:])]:
        for item in recognizer(strip):
            text = str(item.get('text', '')).strip()[:160]
            try:
                score = float(item.get('confidence', 0))
            except (ValueError, TypeError):
                score = 0.0
            if text and score >= 0.3:
                rows.append({'text': text, 'confidence': round(min(max(score, 0), 1), 4), 'region': label})
    return rows[:60]


def _normalize_time_text(raw: str) -> str:
    """Normalize OCR punctuation only, never guess missing digits."""
    value = unicodedata.normalize('NFKC', raw)
    value = value.replace('–', '-').replace('—', '-').replace('−', '-').replace('∶', ':').replace('，', ' ').replace(',', ' ')
    return re.sub(r'\s*([:/\-.年月日])\s*', r'\1', value)


def _parse_time(rows: list[dict]) -> tuple[str | None, str | None]:
    """Only parse complete valid calendar timestamps; timezone remains unknown."""
    for label in ('top_right', 'top', 'bottom'):
        parts = [x['text'] for x in rows if x['region'] == label]
        for raw in parts + [' '.join(parts)]:
            match = _DATE_TIME.search(_normalize_time_text(raw))
            if not match:
                continue
            try:
                dt = datetime(*map(int, match.groups()))
                return dt.strftime('%Y-%m-%dT%H:%M:%S'), match.group(0)
            except ValueError:
                continue
    return None, None


def _time_fallback_rows(image: np.ndarray, recognizer) -> list[dict]:
    """On time parse failure, retry OCR only for the upper-right OSD area."""
    h, w = image.shape[:2]
    if h < 40 or w < 80:
        return []
    roi = image[:max(24, round(h * 0.13)), round(w * 0.50):]
    if roi.size == 0:
        return []
    if roi.shape[1] > 1000:
        scale = 1000.0 / roi.shape[1]
        roi = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    enlarged = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, high = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)
    modes = [('upscaled', enlarged),
             ('clahe', cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)),
             ('white_text', cv2.cvtColor(high, cv2.COLOR_GRAY2BGR))]
    rows = []
    for mode, frame in modes:
        padded = cv2.copyMakeBorder(frame, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        for item in recognizer(padded):
            raw = str(item.get('text', '')).strip()[:160]
            try:
                confidence = float(item.get('confidence', 0.0))
            except (ValueError, TypeError):
                continue
            if raw and confidence >= 0.3:
                rows.append({'text': raw, 'confidence': round(min(confidence, 1), 4),
                             'region': 'top_right', 'preprocess': mode})
        if _parse_time(rows)[0] is not None:
            break
    return rows[:36]


def _parse_name(rows: list[dict]) -> tuple[str | None, str]:
    for row in rows:
        candidate = _DATE_TIME.sub('', row['text']).strip(' :-_—|')
        m = _NAME_PREFIX.search(candidate)
        if m and m.group(1).strip():
            return m.group(1).strip()[:96], 'labelled_candidate'
    bottom = [row['text'].strip() for row in rows if row['region'] == 'bottom']
    options = bottom + [bottom[i].rstrip() + bottom[i+1].lstrip()
                        for i in range(len(bottom)-1)]
    for raw in options:
        candidate = re.sub(r'\s*[-—－]\s*', '-', raw).strip()
        if _DEVICE_CODE.fullmatch(candidate) and not _DATE_TIME.search(candidate):
            return candidate[:96], 'structured_osd_candidate'
    return None, 'no_camera_name_pattern'


def _sample_offsets(duration_raw) -> list[float]:
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        return [0.0, 1.0, 2.0]
    if not (0 < duration < 60 * 60 * 24):
        return [0.0, 1.0, 2.0]
    return sorted(set(round(max(0.0, min(duration - 0.05, t)), 3) for t in
                      (min(0.5, duration * 0.1), duration / 2, max(0.0, duration - 0.5))))


def _video_frame(path: Path, offset: float) -> np.ndarray | None:
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-ss', str(offset), '-i', str(path),
           '-map', '0:v:0', '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-']
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=12, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout or len(proc.stdout) > 24 * 1024 * 1024:
        return None
    return cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR)


def analyze_overlay(media, path: Path, result: dict, recognizer=None, frame_reader=None) -> None:
    time = result['time']
    camera = result['camera']
    time.update(overlay_time_samples=[], time_status='not_checked', timezone=None,
                metadata_time_is_capture_time=False)
    camera.update(overlay_name_raw=None, camera_candidates=[], camera_uuid=None,
                  match_status='not_checked')
    if not settings.ocr_enabled and recognizer is None:
        time['ocr_engine_status'] = 'disabled'
        camera['ocr_engine_status'] = 'disabled'
        return
    time['ocr_engine_status'] = camera['ocr_engine_status'] = 'pending'
    if result['quality']['decode_status'] in ('failed', 'not_checked'):
        time['time_status'] = 'media_unusable'
        camera['match_status'] = 'media_unusable'
        return
    recognizer = recognizer or _recognize
    sample_frames = []
    if media.kind == 'image':
        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            sample_frames = [(None, image)]
    elif media.kind == 'video':
        duration = result['media']['technical_metadata'].get('duration_seconds')
        reader = frame_reader or _video_frame
        sample_frames = [(offset, reader(path, offset)) for offset in _sample_offsets(duration)]
    successful = []
    for offset, frame in sample_frames:
        if frame is None:
            continue
        try:
            rows = _read_overlay_strips(frame, recognizer)
        except (ImportError, ModuleNotFoundError):
            time['time_status'] = 'ocr_engine_unavailable'
            camera['match_status'] = 'ocr_engine_unavailable'
            time['ocr_engine_status'] = camera['ocr_engine_status'] = 'unavailable'
            return
        except Exception:
            time['time_status'] = 'ocr_engine_error'
            camera['match_status'] = 'ocr_engine_error'
            time['ocr_engine_status'] = camera['ocr_engine_status'] = 'error'
            return
        parsed_time, time_raw = _parse_time(rows)
        name, name_status = _parse_name(rows)
        fallback_attempted = False
        if parsed_time is None and (media.kind == 'image' or not successful):
            fallback_attempted = True
            try:
                extra = _time_fallback_rows(frame, recognizer)
                rows = (rows + extra)[:96]
                parsed_time, time_raw = _parse_time(rows)
            except Exception:
                pass
        successful.append({'offset_seconds': offset, 'time_text': time_raw,
                           'local_time': parsed_time, 'name_candidate': name,
                           'name_status': name_status, 'ocr_rows': rows,
                           'time_roi_fallback_attempted': fallback_attempted})
    time['overlay_time_samples'] = successful
    time['ocr_time_diagnostics'] = {
        'source': 'ocr_text_unverified',
        'parsed_sample_count': sum(bool(s['local_time']) for s in successful),
        'top_raw_texts': [
            {'text': r['text'], 'confidence': r['confidence'],
             'region': r['region'], 'preprocess': r.get('preprocess', 'original')}
            for s in successful for r in s['ocr_rows']
            if r.get('region') in ('top', 'top_right')
        ][:30],
        'roi_fallback_attempted': any(s['time_roi_fallback_attempted'] for s in successful),
    }
    time['ocr_engine_status'] = camera['ocr_engine_status'] = 'ran'
    if not successful:
        time['time_status'] = 'frame_unavailable'
        camera['match_status'] = 'frame_unavailable'
        return
    times = [(s['offset_seconds'], s['local_time']) for s in successful if s['local_time']]
    if not times:
        time['time_status'] = 'unreadable'
    elif len(times) == 1:
        time['time_status'] = 'observed_unverified' if media.kind == 'image' else 'insufficient_samples'
    else:
        # OCR clock delta should follow media time offsets; off-by-one second allowed.
        first_offset, first_time = times[0]
        base = datetime.fromisoformat(first_time)
        consistent = all(abs((datetime.fromisoformat(t) - base).total_seconds() -
                             (float(o) - float(first_offset))) <= _TIMESTAMP_TOLERANCE_SECONDS
                         for o, t in times[1:])
        time['time_status'] = 'consistent_unverified' if consistent else 'inconsistent'
    names = [s['name_candidate'] for s in successful if s['name_candidate']]
    methods = sorted({s['name_status'] for s in successful if s['name_candidate']})
    camera['overlay_candidate_methods'] = methods
    if not names:
        camera['match_status'] = 'no_labelled_name'
    else:
        camera['camera_candidates'] = sorted(set(names))[:10]
        if len(set(names)) == 1:
            camera['overlay_name_raw'] = names[0]
            camera['match_status'] = ('structured_osd_candidate_unbound'
                if methods == ['structured_osd_candidate'] else 'labelled_candidate_unbound')
        else:
            camera['match_status'] = 'conflicting_names'
