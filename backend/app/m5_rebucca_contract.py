"""M5: normalize Rebucca EngineFactory detection output without inventing identity.

Rebucca BaseEngine.detect(frame_bgr) returns dictionaries containing pixel-space
box (xyxy), label, score, and sometimes mask_polygon. This module performs only
source-aware coordinate normalization; it neither runs models nor persists facts.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math


class RebuccaContractError(ValueError):
    pass


MAX_PER_MODEL = 300
MAX_POLYGON_POINTS = 256


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def normalize_detections(
    detections: Sequence[Mapping],
    *,
    width: int,
    height: int,
    model_id: str,
    model_name: str,
    class_map: Mapping[str, str] | None = None,
) -> list[dict]:
    """Return unverified model-specific candidates; never fuse model outputs.

    Coordinates are always from Rebucca's ORIGINAL BGR frame, not the model's
    resized tensor. This matches its EngineFactory BaseEngine detection contract.
    """
    if not isinstance(width, int) or isinstance(width, bool) or not 0 < width <= 20000:
        raise RebuccaContractError("m5_invalid_frame_size")
    if not isinstance(height, int) or isinstance(height, bool) or not 0 < height <= 20000:
        raise RebuccaContractError("m5_invalid_frame_size")
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 128:
        raise RebuccaContractError("m5_model_id_required")
    if not isinstance(detections, (list, tuple)) or len(detections) > 5000:
        raise RebuccaContractError("m5_invalid_detections")
    class_map = class_map or {}
    if not isinstance(class_map, Mapping):
        raise RebuccaContractError("m5_invalid_class_map")
    items: list[dict] = []
    for index, det in enumerate(detections):
        if not isinstance(det, Mapping):
            continue
        box = det.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        values = [_finite(value) for value in box]
        if any(value is None for value in values):
            continue
        x1, y1, x2, y2 = values
        x1, x2 = max(0.0, min(float(width), x1)), max(0.0, min(float(width), x2))
        y1, y2 = max(0.0, min(float(height), y1)), max(0.0, min(float(height), y2))
        if x2 <= x1 or y2 <= y1:
            continue
        confidence = _finite(det.get("score"))
        if confidence is None or confidence < 0 or confidence > 1:
            continue
        label = det.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        label = label.strip()[:120]
        mapped = class_map.get(label, "unknown")
        if mapped not in ("person", "vehicle", "other", "unknown"):
            raise RebuccaContractError("m5_invalid_class_map")
        item = {
            "candidate_index": index,
            "model_id": model_id,
            "model_name": str(model_name)[:120],
            "label_raw": label,
            "object_class": mapped,
            "confidence": round(confidence, 6),
            "bbox_normalized": [
                round(x1 / width, 6), round(y1 / height, 6),
                round(x2 / width, 6), round(y2 / height, 6),
            ],
            "status": "unverified_detection_proposal",
        }
        poly = det.get("mask_polygon")
        if isinstance(poly, (list, tuple)) and 3 <= len(poly) <= MAX_POLYGON_POINTS:
            points = []
            for point in poly:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    points = []
                    break
                px, py = _finite(point[0]), _finite(point[1])
                if px is None or py is None:
                    points = []
                    break
                points.append([
                    round(max(0.0, min(float(width), px)) / width, 6),
                    round(max(0.0, min(float(height), py)) / height, 6),
                ])
            if len(points) >= 3:
                item["mask_polygon_normalized"] = points
        items.append(item)
        if len(items) >= MAX_PER_MODEL:
            break
    return items
