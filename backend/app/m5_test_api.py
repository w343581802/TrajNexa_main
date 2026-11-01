"""Image-only experiment endpoints. No case, media, evidence or map writes.

Uses Rebucca's configured small model sidecar and TrajNexa's existing VLM
detection and original-resolution ROI parser. Server-side model allowlist only.
"""
from __future__ import annotations

import json
import math
import time
from hashlib import sha256
from threading import BoundedSemaphore

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, UploadFile

from app import detectors, m4_vision
from app.config import settings
from app.m5_vision_registry import VisionRegistryError, resolve_vision_model
from app.m5_vision_transport import chat_for
from app.recording_api import fail


router = APIRouter(prefix="/api/v1/ai/model-test")
MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 20_000_000
_vlm_gate = BoundedSemaphore(value=1)


def _read_image(file: UploadFile):
    if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
        fail(422, "m5-test-image-type", "只支持 PNG、JPEG、WebP 测试图片。")
    data = file.file.read(MAX_BYTES + 1)
    if not data:
        fail(422, "m5-test-image-empty", "请选择测试图片。")
    if len(data) > MAX_BYTES:
        fail(413, "m5-test-image-too-large", "图片不得超过 12MB。")
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        fail(422, "m5-test-image-invalid", "图片解码失败。")
    height, width = image.shape[:2]
    if width < 16 or height < 16 or width * height > MAX_PIXELS:
        fail(422, "m5-test-image-size", "图片分辨率超出允许范围。")
    return image, sha256(data).hexdigest()


def _ids(text: str, limit: int) -> list[str]:
    if not isinstance(text, str) or len(text) > 1500:
        fail(422, "m5-test-model-ids", "模型选择无效。")
    try:
        ids = json.loads(text)
    except (TypeError, ValueError):
        fail(422, "m5-test-model-ids", "模型选择须为 JSON 数组。")
    if (not isinstance(ids, list) or len(ids) > limit
            or any(not isinstance(v, str) or not 1 <= len(v) <= 128 for v in ids)
            or len(set(ids)) != len(ids)):
        fail(422, "m5-test-model-ids", "模型数量超限或重复。")
    return ids


def _vision_models(ids: list[str]):
    rows = []
    for mid in ids:
        try:
            rows.append(resolve_vision_model(settings, mid))
        except VisionRegistryError:
            fail(422, "m5-test-unknown-model", "视觉模型未注册或配置无效。")
    if any(not m["enabled"] for m in rows):
        fail(503, "m5-test-vision-disabled",
             "某些已选视觉模型未启用，请在模型管理页检查配置。")
    return rows


def _run(row: dict, status: str, count: int, started: float, error: str = ""):
    out = {"model_id": row["id"], "kind": row["kind"],
           "status": status, "candidate_count": count,
           "elapsed_ms": round((time.monotonic() - started) * 1000)}
    if error:
        out["error_code"] = error[:80]
    return out


@router.post("/detect")
def detect(
    file: UploadFile = File(...),
    small_model_ids: str = Form("[]"),
    vision_model_ids: str = Form("[]"),
    min_confidence: float = Form(0.35),
):
    if not math.isfinite(min_confidence) or not .2 <= min_confidence <= 1:
        fail(422, "m5-test-confidence", "置信度须介于 0.2–1。")
    small = _ids(small_model_ids, 5)
    vision = _ids(vision_model_ids, 3)
    if not small and not vision:
        fail(422, "m5-test-no-model", "请至少选择一个模型。")
    entries = _vision_models(vision)
    if small and not detectors.enabled():
        fail(503, "m5-test-small-disabled", "本机检测器未配置。")
    image, digest = _read_image(file)
    candidates, runs = [], []
    begun_all = time.monotonic()
    if small:
        start = time.monotonic()
        try:
            result = detectors.detect_flat(image, small, min_confidence)
        except detectors.DetectorError as exc:
            for mid in small:
                runs.append(_run({"id": mid, "kind": "detector"},
                                 "inference_error", 0, start, str(exc)))
        else:
            candidates.extend(detectors.proposals(
                result["candidates"], (image.shape[1], image.shape[0])))
            for entry in result["model_runs"]:
                runs.append(_run({"id": entry["id"], "kind": "detector"},
                                 "ok", entry["candidate_count"], start))

    if entries:
        if not _vlm_gate.acquire(blocking=False):
            for entry in entries:
                runs.append(_run({"id": entry["id"], "kind": "vision"},
                                 "inference_error", 0, time.monotonic(), "m5-vision-busy"))
        else:
            try:
                for entry in entries:
                    start = time.monotonic()
                    try:
                        proposals = m4_vision.detect_objects(
                            image, base_url=settings.vision_base_url,
                            model=entry["model_id"],
                            timeout=min(120, settings.vision_timeout_seconds),
                            chat_fn=chat_for(entry))
                    except m4_vision.VisionError as exc:
                        runs.append(_run({"id": entry["id"], "kind": "vision"},
                                         "inference_error", 0, start, str(exc)))
                        continue
                    for proposal in proposals:
                        candidates.append({**proposal, "model_id": entry["id"],
                                           "model_name": entry["name"],
                                           "proposal_index": len(candidates)})
                    runs.append(_run({"id": entry["id"], "kind": "vision"},
                                     "returned_unverified", len(proposals), start))
            finally:
                _vlm_gate.release()
    if not runs:
        fail(503, "m5-test-no-model-run", "没有模型执行。")
    return {"test_only": True,
            "status": "all_models_failed" if all(
                x["status"] == "inference_error" for x in runs) else "candidates_unverified",
            "source_sha256": digest,
            "image_size": [image.shape[1], image.shape[0]],
            "model_runs": runs, "candidates": candidates,
            "candidate_count": len(candidates),
            "elapsed_ms": round((time.monotonic() - begun_all) * 1000),
            "identity_established": False, "case_id": None}


@router.post("/roi")
def roi(
    file: UploadFile = File(...),
    vision_model_ids: str = Form("[]"),
    bbox_normalized: str = Form("[0,0,1,1]"),
    object_class: str = Form("person"),
    prompt_override: str = Form(""),
):
    ids = _ids(vision_model_ids, 3)
    if not ids:
        fail(422, "m5-test-no-vision-model", "请选择视觉模型。")
    entries = _vision_models(ids)
    if object_class not in ("person", "vehicle", "other"):
        fail(422, "m5-test-class", "无效目标类别。")
    if len(prompt_override) > 2400:
        fail(422, "m5-test-prompt", "提示词不能超过 2400 字。")
    try:
        box = json.loads(bbox_normalized)
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError
        coords = [float(x) if not isinstance(x, bool) else math.nan for x in box]
        if not all(math.isfinite(x) for x in coords) or not (
                0 <= coords[0] < coords[2] <= 1 and 0 <= coords[1] < coords[3] <= 1):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        fail(422, "m5-test-roi", "框选区域无效。")
    image, digest = _read_image(file)
    if not _vlm_gate.acquire(blocking=False):
        fail(429, "m5-test-vision-busy", "视觉模型繁忙。")
    prompt = m4_vision.effective_feature_prompt(object_class, prompt_override)
    rows = []
    try:
        for entry in entries:
            start = time.monotonic()
            row = {"model_id": entry["id"], "model_name": entry["name"],
                   "provider": entry["provider"], "status": "inference_error",
                   "features_unverified": [],
                   "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest()}
            try:
                row["features_unverified"] = m4_vision.describe_roi(
                    image, coords, base_url=settings.vision_base_url,
                    model=entry["model_id"],
                    timeout=min(120, settings.vision_timeout_seconds),
                    object_class=object_class, prompt_override=prompt_override or None,
                    chat_fn=chat_for(entry))
            except m4_vision.VisionError as exc:
                row["error_code"] = str(exc)[:80]
            else:
                row["status"] = "returned_unverified"
            row["elapsed_ms"] = round((time.monotonic() - start) * 1000)
            rows.append(row)
    finally:
        _vlm_gate.release()
    return {"test_only": True, "status": "completed_with_unverified_results",
            "source_sha256": digest, "bbox_normalized": coords,
            "object_class": object_class, "results": rows,
            "identity_established": False, "case_id": None}
