"""B7-B：VLM 属性抽取（Qwen3-VL via Ollama / OpenAI 兼容端点）。

只产出候选证据，不参与 OSNet 排序、不确认身份。每个字段都带 visible 标记：
模型没看见就写 false，解析时整条丢弃，避免把猜测写成属性。
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from urllib.parse import urlsplit

import cv2

from app import m4_vision
from app.config import settings
from app import model_capabilities

PROMPT = (
    "这是一段监控画面里目标的裁剪。只描述画面中确实看得见的内容，"
    "输出一行 JSON：\n"
    '{"garment_color":{"value":"浅蓝","visible":true},'
    '"garment_type":{"value":"外套","visible":true},'
    '"vehicle_color":{"value":"奶白","visible":true},'
    '"vehicle_type":{"value":"踏板电动车","visible":true},'
    '"companion_present":{"value":"车前有一个小孩","visible":true}}\n'
    "规则：看不清或画面里没有就写 visible=false、value 留空；"
    "不要猜身份、年龄、性别、姓名、车牌或品牌；不要解释，只输出 JSON。"
)

FIELDS = ("garment_color", "garment_type", "vehicle_color", "vehicle_type",
          "companion_present")
EXTERNAL_FLAG = "TRAJNEXA_VLM_EXTERNAL_UPLOAD"


class AttributeError_(ValueError):
    """稳定错误码。"""


def _crop(image, box):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [int(round(float(v))) for v in box]
    pad_x = max(4, int((x2 - x1) * 0.12))
    pad_y = max(4, int((y2 - y1) * 0.08))
    x1 = max(0, x1 - pad_x)
    y1 = max(0, y1 - pad_y)
    x2 = min(width, x2 + pad_x)
    y2 = min(height, y2 + pad_y)
    if x2 - x1 < 16 or y2 - y1 < 24:
        raise AttributeError_("attribute_crop_too_small")
    return image[y1:y2, x1:x2]


def _allowed(entry: dict) -> None:
    """外部 VLM 必须显式授权上传画面；本机 Ollama 不受此限。"""
    if entry.get("provider") == "ollama":
        return
    url = entry.get("api_url") or ""
    host = (urlsplit(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return
    if (os.environ.get(EXTERNAL_FLAG) or "").strip() not in ("1", "true", "yes"):
        raise AttributeError_("external_upload_not_allowed")


def _chat(entry: dict, jpeg: bytes) -> str:
    provider = entry.get("provider")
    model = entry.get("version") or entry.get("model")
    if provider == "ollama":
        return m4_vision._chat(settings.vision_base_url, model, PROMPT, jpeg,
                               float(getattr(settings, "vision_timeout_seconds", 120)))
    if provider == "openai_compatible":
        from app import m5_vision_transport

        return m5_vision_transport.remote_chat(entry, PROMPT, jpeg,
                                               float(getattr(settings, "vision_timeout_seconds", 120)))
    raise AttributeError_("attribute_provider_unsupported")


def _parse(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise AttributeError_("attribute_invalid_json")
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError):
        raise AttributeError_("attribute_invalid_json") from None
    if not isinstance(payload, dict):
        raise AttributeError_("attribute_invalid_json")
    attributes = {}
    evidence = {}
    for name in FIELDS:
        item = payload.get(name)
        if isinstance(item, str):
            value, visible = item.strip(), bool(item.strip())
        elif isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            visible = bool(item.get("visible")) and bool(value)
        else:
            value, visible = "", False
        attributes[name] = value or None
        evidence[name] = bool(visible)
    return {"attributes": attributes, "evidence": evidence}


def _complete(parsed: dict) -> bool:
    flags = parsed.get("evidence") or {}
    return bool(flags.get("garment_color") and flags.get("vehicle_color"))


def extract(image, box, task: str | None = None, mode: str | None = None) -> dict:
    """按能力策略抽取属性；cascade 在结果不完整时才调用大模型。"""
    crop = _crop(image, box)
    jpeg = m4_vision._encode_jpeg(crop)
    plan = model_capabilities.plan_models("attribute_extraction", mode, task)
    started = time.time()
    results = []
    chosen = None
    for index, entry in enumerate(plan):
        _allowed(entry)
        call_started = time.time()
        try:
            raw = _chat(entry, jpeg)
            parsed = _parse(raw)
        except (AttributeError_, m4_vision.VisionError) as exc:
            results.append({"model": entry["model"], "version": entry["version"],
                            "error": str(exc),
                            "cost_ms": round((time.time() - call_started) * 1000.0, 1)})
            continue
        results.append({"model": entry["model"], "version": entry["version"],
                        "attributes": parsed["attributes"], "evidence": parsed["evidence"],
                        "cost_ms": round((time.time() - call_started) * 1000.0, 1)})
        if chosen is None or _complete(parsed):
            chosen = len(results) - 1
        if mode != "parallel" and _complete(parsed):
            break
    total = round((time.time() - started) * 1000.0, 1)
    return {"mode": mode or model_capabilities.strategy_for("attribute_extraction", task),
            "results": results, "chosen": chosen, "cost_ms": total,
            "identity_effect": "none"}
