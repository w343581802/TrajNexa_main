"""受哈希校验的图片解码（原 m4_detection 中的工具，D-FINE 移除后独立）。"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np

MAX_PIXELS = 40_000_000


class MediaImageError(ValueError):
    """稳定错误码；不泄露路径或凭据。"""


def verified_image(path: Path, expected_hash: str) -> np.ndarray:
    try:
        content = path.read_bytes()
    except OSError:
        raise MediaImageError("media_source_unavailable") from None
    if sha256(content).hexdigest() != expected_hash:
        raise MediaImageError("media_source_hash_mismatch")
    img = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise MediaImageError("media_source_decode_failed")
    if img.shape[0] * img.shape[1] > MAX_PIXELS:
        raise MediaImageError("media_source_image_too_large")
    return img
