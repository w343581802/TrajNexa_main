"""B7-A：统一模型能力层。

四类能力各自有固定输入/输出契约，任务（案件/流程）通过配置选择具体模型。
业务代码只调用 resolve()/invoke()，换模型改配置即可，不需要改追踪流程。

设计约束（与 design/44 D.66、design/48 §15 一致）：
  1. 不重建 M5 模型管理：检测器读 config/detectors.json，视觉大模型读 M5 注册表；
  2. 版本可复现：每次分析记录模型 id + 权重 sha256 前缀；
  3. 失败回退：主模型不可用时按 fallbacks 顺序降级，并把 fallback_used 写进来源记录；
  4. 能力未接入不假装可用：attribute_extraction / visual_grounding 没有配置就报
     capability_model_unavailable，由调用方决定是否继续。
"""
from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

CAPABILITIES = {
    "object_detection": {
        "summary": "画面级目标检测",
        "input": ["image"],
        "output": ["candidates[]"],
    },
    "person_reid": {
        "summary": "单张图人像外观向量",
        "input": ["image", "box"],
        "output": ["vector[]"],
    },
    "attribute_extraction": {
        "summary": "结构化属性抽取（衣着、车辆、同行关系）",
        "input": ["image", "prompt"],
        "output": ["attributes{}"],
    },
    "visual_grounding": {
        "summary": "自然语言定位目标框",
        "input": ["image", "query"],
        "output": ["box[4]", "score"],
    },
}

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "model_capabilities.json"
# 部署/联调可以用环境变量指向另一份能力配置，追踪流程不需要改代码。
CONFIG_ENV = "TRAJNEXA_MODEL_CAPABILITIES"
_TASK_DEFAULT = "default"
_CACHE: dict = {"config_key": None, "config": None, "digest": {}}
_EXECUTIONS: list = []


class CapabilityError(ValueError):
    """稳定错误码，不返回权重路径等内部细节。"""


def _read_config(path=None) -> dict:
    if path is None:
        path = (os.environ.get(CONFIG_ENV) or "").strip() or None
    target = Path(path) if path else CONFIG_PATH
    try:
        key = (str(target), target.stat().st_mtime_ns)
    except OSError:
        raise CapabilityError("capability_config_missing") from None
    if _CACHE["config_key"] == key:
        return _CACHE["config"]
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        raise CapabilityError("capability_config_invalid") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("default"), dict):
        raise CapabilityError("capability_config_invalid")
    for name in payload["default"]:
        if name not in CAPABILITIES:
            raise CapabilityError("capability_unknown")
    _CACHE["config_key"] = key
    _CACHE["config"] = payload
    _CACHE["digest"] = {}
    return payload


def _file_digest(path: Path) -> str:
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return ""
    stamp = (key, stat.st_size, stat.st_mtime_ns)
    cached = _CACHE["digest"].get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    value = sha256(path.read_bytes()).hexdigest()
    _CACHE["digest"][key] = (stamp, value)
    return value


def _model_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _detector_models() -> dict:
    from app import detectors

    try:
        loaded = detectors._load_all()
    except Exception:
        return {}
    result = {}
    for model_id, item in loaded.items():
        result[model_id] = {
            "model": model_id,
            "provider": "onnxruntime",
            "name": item.get("name", model_id),
            "version": (item.get("model_sha256") or "")[:16],
        }
    return result


def _grounding_models() -> dict:
    config = _read_config()
    result = {}
    try:
        from app import wevisone_provider

        has_package = wevisone_provider.installed()
    except Exception:
        has_package = False
    for model_id, spec in (config.get("models") or {}).items():
        if spec.get("capability") != "visual_grounding":
            continue
        result[model_id] = {"model": model_id,
                            "provider": spec.get("provider", "wevisone_local"),
                            "name": spec.get("name", model_id),
                            "version": spec.get("weight", ""),
                            "installed": bool(has_package)}
    return result


def _vision_models() -> dict:
    from app.config import settings

    try:
        from app.m5_vision_registry import configured_vision_models

        models = configured_vision_models(settings)
    except Exception:
        return {}
    return {
        item["id"]: {
            "model": item["id"],
            "provider": item.get("provider", "ollama"),
            "name": item.get("name", item["id"]),
            "version": str(item.get("model_id", ""))[:64],
        }
        for item in models
    }


def available(capability: str) -> list[dict]:
    """某能力当前可选的模型（只列真实存在/已配置的，不列未来型号）。"""
    if capability not in CAPABILITIES:
        raise CapabilityError("capability_unknown")
    if capability == "object_detection":
        return list(_detector_models().values())
    if capability == "visual_grounding":
        return list(_grounding_models().values())
    if capability == "person_reid":
        config = _read_config()
        result = []
        for model_id, spec in (config.get("models") or {}).items():
            if spec.get("capability") != "person_reid":
                continue
            path = _model_root() / spec.get("path", "")
            if not path.is_file():
                continue
            result.append({"model": model_id, "provider": spec.get("provider", "onnxruntime"),
                           "name": spec.get("name", model_id),
                           "version": _file_digest(path)[:16], "path": spec.get("path")})
        return result
    return list(_vision_models().values())


def _pick(capability: str, model: str | None, config: dict, task: str | None) -> dict:
    task_spec = (config.get("tasks") or {}).get(task or _TASK_DEFAULT) or {}
    if model is None:
        model = task_spec.get(capability) or config["default"].get(capability)
    if not model or model == "none":
        raise CapabilityError("capability_model_unavailable")
    if capability == "visual_grounding":
        entry = _grounding_models().get(model)
        if entry is None:
            raise CapabilityError("capability_model_unavailable")
    elif capability in ("object_detection", "person_reid"):
        table = _detector_models() if capability == "object_detection" else {
            item["model"]: item for item in available("person_reid")}
        entry = table.get(model)
        if entry is None:
            raise CapabilityError("capability_model_unavailable")
    else:
        entry = _vision_models().get(model)
        if entry is None:
            raise CapabilityError("capability_model_unavailable")
    resolved = dict(entry)
    resolved["capability"] = capability
    resolved["fallback_used"] = False
    resolved["task"] = task or _TASK_DEFAULT
    if capability == "person_reid":
        spec = (config.get("models") or {}).get(model) or {}
        resolved["path"] = spec.get("path")
    return resolved


def resolve(capability: str, task: str | None = None, model: str | None = None) -> dict:
    """能力 → 具体模型。model 显式指定时优先，否则按任务覆盖、再按默认。"""
    config = _read_config()
    return _pick(capability, model, config, task)


def reset_executions() -> None:
    _EXECUTIONS.clear()


def executions() -> list:
    """本次运行真实执行过的模型（含回退结果），供 provenance 使用。"""
    return list(_EXECUTIONS)


def strategy_for(capability: str, task: str | None = None) -> str:
    config = _read_config()
    return ((config.get("strategies") or {}).get(capability)
            or config.get("strategy") or "small_only")


def plan_models(capability: str, mode: str | None = None,
                task: str | None = None) -> list:
    """按策略给出要执行的模型序列。

    small_only / large_only：只跑一个；
    cascade：小模型先跑，结果不完整再跑大模型（由调用方判断 stop）；
    parallel：两个都跑，各自保留结果；
    observe_only：主模型照常，影子模型只记录、不参与排序。
    """
    config = _read_config()
    mode = mode or strategy_for(capability, task)
    if mode not in ("small_only", "large_only", "cascade", "parallel", "observe_only"):
        raise CapabilityError("strategy_unknown")
    sizes = (config.get("sizes") or {}).get(capability) or {}
    small = sizes.get("small")
    large = sizes.get("large")
    primary = (config.get("tasks") or {}).get(task or _TASK_DEFAULT, {}).get(capability)
    primary = primary or config["default"].get(capability)
    if not small:
        small = primary
    return_mode = mode
    if return_mode == "small_only":
        return [resolve(capability, task=task, model=small)]
    if return_mode == "large_only":
        return [resolve(capability, task=task, model=large or primary)]
    return [resolve(capability, task=task, model=m) for m in (small, large or primary)]


def invoke(capability: str, payload: dict, task: str | None = None,
           model: str | None = None) -> dict:
    """按能力分派到真实实现；主模型失败时按 fallbacks 降级一次。"""
    config = _read_config()
    chain = [model] + list((config.get("fallbacks") or {}).get(capability) or [])
    primary = model or resolve(capability, task=task)["model"]
    last = None
    for index, candidate in enumerate(chain):
        resolved = resolve(capability, task=task, model=candidate)
        try:
            result = _dispatch(capability, resolved, payload)
            result["model"] = resolved["model"]
            result["model_version"] = resolved["version"]
            result["fallback_used"] = index > 0
            _EXECUTIONS.append({"capability": capability, "model": resolved["model"],
                                "version": resolved["version"],
                                "fallback_used": index > 0,
                                "fallback_from": primary if index > 0 else None})
            return result
        except CapabilityError:
            raise
        except Exception as exc:  # 权重/后端故障才回退，参数错误直接上抛
            last = exc
            continue
    raise CapabilityError("capability_invoke_failed") from last


def _dispatch(capability: str, resolved: dict, payload: dict) -> dict:
    if capability == "object_detection":
        from app import detectors

        image = payload.get("image")
        if image is None:
            raise CapabilityError("payload_invalid")
        result = detectors.detect_flat(image, [resolved["model"]],
                                       float(payload.get("min_confidence", 0.35)))
        return {"candidates": result["candidates"]}
    if capability == "visual_grounding":
        from app import wevisone_provider

        try:
            return wevisone_provider.ground(payload.get("image"), payload.get("query"),
                                            resolved)
        except wevisone_provider.WeVisOneError as exc:
            raise CapabilityError(str(exc)) from None
    if capability == "person_reid":
        from app import m6_person_reid

        image = payload.get("image")
        box = payload.get("box")
        if image is None or not box:
            raise CapabilityError("payload_invalid")
        engine, digest = m6_person_reid.engine_for(resolved.get("path") or "")
        vector = engine.extract_embeddings(image, [[int(v) for v in box]])[1][0]
        return {"vector": [float(v) for v in vector],
                "weights_digest": digest[:16]}
    raise CapabilityError("capability_not_wired")


def detector_id(task: str | None = None) -> str:
    """检测业务代码统一从这里取模型，避免把 dfine-n 写死在流程里。"""
    return resolve("object_detection", task=task)["model"]


def reid_model_path() -> str | None:
    """ReID 业务代码统一从这里取权重路径；未配置时返回 None 由调用方兜底。"""
    try:
        return resolve("person_reid")["path"]
    except CapabilityError:
        return None


def provenance(task: str | None = None) -> dict:
    """每次分析写进结果的模型来源记录，用于复现与审计。"""
    record = {}
    for capability in CAPABILITIES:
        try:
            item = resolve(capability, task=task)
        except CapabilityError as exc:
            record[capability] = {"model": None, "error": str(exc)}
            continue
        record[capability] = {"model": item["model"], "provider": item["provider"],
                              "version": item["version"], "task": item["task"]}
    executed = {}
    for event in _EXECUTIONS:
        bucket = executed.setdefault(event["capability"], [])
        same = next((item for item in bucket
                     if item["model"] == event["model"]
                     and item["fallback_used"] == event["fallback_used"]), None)
        if same is None:
            bucket.append({"model": event["model"], "version": event["version"],
                           "fallback_used": event["fallback_used"],
                           "fallback_from": event["fallback_from"], "count": 1})
        else:
            same["count"] += 1
    record["executed"] = executed
    record["strategies"] = {name: strategy_for(name) for name in CAPABILITIES}
    return record


def stamp(payload: dict, task: str | None = None) -> dict:
    """把来源记录挂到任意结果字典上，返回同一个对象。"""
    payload["model_provenance"] = provenance(task)
    return payload
