"""B7-C0：VLM 属性抽取（防答案泄漏、人/场景双裁剪、逐字段来源）。

只产出候选证据，不参与 OSNet 排序、不确认身份。三条硬约束：
  1. 提示词里不放任何目标答案，字段值一律留空，避免模型照抄示例；
  2. 衣着用人体裁剪，车辆/同行用场景上下文裁剪，错位字段直接丢弃；
  3. 每个字段记录实际使用的裁剪区域和"模型声称"标记，visible=true 不是核验通过。
"""
from __future__ import annotations

import cv2
import json
import os
import re
import time
from urllib.parse import urlsplit

from app import m4_vision
from app.config import settings
from app import model_capabilities

PERSON_FIELDS = ("garment_color", "garment_type")
CONTEXT_FIELDS = ("vehicle_color", "vehicle_type", "companion_present")
FIELDS = PERSON_FIELDS + CONTEXT_FIELDS

PROMPT_PERSON = (
    "这是监控画面中行人的裁剪。只描述画面里确实看得见的衣着，输出一行 JSON：\n"
    '{"garment_color":{"value":"","visible":false},'
    '"garment_type":{"value":"","visible":false}}\n'
    "value 填中文短语（颜色、衣物类别）；看不清或不在画面里就保持空字符串并 visible=false；"
    "不要猜身份、年龄、性别、姓名，不要解释，只输出 JSON。"
)

PROMPT_CONTEXT = (
    "这是监控画面中目标及其周围环境的裁剪。只描述画面里确实看得见的车辆和同行情况，输出一行 JSON：\n"
    '{"vehicle_color":{"value":"","visible":false},'
    '"vehicle_type":{"value":"","visible":false},'
    '"companion_present":{"value":"","visible":false}}\n'
    "只有画面里确实出现与目标同行的人（例如同乘、牵手）才填 companion_present；"
    "没有车辆或同行者就保持空值并 visible=false；不要猜身份，只输出 JSON。"
)

PROMPT_VERSION = "b7c0-v3"
# 裁剪太小（远处目标）时模型只能瞎猜，低于这个尺寸直接记未知。
MIN_CROP = {"person": (32, 48), "context": (64, 48)}
# 编码前放大到至少这个宽度，最多放大 4 倍，避免糊成一团。
TARGET_WIDTH = {"person": 192, "context": 256}
EXTERNAL_FLAG = "TRAJNEXA_VLM_EXTERNAL_UPLOAD"


class AttributeError_(ValueError):
    """稳定错误码。"""


def _clamp(image, box):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = [int(round(float(v))) for v in box]
    return [max(0, min(width, x1)), max(0, min(height, y1)),
            max(0, min(width, x2)), max(0, min(height, y2))]


def _person_region(image, box):
    height, width = image.shape[:2]
    x1, y1, x2, y2 = _clamp(image, box)
    pad_x = max(4, int((x2 - x1) * 0.12))
    pad_y = max(4, int((y2 - y1) * 0.08))
    return [max(0, x1 - pad_x), max(0, y1 - pad_y),
            min(width, x2 + pad_x), min(height, y2 + pad_y)]


def _context_region(image, box):
    """人车关系通常在人体框下缘两侧，向左右和下方扩展，保留上方少量背景。"""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = _clamp(image, box)
    span_x = x2 - x1
    span_y = y2 - y1
    left = max(0, x1 - int(span_x * 0.9))
    right = min(width, x2 + int(span_x * 0.9))
    top = max(0, y1 - int(span_y * 0.15))
    bottom = min(height, y2 + int(span_y * 0.35))
    return [left, top, right, bottom]


def _crop(image, region, scope):
    x1, y1, x2, y2 = region
    min_w, min_h = MIN_CROP[scope]
    if x2 - x1 < min_w or y2 - y1 < min_h:
        raise AttributeError_("attribute_crop_too_small")
    crop = image[y1:y2, x1:x2]
    width = crop.shape[1]
    if width < TARGET_WIDTH[scope]:
        scale = min(4.0, TARGET_WIDTH[scope] / float(width))
        crop = cv2.resize(crop, (int(round(crop.shape[1] * scale)),
                                 int(round(crop.shape[0] * scale))),
                          interpolation=cv2.INTER_CUBIC)
    return crop


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


def _chat(entry: dict, prompt: str, jpeg: bytes) -> str:
    provider = entry.get("provider")
    model = entry.get("version") or entry.get("model")
    if provider == "ollama":
        return m4_vision._chat(settings.vision_base_url, model, prompt, jpeg,
                               float(getattr(settings, "vision_timeout_seconds", 120)))
    if provider == "openai_compatible":
        from app import m5_vision_transport

        return m5_vision_transport.remote_chat(entry, prompt, jpeg,
                                               float(getattr(settings, "vision_timeout_seconds", 120)))
    raise AttributeError_("attribute_provider_unsupported")


def _parse(text: str, allowed_fields) -> dict:
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
    for name in allowed_fields:
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


def _run_scope(image, region, scope, prompt, allowed_fields, plan, mode):
    jpeg = m4_vision._encode_jpeg(_crop(image, region, scope))
    results = []
    chosen = None
    for entry in plan:
        _allowed(entry)
        call_started = time.time()
        try:
            parsed = _parse(_chat(entry, prompt, jpeg), allowed_fields)
        except (AttributeError_, m4_vision.VisionError) as exc:
            results.append({"model": entry["model"], "version": entry["version"],
                            "error": str(exc),
                            "cost_ms": round((time.time() - call_started) * 1000.0, 1)})
            continue
        results.append({"model": entry["model"], "version": entry["version"],
                        "attributes": parsed["attributes"], "evidence": parsed["evidence"],
                        "cost_ms": round((time.time() - call_started) * 1000.0, 1)})
        chosen = len(results) - 1
        if mode != "parallel":
            break
    return {"results": results, "chosen": chosen}


def extract(image, box, task: str | None = None, mode: str | None = None) -> dict:
    """双裁剪抽取：人体裁剪给衣着，场景裁剪给车辆与同行关系。"""
    started = time.time()
    person_region = _person_region(image, box)
    context_region = _context_region(image, box)
    attributes = {name: None for name in FIELDS}
    evidence = {name: False for name in FIELDS}
    sources = {}
    scopes = []
    plan = model_capabilities.plan_models("attribute_extraction", mode, task)
    for entry in plan:
        _allowed(entry)
    if not plan:
        raise AttributeError_("attribute_model_unavailable")

    for scope, region, prompt, fields in (
            ("person", person_region, PROMPT_PERSON, PERSON_FIELDS),
            ("context", context_region, PROMPT_CONTEXT, CONTEXT_FIELDS)):
        try:
            outcome = _run_scope(image, region, scope, prompt, fields, plan, mode)
        except (AttributeError_, m4_vision.VisionError) as exc:
            scopes.append({"scope": scope, "region": region,
                           "calls": [], "error": str(exc)})
            continue
        scopes.append({"scope": scope, "region": region, "chosen": outcome["chosen"],
                       "calls": outcome["results"]})
        chosen = outcome["chosen"]
        if chosen is None:
            continue
        item = outcome["results"][chosen]
        for name in fields:
            attributes[name] = item["attributes"].get(name)
            evidence[name] = bool(item["evidence"].get(name))
            if evidence[name]:
                sources[name] = {"crop": scope, "region": region,
                                 "model": item["model"], "version": item["version"],
                                 "verification": "model_claim"}

    total = round((time.time() - started) * 1000.0, 1)
    return {"prompt_version": PROMPT_VERSION,
            "mode": mode or model_capabilities.strategy_for("attribute_extraction", task),
            "attributes": attributes, "evidence": evidence, "sources": sources,
            "scopes": scopes,
            "cost_ms": total, "identity_effect": "none"}
