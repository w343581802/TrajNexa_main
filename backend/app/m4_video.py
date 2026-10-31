"""M4c-1: bounded, read-only keyframe observations from a scanned case clip.

No video copies, no new frame extraction pipeline, no asserted exact timestamps,
no cross-frame Tracklet and no GIS positioning. Uses OverlayIdentity's
grab_evidence_frame, which rejects decoder placeholders/blank/broken frames.
"""
from __future__ import annotations

from pathlib import Path
import math

import cv2
import numpy as np

from app import overlay_identity, recordings, m4_detection
from app.config import settings
from app.models import RecordingClip, RecordingSource


class KeyframeError(ValueError):
    """Stable sanitized error code (no absolute host paths)."""


MAX_OFFSETS = 6
MAX_PREVIEW_SECONDS_WITHOUT_DURATION = 60.0
MAX_FRAME_PIXELS = 28_000_000
MAX_FRAME_JPEG_BYTES = 1_400_000


def checked_offset(value: float, clip: RecordingClip) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise KeyframeError('m4c_invalid_offset') from None
    if not math.isfinite(seconds) or seconds < 0.0:
        raise KeyframeError('m4c_invalid_offset')
    duration = clip.duration_seconds
    if duration is None or not math.isfinite(duration) or duration <= 0:
        maximum = MAX_PREVIEW_SECONDS_WITHOUT_DURATION
    else:
        maximum = min(float(duration), 6 * 3600)
    if seconds > maximum:
        raise KeyframeError('m4c_offset_outside_clip')
    return round(seconds, 3)


def checked_offsets(values, clip: RecordingClip):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_OFFSETS:
        raise KeyframeError('m4c_invalid_offsets')
    offsets = [checked_offset(v, clip) for v in values]
    if len(set(offsets)) != len(offsets):
        raise KeyframeError('m4c_duplicate_offsets')
    return offsets


def resolve_case_clip(db, *, case_id: str, clip_id: str):
    clip = db.get(RecordingClip, clip_id)
    if clip is None:
        raise KeyframeError('m4c_clip_not_found')
    source = db.get(RecordingSource, clip.source_id)
    if source is None or source.case_id != case_id:
        # Deliberately do not reveal whether a clip belongs to another case.
        raise KeyframeError('m4c_clip_not_found')
    try:
        folder = recordings.resolve_rel(source.path)
        path = recordings.resolve_rel(clip.relpath)
        if (not path.is_relative_to(folder) or not path.is_file()
                or not recordings.is_video(path) or path.name != clip.file_name):
            raise KeyframeError('m4c_clip_unavailable')
        stat = path.stat()
    except (OSError, recordings.RecordingPathError):
        raise KeyframeError('m4c_clip_unavailable') from None
    if stat.st_size != clip.size_bytes or stat.st_mtime_ns != clip.mtime_ns:
        raise KeyframeError('m4c_clip_changed_rescan_required')
    return clip, path


def ensure_same_recorded_file(path: Path, clip: RecordingClip):
    try:
        st = path.stat()
    except OSError:
        raise KeyframeError('m4c_clip_unavailable') from None
    if st.st_size != clip.size_bytes or st.st_mtime_ns != clip.mtime_ns:
        raise KeyframeError('m4c_clip_changed_rescan_required')


def fetch_frame(path: Path, clip: RecordingClip, offset_seconds: float):
    """Read exactly the *requested* sample offset through shared video pipeline.

    Offset is a requested seek, NOT verified presentation timestamp.
    """
    frame = overlay_identity.grab_evidence_frame(path, offsets=(offset_seconds,))
    if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
        return None
    h, w = frame.shape[:2]
    if not (24 <= h <= 10000 and 24 <= w <= 10000 and h * w <= MAX_FRAME_PIXELS):
        raise KeyframeError('m4c_frame_invalid_dimensions')
    ensure_same_recorded_file(path, clip)
    return frame


def frame_quality(frame: np.ndarray):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    median = float(np.median(gray))
    texture = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    warnings = []
    if median < 24:
        warnings.append('dark_frame')
    if median > 242:
        warnings.append('overexposed_frame')
    if texture < 35:
        warnings.append('blur_or_low_texture')
    return {
        'heuristic_only': True,
        'median_luminance_0_255': round(median, 2),
        'laplacian_variance': round(texture, 2),
        'warnings': warnings,
    }


def preview_keyframes(*, clip: RecordingClip, path: Path, offsets: list[float],
                      include_detection: bool = False, detector=None):
    offsets = checked_offsets(offsets, clip)
    if include_detection and detector is None and not (
        settings.m4b_enabled and settings.m4b_model_sha256
    ):
        raise KeyframeError('m4c_detector_not_configured')
    frames = []
    for offset in offsets:
        frame = fetch_frame(path, clip, offset)
        if frame is None:
            frames.append({
                'requested_offset_seconds': offset,
                'status': 'frame_unavailable',
                'width': None, 'height': None, 'quality': None, 'detections': None,
            })
            continue
        height, width = frame.shape[:2]
        item = {
            'requested_offset_seconds': offset,
            'status': 'frame_available',
            'width': width, 'height': height,
            'quality': frame_quality(frame), 'detections': None,
        }
        if include_detection:
            if detector is None:
                detections = m4_detection.detect_image(
                    frame,
                    model_path=settings.m4b_model_path,
                    model_sha256=settings.m4b_model_sha256,
                    source_sha256='video_clip_not_sha256_verified',
                )
            else:
                detections = detector(frame, offset)
            item['detections'] = {
                'model_family': detections['model_family'],
                'model_sha256': detections.get('model_sha256'),
                'status': 'candidates_unverified',
                'candidates': detections['candidates'],
            }
        frames.append(item)
    ensure_same_recorded_file(path, clip)
    return {
        'schema_version': '1.0',
        'status': 'sampled_keyframes_unverified',
        'clip_id': clip.id,
        'source': 'registered_recording_readonly',
        'source_file_fingerprint': {
            'size_bytes': clip.size_bytes, 'mtime_ns': clip.mtime_ns,
            'content_sha256_verified': False,
        },
        'start_time_candidate': clip.start_local,
        'start_time_source': clip.start_source,
        'absolute_frame_time_verified': False,
        'camera_uuid_candidate': clip.camera_uuid,
        'camera_match_status': clip.match_status,
        'camera_verified_for_observation': False,
        'tracklet_status': 'not_established',
        'frame_time_semantics': 'requested_seek_offset_not_verified_pts',
        'frames': frames,
    }


def jpeg_preview(frame: np.ndarray) -> bytes:
    height, width = frame.shape[:2]
    if max(width, height) > 1280:
        factor = 1280 / max(width, height)
        frame = cv2.resize(frame, (int(width * factor), int(height * factor)),
                           interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if not ok or len(jpeg) > MAX_FRAME_JPEG_BYTES:
        raise KeyframeError('m4c_preview_image_too_large')
    return jpeg.tobytes()
