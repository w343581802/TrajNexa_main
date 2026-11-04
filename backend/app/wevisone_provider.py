"""B7-C1：WeVisOne-VL 本机指代定位 Provider（只做 observe_only 观察）。

- 走官方 `wevisone_vl.engine.WeVisOneVL`，不联网、不再包一层 HTTP；
- 输出只有框和"模型声称"，没有置信分数，不建立身份；
- 依赖缺失时明确报 wevisone_not_installed，不返回空框冒充"没找到"。
"""
from __future__ import annotations

import importlib.util
import time

import numpy as np

MODELS = {
    "wevisone_vl_2b": "tencent/WeVisOne-VL-2B",
    "wevisone_vl_4b": "tencent/WeVisOne-VL-4B",
}

_ENGINES: dict = {}


class WeVisOneError(ValueError):
    """稳定错误码。"""


def installed() -> bool:
    return importlib.util.find_spec("wevisone_vl") is not None


def _load_engine(entry: dict):
    model_id = entry.get("version") or MODELS.get(entry["model"], "")
    if not model_id:
        raise WeVisOneError("wevisone_model_unknown")
    if model_id in _ENGINES:
        return _ENGINES[model_id]
    from wevisone_vl.engine import WeVisOneVL

    engine = WeVisOneVL(model_id, backend="transformers")
    _ENGINES[model_id] = engine
    return engine


def _as_rgb(image):
    from PIL import Image

    if isinstance(image, np.ndarray):
        array = image[:, :, ::-1] if image.ndim == 3 else image
        return Image.fromarray(np.ascontiguousarray(array)).convert("RGB")
    return image.convert("RGB")


def ground(image, query: str, entry: dict, max_new_tokens: int = 512) -> dict:
    """指代定位：返回候选框与"模型声称"状态，不参与排序、不建立身份。"""
    from app.wevisone_grounding import parse_referring_response, referring_prompt

    if not isinstance(query, str) or not query.strip():
        raise WeVisOneError("wevisone_query_missing")
    if not installed():
        raise WeVisOneError("wevisone_not_installed")
    prompt = referring_prompt(query)
    engine = _load_engine(entry)
    rgb = _as_rgb(image)
    started = time.time()
    generated = engine.predict(rgb, task="referring", query=query,
                               decode_matting=False, max_new_tokens=max_new_tokens)
    latency = round(time.time() - started, 3)
    parsed = parse_referring_response(generated.response, rgb.width, rgb.height)
    return {
        "status": parsed["status"], "boxes": parsed["boxes"],
        "score": None, "identity_established": False, "review_required": True,
        "model": entry["model"], "version": entry.get("version"), "prompt": prompt,
        "query": query, "latency_s": latency,
        "raw_response": (generated.response or "")[:2000],
        "identity_effect": "none", "affects_tracking": False,
    }
