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
    # B7-A：能力层配置优先，环境变量作为兜底；换权重只改配置，不改代码。
    from app import model_capabilities

    source = (model_capabilities.reid_model_path() or "").strip()
    env_source = (settings.m6_person_reid_model_path or "").strip()
    if env_source:
        # 环境变量仍然显式校验：不允许借它把权重指到 models/ 之外。
        env_path = (root / env_source).resolve()
        if (not env_path.is_relative_to(model_dir) or
                env_path.suffix.lower() != ".onnx"):
            raise ReIDError("m6_reid_model_path_invalid")
    if not source:
        source = env_source
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


def _pixel_box_from_size(width: int, height: int, box) -> list[int] | None:
    """归一化框 → 像素框；宽高按传入的图片尺寸算，供候选池与推理复用。"""
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(float(v)) for v in box):
        return None
    x1, y1, x2, y2 = [float(v) for v in box]
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return None
    out = [max(0, int(x1 * width)), max(0, int(y1 * height)),
           min(width, math.ceil(x2 * width)), min(height, math.ceil(y2 * height))]
    if out[2] - out[0] < 16 or out[3] - out[1] < 24:
        return None
    return out


def _pixel_box(image: np.ndarray, box) -> list[int] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(float(v)) for v in box):
        return None
    h, w = image.shape[:2]
    return _pixel_box_from_size(w, h, box)


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


POOL_DEFAULTS = {
    "high_conf": 0.40,
    "min_height_px": 70,
    "dedupe_iou": 0.5,
    "similarity_threshold": 0.72,
}


def _iou_px(a: list[int], b: list[int]) -> float:
    """两个像素框的 IoU。"""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = float((x2 - x1) * (y2 - y1))
    area = float((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])) - inter
    return inter / area if area > 0 else 0.0


def split_candidate_pool(image_size, candidates, high_conf, min_height_px, dedupe_iou):
    """检测候选 → 同帧去重 → 两级候选池。

    高质量池优先送 ReID；低质量池（低置信度/太小）不丢弃，留作备用候选，
    以免把小目标、骑手、遮挡目标过早排除。参数都是实验旋钮，不是业务规则。
    """
    width, height = int(image_size[0]), int(image_size[1])
    rows = []
    for item in candidates:
        if item.get("object_class") != "person":
            continue
        px = _pixel_box_from_size(width, height, item.get("bbox_normalized"))
        if px is None:
            continue
        conf = item.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) and not isinstance(conf, bool) else 0.0
        box_h = px[3] - px[1]
        reasons = []
        if conf < float(high_conf):
            reasons.append("low_confidence")
        if box_h < int(min_height_px):
            reasons.append("small")
        rows.append({
            "proposal_index": item.get("proposal_index"),
            "bbox_normalized": item.get("bbox_normalized"),
            "detector_label": item.get("detector_label"),
            "detector_confidence": round(conf, 4),
            "model_id": item.get("model_id"),
            "model_name": item.get("model_name"),
            "pixel_box": px,
            "pixel_height": box_h,
            "tier": "low" if reasons else "high",
            "tier_reason": reasons,
        })
    rows.sort(key=lambda r: (-r["detector_confidence"], str(r["proposal_index"])))
    kept, duplicates = [], []
    for row in rows:
        twin = next((k for k in kept if _iou_px(k["pixel_box"], row["pixel_box"]) >= float(dedupe_iou)), None)
        if twin is not None:
            duplicates.append({"proposal_index": row["proposal_index"],
                               "duplicate_of": twin["proposal_index"],
                               "iou": round(_iou_px(twin["pixel_box"], row["pixel_box"]), 3)})
            continue
        kept.append(row)
    return {
        "high": [r for r in kept if r["tier"] == "high"],
        "low": [r for r in kept if r["tier"] == "low"],
        "duplicates": duplicates,
        "raw_person_count": len(rows),
    }


def _ranked_row(row, score, tier):
    return {
        "model_id": row.get("model_id"),
        "model_name": row.get("model_name"),
        "proposal_index": row["proposal_index"],
        "detector_label": row["detector_label"],
        "detector_confidence": row.get("detector_confidence"),
        "bbox_normalized": row["bbox_normalized"],
        "pixel_height": row["pixel_height"],
        "tier": tier,
        "tier_reason": row.get("tier_reason") or [],
        "cosine_similarity": round(max(-1, min(1, score)), 5),
        "status": "unverified_similarity",
    }


def _decide(pool, ranking, threshold):
    """三态：候选值得核查 / 证据不足 / 未检测到可用人员。都不是身份确认。"""
    best = ranking[0]["cosine_similarity"] if ranking else None
    margin = None
    if len(ranking) > 1:
        margin = round(best - ranking[1]["cosine_similarity"], 5)
    if not pool["raw_person_count"]:
        return {"state": "no_person_candidate", "reason": "detector_found_no_person",
                "threshold": threshold, "best_similarity": None, "margin": None}
    if not pool["high"]:
        return {"state": "insufficient_evidence", "reason": "no_high_quality_candidate",
                "threshold": threshold, "best_similarity": best, "margin": None}
    if best is None:
        return {"state": "insufficient_evidence", "reason": "no_valid_embedding",
                "threshold": threshold, "best_similarity": None, "margin": None}
    if best < float(threshold):
        return {"state": "insufficient_evidence", "reason": "similarity_below_threshold",
                "threshold": threshold, "best_similarity": best, "margin": margin}
    return {
        "state": "candidate_for_review",
        "reason": "similarity_above_threshold",
        "threshold": threshold,
        "best_similarity": best,
        "margin": margin,
        "narrow_margin": bool(margin is not None and margin < 0.05),
    }


def rank_people(reference_image: np.ndarray, reference_bbox: list[float],
                search_image: np.ndarray, candidates: list[dict],
                high_conf: float = POOL_DEFAULTS["high_conf"],
                min_height_px: int = POOL_DEFAULTS["min_height_px"],
                dedupe_iou: float = POOL_DEFAULTS["dedupe_iou"],
                similarity_threshold: float = POOL_DEFAULTS["similarity_threshold"]) -> dict:
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

    pool = split_candidate_pool((search_image.shape[1], search_image.shape[0]),
                                candidates, high_conf, min_height_px, dedupe_iou)
    summary = {
        "raw_person_count": pool["raw_person_count"],
        "after_dedupe": len(pool["high"]) + len(pool["low"]),
        "duplicates_removed": len(pool["duplicates"]),
        "high_count": len(pool["high"]),
        "low_count": len(pool["low"]),
        "duplicates": pool["duplicates"],
        "params": {"high_conf": high_conf, "min_height_px": min_height_px,
                   "dedupe_iou": dedupe_iou, "similarity_threshold": similarity_threshold},
    }
    selected = pool["high"] + pool["low"]
    if not selected:
        return {"ranking": [], "backup": [], "candidate_count": 0,
                "pool": summary, "decision": _decide(pool, [], similarity_threshold),
                "reid_model_sha256": digest, "status": "no_person_candidates"}

    try:
        indices, vectors = engine.extract_embeddings(search_image,
                                                     [r["pixel_box"] for r in selected])
    except Exception:
        raise ReIDError("m6_reid_candidate_unavailable") from None
    if len(indices) != len(vectors):
        raise ReIDError("m6_reid_bad_embedding")

    high, low = [], []
    for i, row in zip(indices, vectors):
        if not isinstance(i, (int, np.integer)) or i < 0 or i >= len(selected):
            raise ReIDError("m6_reid_bad_embedding")
        try:
            vec = _normalized_embedding(np.asarray(row, dtype=np.float32)[None, :])
        except ReIDError:
            # 单个裁剪坏掉不该让其余候选变成「已确认不匹配」
            continue
        score = float(np.dot(ref_vec, vec))
        if not math.isfinite(score):
            continue
        src = selected[int(i)]
        (high if src["tier"] == "high" else low).append(_ranked_row(src, score, src["tier"]))

    high.sort(key=lambda x: (-x["cosine_similarity"], str(x["proposal_index"])))
    low.sort(key=lambda x: (-x["cosine_similarity"], str(x["proposal_index"])))
    for rank, candidate in enumerate(high, 1):
        candidate["rank"] = rank
    for rank, candidate in enumerate(low, 1):
        candidate["backup_rank"] = rank
    decision = _decide(pool, high, similarity_threshold)
    return {"ranking": high, "backup": low, "candidate_count": len(high),
            "pool": summary, "decision": decision, "reid_model_sha256": digest,
            "status": "ranked_unverified" if (high or low) else "no_valid_embeddings"}
