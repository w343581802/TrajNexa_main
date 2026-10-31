"""M4a: measurable, non-biometric evidence from a human-selected screenshot ROI.

No person identification, face matching, identity embeddings, or automatic
cross-camera association. Colour is a scene-level hint, not clothing truth.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import math

import cv2
import numpy as np


class FeatureInputError(ValueError):
    """Safe, stable error code; never includes a private file path."""


VERSION = 'm4a_roi_heuristics_v1'
MAX_PIXELS = 28_000_000
TONE_NAMES = ('dark', 'gray', 'light', 'red', 'orange', 'yellow',
              'green', 'cyan', 'blue', 'purple')


def _tones(crop: np.ndarray) -> list[dict]:
    """Coarse ROI colour distribution only. Background can dominate the crop."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Deterministic small sample; avoid spending CPU on huge screenshots.
    sample = hsv[::max(1, hsv.shape[0] // 250), ::max(1, hsv.shape[1] // 250)]
    h, s, v = [sample[:, :, i] for i in range(3)]
    bucket = np.zeros(h.shape, np.uint8)
    bucket[(s < 35) & (v >= 65)] = 1
    bucket[(s < 35) & (v >= 185)] = 2
    chromatic = (s >= 35) & (v >= 45)
    bucket[chromatic & ((h < 9) | (h >= 170))] = 3
    bucket[chromatic & (h >= 9) & (h < 22)] = 4
    bucket[chromatic & (h >= 22) & (h < 36)] = 5
    bucket[chromatic & (h >= 36) & (h < 81)] = 6
    bucket[chromatic & (h >= 81) & (h < 101)] = 7
    bucket[chromatic & (h >= 101) & (h < 135)] = 8
    bucket[chromatic & (h >= 135) & (h < 170)] = 9
    counts = np.bincount(bucket.ravel(), minlength=len(TONE_NAMES))
    total = int(counts.sum())
    ordered = sorted(range(len(TONE_NAMES)), key=lambda i: (-int(counts[i]), i))
    return [{'tone': TONE_NAMES[i], 'fraction': round(int(counts[i]) / total, 3)}
            for i in ordered[:3] if counts[i] / total >= 0.06]


def analyze_image_roi(path: Path, expected_sha256: str, box: list[float]) -> dict:
    """Inspect existing source *read-only* and reject changed/corrupt originals."""
    if len(box) != 4 or any(not isinstance(v, (int, float)) or
                            isinstance(v, bool) or not math.isfinite(v) for v in box):
        raise FeatureInputError('m4_invalid_bbox')
    x1, y1, x2, y2 = [float(v) for v in box]
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise FeatureInputError('m4_invalid_bbox')
    try:
        raw = path.read_bytes()
    except OSError:
        raise FeatureInputError('m4_media_missing') from None
    if sha256(raw).hexdigest() != expected_sha256:
        raise FeatureInputError('m4_source_hash_mismatch')
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise FeatureInputError('m4_decode_failed')
    height, width = image.shape[:2]
    if width * height > MAX_PIXELS:
        raise FeatureInputError('m4_image_too_large')
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = math.ceil(x2 * width), math.ceil(y2 * height)
    roi = image[top:bottom, left:right]
    if roi.shape[0] < 24 or roi.shape[1] < 24:
        raise FeatureInputError('m4_roi_too_small')
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    median = float(np.median(gray))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    saturation = float(np.median(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)[:, :, 1]))
    flags: list[str] = []
    if median < 24:
        flags.append('dark_roi')
    if median > 242:
        flags.append('overexposed_roi')
    if sharpness < 35:
        flags.append('blur_or_low_texture')
    if saturation < 25:
        flags.append('color_unreliable_grayscale_or_ir')
    if (right - left) * (bottom - top) < width * height * 0.005:
        flags.append('small_fraction_of_frame')
    return {
        'schema_version': '1.0',
        'method': VERSION,
        'source_kind': 'uploaded_image',
        'bbox_normalized': [x1, y1, x2, y2],
        'bbox_pixels': [left, top, right, bottom],
        'frame_size': [width, height],
        'source_sha256': expected_sha256,
        'quality': {
            'median_luminance_0_255': round(median, 2),
            'laplacian_variance': round(sharpness, 2),
            'median_saturation_0_255': round(saturation, 2),
            'heuristic_only': True,
            'flags': flags,
        },
        'appearance': {
            'roi_tones_unverified': _tones(roi),
            'semantic_attributes': {},
            'semantic_status': 'not_analyzed',
            'warning': 'ROI includes background; tones are not verified clothing or identity.',
        },
        'linkage': {
            'identity_status': 'not_established',
            'geo_status': 'unlocated',
            'camera_uuid': None,
            'track_segment': None,
        },
    }
