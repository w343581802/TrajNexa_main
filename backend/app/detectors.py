"""本地检测器注册表：直接使用移植自 Rebucca 的引擎层，不再依赖 Rebucca 进程或 sidecar。

配置来源：backend/config/detectors.json（受版本管理；权重不进库，见 models/README.md）。
引擎层来自 app/engines/（自 Rebucca app/analysis/engines 移植，仅改包路径）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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
# 同一引擎实例不允许并发推理：工作台、模型测试、视频采样共用一个锁
_infer_lock = threading.BoundedSemaphore(1)
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
    if algorithm_type in ("dfine", "rfdetr") and not labels:
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
        "labels": labels,
        "engine_obj": engine,
        "failures": [],
    }


# COCO 顺序里属于「人」和「车」的下标；标签文件就是 COCO 顺序，所以下标可靠
_PERSON_INDEXES = {0}
_VEHICLE_INDEXES = {1, 2, 3, 5, 6, 7}
_PERSON_NAMES = {"person", "人", "行人", "people"}
_VEHICLE_NAMES = {
    "bicycle", "car", "motorcycle", "bus", "truck", "train",
    "自行车", "汽车", "摩托车", "公共汽车", "公交车", "卡车", "货车", "火车",
}


def _object_class(label: str, label_index) -> str:
    text = (label or "").strip()
    if label_index in _PERSON_INDEXES or text.lower() in _PERSON_NAMES:
        return "person"
    if label_index in _VEHICLE_INDEXES or text.lower() in _VEHICLE_NAMES:
        return "vehicle"
    return "other"


def proposals(candidates: list[dict], image_size) -> list[dict]:
    """把扁平的像素框候选翻成前端契约：归一化 bbox + 类别 + 置信度。

    引擎输出的是像素框，界面按 [0,1] 归一化坐标画框；不转换会让页面拿到
    没有 bbox_normalized 的候选而整块渲染崩掉。
    """
    if not isinstance(image_size, (list, tuple)) or len(image_size) != 2:
        raise DetectorError("detector_invalid_image_size")
    width, height = int(image_size[0]), int(image_size[1])
    if width <= 0 or height <= 0:
        raise DetectorError("detector_invalid_image_size")
    out = []
    for index, item in enumerate(candidates or []):
        box = item.get("box") or []
        if len(box) != 4:
            continue
        try:
            x1 = min(max(float(box[0]) / width, 0.0), 1.0)
            y1 = min(max(float(box[1]) / height, 0.0), 1.0)
            x2 = min(max(float(box[2]) / width, 0.0), 1.0)
            y2 = min(max(float(box[3]) / height, 0.0), 1.0)
        except (TypeError, ValueError):
            continue
        if not (x1 < x2 and y1 < y2):
            continue
        label = str(item.get("label") or "")
        score = float(item.get("score") or 0.0)
        out.append({
            "proposal_index": index,
            "object_class": _object_class(label, item.get("label_index")),
            "detector_label": label or "未标注",
            "confidence": round(min(max(score, 0.0), 1.0), 4),
            "bbox_normalized": [round(x1, 6), round(y1, 6), round(x2, 6), round(y2, 6)],
            "status": "unverified_detection_proposal",
            "model_id": item.get("model_id"),
            "model_name": item.get("model_name"),
        })
    return out


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


def unavailable_models() -> list[dict]:
    """管理页显示未就绪配置，推理接口仍仅接受已加载模型。不要暴露本地权重路径。"""
    loaded = _load_all()
    errors = dict(_cache.get("errors") or {})
    raw = _read_raw()
    out = []
    for spec in raw["detectors"]:
        if not isinstance(spec, dict) or spec.get("id") in loaded:
            continue
        model_id = str(spec.get("id") or "")
        if not model_id:
            continue
        out.append({
            "id": model_id,
            "name": str(spec.get("name") or model_id),
            "engine": str(spec.get("engine") or "onnxruntime"),
            "algorithm_type": str(spec.get("algorithm_type") or "yolo8"),
            "task_type": str(spec.get("task_type") or "detect"),
            "device": str(spec.get("device") or "cpu"),
            "input_size": [int(spec.get("input_width") or 640),
                           int(spec.get("input_height") or 640)],
            "conf_threshold": float(spec.get("conf_threshold") or 0.35),
            "iou_threshold": float(spec.get("iou_threshold") or 0.5),
            "error_code": errors.get(model_id, "detector_unavailable"),
            "status": "unavailable",
        })
    return out


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


EDITABLE_FIELDS = ("name", "conf_threshold", "iou_threshold", "device", "labels")
MAX_LABELS = 200
MAX_LABEL_LEN = 40


def _parse_labels(value) -> list[str]:
    """支持类别：英文/中文逗号分隔的字符串，或字符串数组；中文标签原样保留。"""
    if isinstance(value, str):
        parts = re.split(r"[,，]", value)
    elif isinstance(value, list) and all(isinstance(x, str) for x in value):
        parts = value
    else:
        raise DetectorError("detector_update_bad_labels")
    out: list[str] = []
    for part in parts:
        text = part.strip()
        if not text:
            continue
        if len(text) > MAX_LABEL_LEN:
            raise DetectorError("detector_update_bad_labels")
        if text not in out:
            out.append(text)
    if not out or len(out) > MAX_LABELS:
        raise DetectorError("detector_update_bad_labels")
    return out


def _read_raw() -> dict:
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DetectorError("detector_config_missing") from None
    except Exception as exc:
        raise DetectorError("detector_config_invalid") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("detectors"), list):
        raise DetectorError("detector_config_invalid")
    return raw


def _validate_changes(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise DetectorError("detector_update_empty")
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise DetectorError("detector_update_unknown_field:" + ",".join(sorted(unknown)))
    clean: dict = {}
    if "name" in changes:
        name = str(changes["name"] or "").strip()
        if not 1 <= len(name) <= 64:
            raise DetectorError("detector_update_bad_name")
        clean["name"] = name
    for key in ("conf_threshold", "iou_threshold"):
        if key in changes:
            value = changes[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (
                    0 < float(value) <= 1.0):
                raise DetectorError("detector_update_bad_threshold")
            clean[key] = round(float(value), 3)
        
    if "labels" in changes:
        clean["labels"] = _parse_labels(changes["labels"])
    if "device" in changes:
        device = str(changes["device"] or "").strip().lower()
        if device not in ("cpu", "gpu"):
            raise DetectorError("detector_update_bad_device")
        clean["device"] = device
    return clean


def update_spec(model_id: str, changes: dict) -> list[dict]:
    """改一个检测器的可编辑字段；写盘后热加载，加载失败则回滚原文件。"""
    clean = _validate_changes(changes)
    path = config_path()
    raw = _read_raw()
    target = next((d for d in raw["detectors"]
                   if isinstance(d, dict) and d.get("id") == model_id), None)
    if target is None:
        raise DetectorError("detector_not_found")
    original = path.read_text(encoding="utf-8")
    target.update(clean)
    if "labels" in clean:
        # 页面上直接填了类别，就以它为准，不再同时留着标签文件
        target.pop("labels_file", None)
    # 只写可编辑字段，保留其余键与顺序
    _write_raw(raw)
    _reload_or_rollback(original, model_id)
    return list_models()


CREATE_FIELDS = ("id", "name", "engine", "algorithm_type", "task_type", "device",
                 "model_file", "labels_file", "labels", "input_width", "input_height",
                 "conf_threshold", "iou_threshold")
MODEL_ROOT = BACKEND_ROOT / "models"
WEIGHT_SUFFIXES = (".onnx", ".pt", ".pth", ".bin", ".xml")


def _write_raw(raw: dict) -> None:
    path = config_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _reload_or_rollback(original: str, model_id: str) -> None:
    try:
        _load_all(force=True)
    except DetectorError:
        config_path().write_text(original, encoding="utf-8")
        _load_all(force=True)
        raise
    if model_id not in _load_all():
        config_path().write_text(original, encoding="utf-8")
        _load_all(force=True)
        raise DetectorError("detector_update_rejected:" + model_id)


def list_weight_files() -> list[str]:
    """models/ 下已存在的权重文件，供新增检测器时选择（相对 backend/ 的路径）。"""
    if not MODEL_ROOT.is_dir():
        return []
    out = []
    for path in sorted(MODEL_ROOT.rglob("*")):
        if path.is_file() and path.suffix.lower() in WEIGHT_SUFFIXES:
            out.append(str(path.relative_to(BACKEND_ROOT)).replace("\\", "/"))
    return out[:200]


def create_spec(spec: dict) -> list[dict]:
    """新增一个检测器：校验 → 写盘 → 加载；加载失败则回滚。"""
    raw = _read_raw()
    if not isinstance(spec, dict):
        raise DetectorError("detector_invalid")
    unknown = set(spec) - set(CREATE_FIELDS)
    if unknown:
        raise DetectorError("detector_create_unknown_field:" + ",".join(sorted(unknown)))
    model_id = str(spec.get("id") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", model_id):
        raise DetectorError("detector_create_bad_id")
    if any(isinstance(d, dict) and d.get("id") == model_id for d in raw["detectors"]):
        raise DetectorError("detector_create_duplicate_id")
    if len(raw["detectors"]) >= MAX_MODELS:
        raise DetectorError("detector_create_too_many")
    entry = {"id": model_id}
    entry.update(_validate_changes({k: v for k, v in spec.items()
                                  if k in EDITABLE_FIELDS and v is not None}))
    engine = str(spec.get("engine") or "onnxruntime").strip().lower()
    known = {e.get("name") for e in list_engines() if isinstance(e, dict)}
    if known and engine not in known:
        raise DetectorError("detector_create_bad_engine")
    algorithm = str(spec.get("algorithm_type") or "yolo8").strip().lower()
    # 引擎层 yolo_postprocess 支持 yolo5/8/11/26 四代输出格式；D-FINE 走专用引擎
    if algorithm not in ("dfine", "rfdetr", "yolo5", "yolov5", "yolo8", "yolov8",
                         "yolo11", "yolo26", "yolo", "yolo_end2end"):
        raise DetectorError("detector_create_bad_algorithm")
    task = str(spec.get("task_type") or "detect").strip().lower()
    if task not in ("detect", "segment"):
        raise DetectorError("detector_create_bad_task")
    weight = _resolve(str(spec.get("model_file") or ""))
    if not weight.is_file():
        raise DetectorError("detector_create_weight_missing")
    if algorithm == "rfdetr" and (engine != "rfdetr_pytorch" or task != "detect"
                                   or weight.suffix.lower() != ".pth" or
                                   int(spec.get("input_width") or 640) != 512 or
                                   int(spec.get("input_height") or 640) != 512):
        raise DetectorError("detector_create_rfdetr_contract_invalid")
    entry.update({"engine": engine, "algorithm_type": algorithm, "task_type": task,
                  "model_file": str(spec["model_file"]),
                  "input_width": int(spec.get("input_width") or 640),
                  "input_height": int(spec.get("input_height") or 640)})
    if entry.get("labels"):
        pass  # 页面上的「支持类别」优先于标签文件
    elif spec.get("labels_file"):
        labels_path = _resolve(str(spec["labels_file"]))
        if not labels_path.is_file():
            raise DetectorError("detector_create_labels_missing")
        entry["labels_file"] = str(spec["labels_file"])
    elif entry["algorithm_type"] in ("dfine", "rfdetr"):
        raise DetectorError("detector_create_labels_required")
    original = config_path().read_text(encoding="utf-8")
    raw["detectors"].append(entry)
    _write_raw(raw)
    _reload_or_rollback(original, model_id)
    return list_models()


def delete_spec(model_id: str) -> list[dict]:
    """删除一个检测器：只改配置，不碰权重文件。"""
    raw = _read_raw()
    kept = [d for d in raw["detectors"] if not (isinstance(d, dict) and d.get("id") == model_id)]
    if len(kept) == len(raw["detectors"]):
        raise DetectorError("detector_not_found")
    original = config_path().read_text(encoding="utf-8")
    raw["detectors"] = kept
    _write_raw(raw)
    try:
        _load_all(force=True)
    except DetectorError:
        config_path().write_text(original, encoding="utf-8")
        _load_all(force=True)
        raise
    return list_models()


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
        labels = item.get("labels") or []
        for p in run["predictions"]:
            label = p["label"]
            candidates.append({"box": p["box"], "label": label, "score": p["score"],
                               "model_id": run["id"], "model_name": item["name"],
                               "label_index": labels.index(label) if label in labels else None})
        runs.append({"id": run["id"], "name": item["name"],
                     "algorithm_type": item["algorithm_type"],
                     "model_sha256": item["model_sha256"],
                     "candidate_count": len(run["predictions"])})
    return {"candidates": candidates, "model_runs": runs}


def detect(image, model_ids: list[str], min_confidence: float = 0.35) -> dict:
    """在一张 BGR 图上按所选检测器依次推理；输出只含业务字段。"""
    models = _load_all()
    if not _infer_lock.acquire(blocking=False):
        raise DetectorError("detector_busy")
    try:
        return _detect_locked(image, model_ids, min_confidence, models)
    finally:
        _infer_lock.release()


def _detect_locked(image, model_ids: list[str], min_confidence: float, models: dict) -> dict:
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
