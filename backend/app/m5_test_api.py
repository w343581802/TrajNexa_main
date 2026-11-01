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

from app import detectors, m4_vision, m6_person_reid
from app.config import settings
from app.m5_vision_registry import VisionRegistryError, resolve_vision_model
from app.m5_vision_transport import chat_for
from app.recording_api import fail


router = APIRouter(prefix="/api/v1/ai/model-test")
MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 20_000_000
_vlm_gate = BoundedSemaphore(value=1)
_m6_gate = BoundedSemaphore(value=1)  # shared CPU ReID model for lab; no background jobs


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


@router.post("/reid-search")
def reid_search(
    reference_file: UploadFile = File(...),
    search_file: UploadFile = File(...),
    reference_bbox_normalized: str = Form(...),
    detector_model_id: str = Form(...),
    min_confidence: float = Form(0.35),
    high_conf: float = Form(0.40),
    min_height_px: int = Form(70),
    dedupe_iou: float = Form(0.5),
    similarity_threshold: float = Form(0.72),
):
    """P1-A: ONE reference-person ROI vs detector PERSON candidates in image B.

    No person identity assertion, no embeddings in response, and NO case writes.
    A ReID model is NOT a detector; use an actually installed YOLO/D-FINE first.
    """
    if not math.isfinite(min_confidence) or not .2 <= min_confidence <= 1:
        fail(422, "m6-reid-confidence", "检测阈值须介于 0.2 至 1。")
    # 以下四项都是实验旋钮：默认值来自 P1-A-4 单摄像头小样本，写死为业务规则前需更多数据
    if not math.isfinite(high_conf) or not .2 <= high_conf <= 1:
        fail(422, "m6-reid-pool-params", "高质量候选置信度须介于 0.2 至 1。")
    if isinstance(min_height_px, bool) or not 24 <= min_height_px <= 1000:
        fail(422, "m6-reid-pool-params", "最小人体高度须介于 24 至 1000 像素。")
    if not math.isfinite(dedupe_iou) or not .1 <= dedupe_iou <= .95:
        fail(422, "m6-reid-pool-params", "去重 IoU 须介于 0.1 至 0.95。")
    if not math.isfinite(similarity_threshold) or not -1 <= similarity_threshold <= 1:
        fail(422, "m6-reid-pool-params", "相似度阈值须介于 -1 至 1。")
    if not detector_model_id or len(detector_model_id) > 64:
        fail(422, "m6-reid-detector-id", "请选择已经加载的人员检测模型。")
    try:
        registered = detectors.list_models()
    except detectors.DetectorError:
        fail(503, "m6-reid-detector-unavailable", "检测模型目录暂不可用。")
    if detector_model_id not in {item["id"] for item in registered}:
        fail(422, "m6-reid-detector-id", "所选检测模型未安装或不可用。")
    try:
        bbox = json.loads(reference_bbox_normalized)
    except (TypeError, ValueError):
        fail(422, "m6-reid-reference-box", "请在参考图片上框选人员。")
    ref_image, ref_sha = _read_image(reference_file)
    search_image, search_sha = _read_image(search_file)
    if m6_person_reid._pixel_box(ref_image, bbox) is None:
        fail(422, "m6-reid-reference-box", "参考人员框过小或无效，请重新框选。")
    if not _m6_gate.acquire(blocking=False):
        fail(429, "m6-reid-busy", "已有 ReID 实验正在进行，请稍后重试。")
    try:
        started = time.monotonic()
        try:
            detected = detectors.detect_flat(search_image, [detector_model_id], min_confidence)
            proposals = detectors.proposals(detected["candidates"],
                (search_image.shape[1], search_image.shape[0]))
        except detectors.DetectorError:
            fail(503, "m6-reid-detector-failed", "检测器推理失败；尚未进行同目标比较。")
        try:
            compared = m6_person_reid.rank_people(
                ref_image, bbox, search_image, proposals,
                high_conf=high_conf, min_height_px=min_height_px,
                dedupe_iou=dedupe_iou, similarity_threshold=similarity_threshold)
        except m6_person_reid.ReIDError as exc:
            code = str(exc)
            status = 422 if code == "m6_reid_reference_box_invalid" else 503
            fail(status, code.replace("_", "-"),
                 "人员 ReID 模型未配置、权重异常或无法提取向量；本次不会产生假相似度。")
        return {
            "test_only": True, "identity_established": False, "case_id": None,
            "reference_source_sha256": ref_sha,
            "search_source_sha256": search_sha,
            "reference_bbox_normalized": bbox,
            "detector_model_id": detector_model_id,
            "reid_model_sha256": compared["reid_model_sha256"],
            "status": compared["status"],
            "person_candidate_count": compared["candidate_count"],
            "ranking": compared["ranking"],
            "backup": compared.get("backup", []),
            "candidate_pool": compared["pool"],
            "decision": compared["decision"],
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "note": "排名仅表示 OSNet 视觉外观相似度，不是身份概率，需人工复核。",
        }
    finally:
        _m6_gate.release()
