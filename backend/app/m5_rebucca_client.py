"""TrajNexa's optional image-only client for an isolated Rebucca inference service.

TrajNexa never imports Rebucca Django, weights or the model runtime. Input is
a case-owned image already hash-verified by m4_api. Network is opt-in, limited
to local host/Docker host gateway, with no redirects.
"""
from __future__ import annotations

import base64
import json
import math
from urllib.parse import urlsplit
import urllib.error
import urllib.request

import cv2
import numpy as np


class RebuccaImageError(ValueError):
    """A stable sanitized error code (no paths, user content or secrets)."""


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
MAX_MODELS = 5
MAX_CANDIDATES = 1500


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _endpoint(base_url: str) -> str:
    try:
        u = urlsplit(base_url)
        if (u.scheme != "http" or u.hostname not in ALLOWED_HOSTS
                or not u.port or u.username or u.password or u.query
                or u.fragment or u.path not in ("", "/")):
            raise ValueError
    except (ValueError, TypeError):
        raise RebuccaImageError("m5_invalid_service_endpoint") from None
    return base_url.rstrip("/")


def _request(base_url: str, path: str, *, token: str, timeout: float,
             body: dict | None = None) -> dict:
    root = _endpoint(base_url)
    if not 1 <= timeout <= 120:
        raise RebuccaImageError("m5_invalid_timeout")
    headers = {"Accept": "application/json"}
    payload = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if token:
        headers["X-TrajNexa-Token"] = token
    req = urllib.request.Request(root + path, data=payload, headers=headers,
                                 method="POST" if payload is not None else "GET")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect).open(
                req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RebuccaImageError("m5_service_forbidden") from None
        if exc.code in (400, 413, 422):
            raise RebuccaImageError("m5_service_rejected") from None
        raise RebuccaImageError("m5_service_failed") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RebuccaImageError("m5_service_unavailable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RebuccaImageError("m5_service_bad_response")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RebuccaImageError("m5_service_bad_response") from None
    if not isinstance(result, dict):
        raise RebuccaImageError("m5_service_bad_response")
    return result


def list_models(*, base_url: str, token: str, timeout: float) -> list[dict]:
    payload = _request(base_url, "/models", token=token, timeout=timeout)
    rows = payload.get("models")
    if not isinstance(rows, list) or len(rows) > MAX_MODELS:
        raise RebuccaImageError("m5_service_bad_response")
    clean = []
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            raise RebuccaImageError("m5_service_bad_response")
        mid, name = item.get("id"), item.get("name")
        if not isinstance(mid, str) or not mid or len(mid) > 128 or mid in seen:
            raise RebuccaImageError("m5_service_bad_response")
        if not isinstance(name, str) or not name or len(name) > 120:
            raise RebuccaImageError("m5_service_bad_response")
        seen.add(mid)
        digest = item.get("model_sha256", "")
        if not isinstance(digest, str) or len(digest) != 64 or any(
                ch not in "0123456789abcdefABCDEF" for ch in digest):
            raise RebuccaImageError("m5_service_bad_response")
        clean.append({"id": mid, "name": name,
                      "engine": str(item.get("engine") or "")[:32],
                      "algorithm_type": str(item.get("algorithm_type") or "")[:40],
                      "task_type": str(item.get("task_type") or "detect")[:16],
                      "device": str(item.get("device") or "cpu")[:32],
                      "input_size": item.get("input_size", [640, 640]),
                      "model_sha256": digest.lower()})
    return clean


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        val = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return val if math.isfinite(val) else None


def detect_image(image: np.ndarray, *, model_ids: list[str],
                 min_confidence: float, base_url: str, token: str,
                 timeout: float) -> dict:
    if not isinstance(model_ids, list) or not 1 <= len(model_ids) <= MAX_MODELS or (
            not all(isinstance(mid, str) and 0 < len(mid) <= 128 for mid in model_ids)
            or len(set(model_ids)) != len(model_ids)):
        raise RebuccaImageError("m5_invalid_model_selection")
    if not .2 <= min_confidence <= 1 or not math.isfinite(min_confidence):
        raise RebuccaImageError("m5_invalid_threshold")
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise RebuccaImageError("m5_invalid_image")
    height, width = image.shape[:2]
    if height * width > 20_000_000:
        raise RebuccaImageError("m5_image_too_large")
    ok, jpg = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
    if not ok or len(jpg) > 16 * 1024 * 1024:
        raise RebuccaImageError("m5_image_too_large")
    response = _request(
        base_url, "/detect", token=token, timeout=timeout,
        body={"image_base64": base64.b64encode(jpg.tobytes()).decode("ascii"),
              "model_ids": model_ids, "min_confidence": min_confidence})
    if response.get("status") != "candidates_unverified" or (
            response.get("image_size") != [width, height]):
        raise RebuccaImageError("m5_service_bad_response")
    runs = response.get("model_runs")
    if not isinstance(runs, list) or len(runs) != len(model_ids):
        raise RebuccaImageError("m5_service_bad_response")
    validated_runs = []
    for run, requested in zip(runs, model_ids):
        if not isinstance(run, dict) or run.get("model_id") != requested or (
                run.get("status") not in ("returned_unverified", "inference_error")):
            raise RebuccaImageError("m5_service_bad_response")
        count = run.get("candidate_count")
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 300:
            raise RebuccaImageError("m5_service_bad_response")
        validated_runs.append({"model_id": requested, "status": run["status"],
                               "candidate_count": count})
    candidates = response.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise RebuccaImageError("m5_service_bad_response")
    known = set(model_ids)
    cleaned = []
    for index, item in enumerate(candidates):
        if not isinstance(item, dict) or item.get("model_id") not in known:
            raise RebuccaImageError("m5_service_bad_response")
        box = item.get("bbox_normalized")
        if not isinstance(box, list) or len(box) != 4:
            raise RebuccaImageError("m5_service_bad_response")
        vals = [_finite(v) for v in box]
        if any(v is None for v in vals) or not (
                0 <= vals[0] < vals[2] <= 1 and 0 <= vals[1] < vals[3] <= 1):
            raise RebuccaImageError("m5_service_bad_response")
        confidence = _finite(item.get("confidence"))
        if confidence is None or not 0 <= confidence <= 1:
            raise RebuccaImageError("m5_service_bad_response")
        label = item.get("label_raw")
        name = item.get("model_name")
        if not isinstance(label, str) or not label or len(label) > 120 or (
                not isinstance(name, str) or not name or len(name) > 120):
            raise RebuccaImageError("m5_service_bad_response")
        category = item.get("object_class", "unknown")
        if category not in ("person", "vehicle", "other", "unknown"):
            raise RebuccaImageError("m5_service_bad_response")
        cleaned.append({
            "proposal_index": index, "model_id": item["model_id"],
            "model_name": name, "detector_label": label,
            # The unknown class is not silently guessed to be a vehicle/person.
            "object_class": category if category != "unknown" else "other",
            "category_unmapped": category == "unknown",
            "confidence": confidence, "bbox_normalized": vals,
            "status": "unverified_detection_proposal",
        })
    counts = {mid: 0 for mid in model_ids}
    for candidate in cleaned:
        counts[candidate["model_id"]] += 1
    if any(run["status"] == "returned_unverified" and (
            run["candidate_count"] != counts[run["model_id"]]) for run in validated_runs):
        raise RebuccaImageError("m5_service_bad_response")
    return {"candidates": cleaned, "model_runs": validated_runs}
