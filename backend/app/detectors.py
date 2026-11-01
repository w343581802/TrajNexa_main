"""本地检测器注册表：直接使用移植自 Rebucca 的引擎层，不再依赖 Rebucca 进程或 sidecar。

配置来源：backend/config/detectors.json（受版本管理；权重不进库，见 models/README.md）。
引擎层来自 app/engines/（自 Rebucca app/analysis/engines 移植，仅改包路径）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from app.engines.factory import EngineFactory, list_engines

logger = logging.getLogger("app.detectors")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BACKEND_ROOT / "config" / "detectors.json"
MAX_MODELS = 8
MAX_SELECTION = 5


class DetectorError(RuntimeError):
    """检测器不可用；code 用于 API 层映射，不含路径或凭据。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "models": {}}


def config_path() -> Path:
    raw = (os.environ.get("M5_DETECTORS_CONFIG") or "").strip()
    if not raw:
        return DEFAULT_CONFIG
    p = Path(raw)
    return p if p.is_absolute() else BACKEND_ROOT / p


def _resolve(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else BACKEND_ROOT / p


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _labels(spec: dict) -> list[str]:
    labels = spec.get("labels")
    if labels is None and spec.get("labels_file"):
        path = _resolve(str(spec["labels_file"]))
        if not path.is_file():
            raise DetectorError("detector_labels_missing")
        labels = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if labels is None:
        return []
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise DetectorError("detector_labels_invalid")
    return [x.strip() for x in labels]


def _build(spec: dict) -> dict:
    if not isinstance(spec, dict) or not isinstance(spec.get("id"), str) or not spec["id"]:
        raise DetectorError("detector_invalid")
    model_id = spec["id"]
    model_file = _resolve(str(spec.get("model_file") or ""))
    if not model_file.is_file():
        raise DetectorError("detector_weight_missing:" + model_id)
    labels = _labels(spec)
    algorithm_type = str(spec.get("algorithm_type") or "yolo8")
    if algorithm_type == "dfine" and not labels:
        raise DetectorError("detector_dfine_requires_labels")
    input_w = int(spec.get("input_width", 640))
    input_h = int(spec.get("input_height", 640))
    try:
        engine = EngineFactory.create(
            str(spec.get("engine") or "onnxruntime"),
            model_file=str(model_file),
            labels=labels,
            input_size=(input_w, input_h),
            conf_threshold=float(spec.get("conf_threshold", 0.35)),
            iou_threshold=float(spec.get("iou_threshold", 0.5)),
            algorithm_type=algorithm_type,
            task_type=str(spec.get("task_type") or "detect"),
            device=str(spec.get("device") or "cpu"),
        )
    except Exception as exc:  # 引擎依赖缺失等
        raise DetectorError("detector_engine_unavailable:" + model_id) from exc
    if not engine.load():
        raise DetectorError("detector_load_failed:" + model_id)
    return {
        "id": model_id,
        "name": str(spec.get("name") or model_id),
        "engine": str(spec.get("engine") or "onnxruntime"),
        "algorithm_type": algorithm_type,
        "task_type": str(spec.get("task_type") or "detect"),
        "device": str(spec.get("device") or "cpu"),
        "input_size": [input_w, input_h],
        "conf_threshold": float(spec.get("conf_threshold", 0.35)),
        "iou_threshold": float(spec.get("iou_threshold", 0.5)),
        "model_sha256": _sha256(model_file),
        "engine_obj": engine,
        "failures": [],
    }


def _load_all(force: bool = False) -> dict:
    path = config_path()
    key = (str(path), path.stat().st_mtime if path.is_file() else None)
    with _lock:
        if not force and _cache["key"] == key:
            return _cache["models"]
        models: dict[str, dict] = {}
        errors: dict[str, str] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                specs = raw.get("detectors") if isinstance(raw, dict) else None
                if not isinstance(specs, list) or len(specs) > MAX_MODELS:
                    raise DetectorError("detector_config_invalid")
            except DetectorError:
                raise
            except Exception as exc:
                raise DetectorError("detector_config_invalid") from exc
            for spec in specs:
                try:
                    item = _build(spec)
                    models[item["id"]] = item
                except DetectorError as exc:
                    model_id = str((spec or {}).get("id") or "?") if isinstance(spec, dict) else "?"
                    errors[model_id] = exc.code
                    logger.warning("detector %s 不可用: %s", model_id, exc.code)
        _cache["key"] = key
        _cache["models"] = models
        _cache["errors"] = errors
        return models


def reload() -> None:
    _load_all(force=True)


def enabled() -> bool:
    return config_path().is_file()


def list_models() -> list[dict]:
    models = _load_all()
    return [{k: v for k, v in item.items() if k not in ("engine_obj", "failures")}
            for item in models.values()]


def default_model_id() -> str:
    """业务默认检测器：配置值优先，否则取第一个已加载模型。"""
    models = _load_all()
    configured = (os.environ.get("M5_WORKSPACE_DETECTOR_ID") or "").strip()
    if configured and configured in models:
        return configured
    return next(iter(models), "")


def model_meta(model_id: str) -> dict:
    item = _load_all().get(model_id) or {}
    return {k: v for k, v in item.items() if k not in ("engine_obj", "failures")}


def engines_info() -> list[dict]:
    return list_engines()


def detect_flat(image, model_ids: list[str], min_confidence: float = 0.35) -> dict:
    """扁平化输出：candidates + model_runs，供工作台/视频采样直接使用。"""
    models = _load_all()
    result = detect(image, model_ids, min_confidence)
    candidates = []
    runs = []
    for run in result["models"]:
        item = models[run["id"]]
        for p in run["predictions"]:
            candidates.append({"box": p["box"], "label": p["label"], "score": p["score"],
                               "model_id": run["id"]})
        runs.append({"id": run["id"], "name": item["name"],
                     "algorithm_type": item["algorithm_type"],
                     "model_sha256": item["model_sha256"],
                     "candidate_count": len(run["predictions"])})
    return {"candidates": candidates, "model_runs": runs}


def detect(image, model_ids: list[str], min_confidence: float = 0.35) -> dict:
    """在一张 BGR 图上按所选检测器依次推理；输出只含业务字段。"""
    models = _load_all()
    if not isinstance(model_ids, list) or not model_ids or len(model_ids) > MAX_SELECTION or (
            not all(isinstance(x, str) and x in models for x in model_ids)
            or len(set(model_ids)) != len(model_ids)):
        raise DetectorError("invalid_model_selection")
    if isinstance(min_confidence, bool) or not isinstance(min_confidence, (int, float)) or not (
            0.2 <= float(min_confidence) <= 1.0):
        raise DetectorError("invalid_threshold")
    out = []
    for model_id in model_ids:
        item = models[model_id]
        engine = item["engine_obj"]
        engine.conf_threshold = float(min_confidence)
        try:
            raw = engine.detect(image) or []
        except Exception as exc:
            logger.warning("detector %s detect 失败: %s", model_id, exc)
            raise DetectorError("detector_inference_failed:" + model_id) from exc
        preds = []
        for det in raw:
            box = [float(v) for v in (det.get("box") or [0, 0, 0, 0])]
            score = float(det.get("score") or 0.0)
            if len(box) != 4 or score < float(min_confidence):
                continue
            preds.append({"box": box, "label": str(det.get("label") or ""), "score": score})
        out.append({"id": model_id, "predictions": preds})
    return {"models": out}
