"""M1d conservative static-background reference comparison, not geographic orientation.

OpenCV ORB + RANSAC tests *visual correspondences* between reference/current frame.
Missing matches never prove a direction change. An image homography never yields an
absolute geographic heading; PTZ, illumination, construction, parallax and occlusion
remain unknown in this demo.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import math

import cv2
import numpy as np

from app.config import settings
from app.overlay_ocr import _video_frame, _sample_offsets

MAX_IMAGE_PIXELS = 28_000_000
MAX_WIDTH = 1000


class ViewInputError(ValueError):
    pass


def media_path(media) -> Path:
    root = settings.storage_root.resolve()
    file = (root / media.storage_relpath).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise ViewInputError('media_file_unavailable')
    hasher = hashlib.sha256()
    with file.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(data)
    if hasher.hexdigest() != media.sha256:
        raise ViewInputError('media_hash_mismatch')
    return file


def _decode_image(path: Path) -> np.ndarray | None:
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except (OSError, ValueError, cv2.error):
        return None


def _prepare(image: np.ndarray | None) -> np.ndarray | None:
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        return None
    h, w = image.shape[:2]
    if min(w, h) < 64 or w * h > MAX_IMAGE_PIXELS:
        return None
    if max(w, h) > MAX_WIDTH:
        scale = MAX_WIDTH / max(w, h)
        image = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # Mask typical top/bottom timestamp and camera labels. This is not evidence editing.
    excluded = max(2, round(gray.shape[0] * 0.12))
    gray[:excluded] = 0
    gray[-excluded:] = 0
    return gray


def compare_static_scene(reference: np.ndarray | None, current: np.ndarray | None) -> dict:
    """Feature geometry only. Outputs explainable diagnostics and conservative status."""
    outcome = {
        'status': 'indeterminate',
        'reason': 'insufficient_static_feature_matches',
        'algorithm': 'orb_bf_ratio_ransac_v1',
        'measurements': {'reference_keypoints': 0, 'current_keypoints': 0,
                         'ratio_matches': 0, 'ransac_inliers': 0,
                         'inlier_ratio': None, 'median_displacement_fraction': None},
        'is_absolute_heading_validation': False,
    }
    reference = _prepare(reference)
    current = _prepare(current)
    if reference is None or current is None:
        outcome['reason'] = 'image_unavailable_or_invalid'
        return outcome
    # Compare geometry in the same pixel frame; do not mistake resolution
    # differences for physical camera motion. Aspect changes are ambiguous.
    ra = reference.shape[1] / reference.shape[0]
    rb = current.shape[1] / current.shape[0]
    if abs(ra / rb - 1) > 0.025:
        outcome['reason'] = 'aspect_ratio_mismatch'
        return outcome
    if current.shape != reference.shape:
        current = cv2.resize(current, (reference.shape[1], reference.shape[0]), interpolation=cv2.INTER_AREA)
    orb = cv2.ORB_create(nfeatures=1800, fastThreshold=12)
    kp_a, des_a = orb.detectAndCompute(reference, None)
    kp_b, des_b = orb.detectAndCompute(current, None)
    m = outcome['measurements']
    m['reference_keypoints'], m['current_keypoints'] = len(kp_a), len(kp_b)
    if des_a is None or des_b is None or len(kp_a) < 30 or len(kp_b) < 30:
        outcome['reason'] = 'too_few_background_features'
        return outcome
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(des_a, des_b, k=2)
    good = [a for pair in pairs if len(pair) == 2 for a, b in [pair] if a.distance < 0.72 * b.distance]
    m['ratio_matches'] = len(good)
    if len(good) < 16:
        return outcome
    src = np.float32([kp_a[x.queryIdx].pt for x in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_b[x.trainIdx].pt for x in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0, maxIters=1500)
    if H is None or mask is None or not np.isfinite(H).all():
        outcome['reason'] = 'homography_unavailable'
        return outcome
    keep = mask.ravel().astype(bool)
    inliers = int(keep.sum())
    ratio = inliers / len(good)
    m['ransac_inliers'], m['inlier_ratio'] = inliers, round(ratio, 4)
    if inliers < 14 or ratio < 0.50:
        outcome['reason'] = 'weak_geometric_consensus'
        return outcome
    # Avoid declaring consistency from a tiny repeated logo/corner pattern.
    accepted = src.reshape(-1, 2)[keep]
    x_spread = float(np.ptp(accepted[:, 0]) / reference.shape[1])
    y_spread = float(np.ptp(accepted[:, 1]) / reference.shape[0])
    m['reference_inlier_spread_xy'] = [round(x_spread, 4), round(y_spread, 4)]
    if x_spread < 0.22 or y_spread < 0.18:
        outcome['reason'] = 'insufficient_spatial_coverage'
        return outcome
    # Measure observed pixel correspondence shift; not a compass heading.
    displacement = np.linalg.norm(dst.reshape(-1, 2)[keep] - accepted, axis=1)
    diag = math.hypot(*reference.shape[:2])
    median_frac = float(np.median(displacement)) / max(diag, 1)
    m['median_displacement_fraction'] = round(median_frac, 4)
    if median_frac > 0.09:
        outcome.update(status='possible_view_change', reason='large_reference_feature_displacement')
    else:
        outcome.update(status='reference_view_consistent_candidate', reason='static_scene_geometry_corresponds')
    return outcome


def check_reference_view(reference_media, observed_media, inspected_report: dict) -> dict:
    result = {
        'status': 'indeterminate', 'reason': 'frame_unavailable',
        'reference_media_id': reference_media.id,
        'reference_sha256': reference_media.sha256,
        'observed_media_id': observed_media.id,
        'observed_sha256': observed_media.sha256,
        'frame_offsets_seconds': [],
        'is_absolute_heading_validation': False,
    }
    reference = _decode_image(media_path(reference_media))
    if reference is None:
        result['reason'] = 'reference_image_decode_failed'
        return result
    path = media_path(observed_media)
    if observed_media.kind == 'image':
        frames = [(None, _decode_image(path))]
    else:
        duration = (inspected_report.get('media') or {}).get('technical_metadata', {}).get('duration_seconds')
        offsets = _sample_offsets(duration)
        frames = [(float(t), _video_frame(path, float(t))) for t in offsets]
    checks = []
    for offset, frame in frames:
        if frame is None:
            continue
        sample = compare_static_scene(reference, frame)
        sample['offset_seconds'] = offset
        checks.append(sample)
    result['frame_offsets_seconds'] = [c['offset_seconds'] for c in checks]
    result['frame_results'] = checks
    if not checks:
        return result
    statuses = {c['status'] for c in checks}
    if 'possible_view_change' in statuses:
        result.update(status='possible_view_change', reason='one_or_more_frames_indicate_shift')
    elif 'reference_view_consistent_candidate' in statuses:
        result.update(status='reference_view_consistent_candidate', reason='reference_correspondences_found')
    else:
        result.update(status='indeterminate', reason='no_reliable_static_scene_correspondence')
    return result
