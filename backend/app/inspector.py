"""M1b: conservative media introspection with optional overlay OCR; no identity inference.

Reports raw measurements and explicit unknowns. Heuristics do NOT imply image
forensic validity, camera authenticity, or an actual capture timestamp.
"""
from __future__ import annotations

from pathlib import Path
from app.overlay_ocr import analyze_overlay
from app.camera_catalog import annotate_camera_report
from app.filename_hints import add_filename_hints, compare_ocr_with_filename
import json
import subprocess


def _default_report(media) -> dict:
    return {
        'schema_version': '0.1.3',
        'processing_stage': 'm1c_metadata_ocr_and_directory_candidates',
        'media': {
            'media_id': media.id, 'kind': media.kind, 'sha256': media.sha256,
            'size_bytes': media.size_bytes, 'mime_detected': media.mime_detected,
            'technical_metadata': {},
        },
        'time': {
            'overlay_time_samples': [], 'timezone': None,
            'time_status': 'not_checked', 'metadata_time_is_capture_time': False,
        },
        'camera': {
            'overlay_name_raw': None, 'camera_candidates': [],
            'camera_uuid': None, 'match_status': 'not_checked',
        },
        'quality': {
            'overall_status': 'unknown', 'issues': [],
            'decode_status': 'not_checked', 'measurements': {},
        },
        'view_check': {'status': 'not_checked', 'spatial_revision': None},
        'review_state': 'pending',
    }


def _image_metadata(path: Path, result: dict) -> None:
    import cv2
    import numpy as np

    # imdecode handles UTF-8 filesystem names on platforms where imread may not.
    buffer = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    quality = result['quality']
    if image is None:
        quality.update(decode_status='failed', overall_status='unusable')
        quality['issues'].append('image_decode_failed')
        return
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    median = float(np.median(gray))
    # Not a calibrated sharpness score; depends strongly on scene texture.
    laplacian_variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    quality['measurements'] = {
        'median_luminance_0_255': round(median, 2),
        'laplacian_variance': round(laplacian_variance, 2),
        'measurements_are_heuristic': True,
    }
    result['media']['technical_metadata'] = {'width': width, 'height': height, 'channels': 3}
    quality['decode_status'] = 'ok'
    if median < 24:
        quality['issues'].append('low_brightness_candidate')
    elif median > 242:
        quality['issues'].append('high_brightness_candidate')
    if laplacian_variance < 35:
        quality['issues'].append('low_texture_or_blur_candidate')
    quality['overall_status'] = 'needs_review' if quality['issues'] else 'preliminarily_usable'


def _video_metadata(path: Path, result: dict) -> None:
    quality = result['quality']
    args = [
        'ffprobe', '-v', 'error', '-of', 'json',
        '-show_entries',
        'format=format_name,duration,size,start_time:format_tags=creation_time:'
        'stream=index,codec_type,codec_name,width,height,avg_frame_rate,duration,nb_frames',
        str(path),
    ]
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=12, check=False)
    except FileNotFoundError:
        quality.update(decode_status='not_checked', overall_status='unknown')
        quality['issues'].append('ffprobe_unavailable')
        return
    except subprocess.TimeoutExpired:
        quality.update(decode_status='failed', overall_status='unusable')
        quality['issues'].append('ffprobe_timeout')
        return
    if proc.returncode != 0:
        quality.update(decode_status='failed', overall_status='unusable')
        quality['issues'].append('video_probe_failed')
        return
    try:
        metadata = json.loads(proc.stdout)
    except (ValueError, TypeError):
        quality.update(decode_status='failed', overall_status='unusable')
        quality['issues'].append('video_probe_invalid_json')
        return
    streams = metadata.get('streams', [])
    fmt = metadata.get('format', {})
    video = [s for s in streams if s.get('codec_type') == 'video']
    if not video:
        quality.update(decode_status='failed', overall_status='unusable')
        quality['issues'].append('video_stream_missing')
        return
    # Technical metadata only; ffprobe success does not establish that all frames decode.
    result['media']['technical_metadata'] = {
        'format': fmt.get('format_name'),
        'duration_seconds': fmt.get('duration'),
        'streams': streams[:16],
        'container_creation_time': (fmt.get('tags') or {}).get('creation_time'),
    }
    quality.update(decode_status='metadata_only', overall_status='unknown')
    quality['issues'].append('frame_decode_not_checked')


def inspect_media(media, path: Path) -> dict:
    result = _default_report(media)
    try:
        if media.kind == 'image':
            _image_metadata(path, result)
        elif media.kind == 'video':
            _video_metadata(path, result)
        else:
            result['quality']['issues'].append('unsupported_media_kind')
    except Exception:
        # Preserve a report with explicit failure instead of fabricated observations.
        result['quality'].update(decode_status='failed', overall_status='unusable')
        result['quality']['issues'].append('unexpected_decode_error')
    try:
        analyze_overlay(media, path, result)
    except Exception:
        result['time']['time_status'] = 'ocr_pipeline_error'
        result['camera']['match_status'] = 'ocr_pipeline_error'
    add_filename_hints(result, getattr(media, 'original_name', None))
    compare_ocr_with_filename(result)
    annotate_camera_report(result)
    return result
