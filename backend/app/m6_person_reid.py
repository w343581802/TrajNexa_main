"""M6 P1-A: compare ONE manually specified person with detector person ROIs.

Reuses vendored Rebucca ReidOnnxEngine. No face identification, persistent
embeddings, camera link, background videos or cross-camera claims. Only the
independent M5 image lab calls this code. All images stay in memory.
"""
from __future__ import annotations

from hashlib import sha256
import math
from pathlib import Path

import numpy as np

from app import detectors
from app.config import settings
from app.engines.factory import EngineFactory


class ReIDError(ValueError):
    """Stable error code. No path/weight details are returned to the browser."""


_ENGINE_CACHE: dict = {}


def _model_path() -> tuple[Path, str]:
    root = Path(__file__).resolve().parents[1]
    model_dir = (root / "models").resolve()
    source = (settings.m6_person_reid_model_path or "").strip()
    if not source:
        raise ReIDError("m6_reid_model_unconfigured")
    candidate = (root / source).resolve()
    if (not candidate.is_relative_to(model_dir) or
            candidate.suffix.lower() != ".onnx"):
        raise ReIDError("m6_reid_model_path_invalid")
    if not candidate.is_file():
        raise ReIDError("m6_reid_model_missing")
    # Avoid loading too-large/misplaced weights.
    if candidate.stat().st_size > 500 * 1024 * 1024:
        raise ReIDError("m6_reid_model_too_large")
    digest = sha256(candidate.read_bytes()).hexdigest()
    expected = (settings.m6_person_reid_model_sha256 or "").strip().lower()
    if expected and digest != expected:
        raise ReIDError("m6_reid_model_hash_mismatch")
    return candidate, digest


def _get_engine():
    """Cache a single CPU model. Inference requests are serialized by the API."""
    model, digest = _model_path()
    key = (str(model), digest)
    if _ENGINE_CACHE.get("key") != key:
        try:
            engine = EngineFactory.create(
                "onnxruntime", task_type="reid", algorithm_type="osnet",
                model_file=str(model), input_size=(128, 256), device="cpu")
            if not engine.load():
                raise ReIDError("m6_reid_model_load_failed")
        except ReIDError:
            raise
        except Exception:
            raise ReIDError("m6_reid_model_load_failed") from None
        _ENGINE_CACHE.clear()
        _ENGINE_CACHE.update(key=key, engine=engine)
    return _ENGINE_CACHE["engine"], digest


def _pixel_box(image: np.ndarray, box) -> list[int] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(float(v)) for v in box):
        return None
    x1, y1, x2, y2 = [float(v) for v in box]
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    h, w = image.shape[:2]
    out = [max(0, int(x1 * w)), max(0, int(y1 * h)),
           min(w, math.ceil(x2 * w)), min(h, math.ceil(y2 * h))]
    if out[2] - out[0] < 16 or out[3] - out[1] < 24:
        return None
    return out


def _normalized_embedding(rows) -> np.ndarray:
    vec = np.asarray(rows, dtype=np.float32)
    if vec.ndim != 2 or vec.shape[0] != 1 or vec.shape[1] < 16:
        raise ReIDError("m6_reid_bad_embedding")
    v = vec[0]
    if not np.isfinite(v).all():
        raise ReIDError("m6_reid_bad_embedding")
    length = float(np.linalg.norm(v))
    if length < 1e-8:
        raise ReIDError("m6_reid_bad_embedding")
    return v / length


def rank_people(reference_image: np.ndarray, reference_bbox: list[float],
                search_image: np.ndarray, candidates: list[dict]) -> dict:
    """rank normalized, person-only detector proposals; never assert identity."""
    ref_box = _pixel_box(reference_image, reference_bbox)
    if ref_box is None:
        raise ReIDError("m6_reid_reference_box_invalid")
    engine, digest = _get_engine()

    try:
        indices, vectors = engine.extract_embeddings(reference_image, [ref_box])
        if list(indices) != [0]:
            raise ReIDError("m6_reid_reference_unavailable")
        ref_vec = _normalized_embedding(vectors)
    except ReIDError:
        raise
    except Exception:
        raise ReIDError("m6_reid_reference_unavailable") from None

    selected, boxes = [], []
    for item in candidates:
        if item.get("object_class") != "person":
            continue
        px = _pixel_box(search_image, item.get("bbox_normalized"))
        if px is None:
            continue
        boxes.append(px)
        selected.append(item)
    if not boxes:
        return {"ranking": [], "candidate_count": 0,
                "reid_model_sha256": digest, "status": "no_person_candidates"}
    try:
        indices, vectors = engine.extract_embeddings(search_image, boxes)
    except Exception:
        raise ReIDError("m6_reid_candidate_unavailable") from None
    if len(indices) != len(vectors):
        raise ReIDError("m6_reid_bad_embedding")
    ranking = []
    for i, row in zip(indices, vectors):
        if not isinstance(i, (int, np.integer)) or i < 0 or i >= len(selected):
            raise ReIDError("m6_reid_bad_embedding")
        try:
            vec = _normalized_embedding(np.asarray(row, dtype=np.float32)[None, :])
        except ReIDError:
            # One bad crop should not turn the rest into an asserted mismatch.
            continue
        score = float(np.dot(ref_vec, vec))
        if not math.isfinite(score):
            continue
        candidate = selected[int(i)]
        ranking.append({
            "model_id": candidate.get("model_id"),
            "model_name": candidate.get("model_name"),
            "proposal_index": candidate["proposal_index"],
            "detector_label": candidate["detector_label"],
            "detector_confidence": candidate.get("confidence"),
            "bbox_normalized": candidate["bbox_normalized"],
            "cosine_similarity": round(max(-1, min(1, score)), 5),
            "status": "unverified_similarity",
        })
    ranking.sort(key=lambda x: (-x["cosine_similarity"], x["proposal_index"]))
    for rank, candidate in enumerate(ranking, 1):
        candidate["rank"] = rank
    return {"ranking": ranking, "candidate_count": len(boxes),
            "reid_model_sha256": digest, "status":
                "ranked_unverified" if ranking else "no_valid_embeddings"}
