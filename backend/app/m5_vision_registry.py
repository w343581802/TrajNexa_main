"""Configured local vision-model catalog for M5 image experiments.

All models use the existing local Ollama transport in M4d for now. Additional
model IDs are allowlisted by server settings; clients cannot inject a URL or
arbitrary Ollama model name. Other provider adapters are future M5 work.
"""
from __future__ import annotations

import json
import re


class VisionRegistryError(ValueError):
    pass


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
DEFAULT_MODELS = (
    ("qwen3vl", "Qwen3-VL 2B", "vision_model"),
    ("qwen3vl4b", "Qwen3-VL 4B", "vision_model_large"),
)


def configured_vision_models(settings) -> list[dict]:
    models = []
    ids = set()
    for model_id, name, attr in DEFAULT_MODELS:
        model_name = getattr(settings, attr, "")
        if isinstance(model_name, str) and model_name.strip():
            models.append({
                "id": model_id, "name": name, "provider": "ollama",
                "model_id": model_name.strip(), "enabled": bool(settings.vision_enabled),
                "default_for_roi": model_name.strip() == settings.vision_feature_model,
            })
            ids.add(model_id)

    source = getattr(settings, "m5_vision_models_json", "") or ""
    if not isinstance(source, str) or len(source) > 12000:
        raise VisionRegistryError("m5_invalid_vision_model_config")
    if source.strip():
        try:
            extra = json.loads(source)
        except (json.JSONDecodeError, TypeError):
            raise VisionRegistryError("m5_invalid_vision_model_config") from None
        if not isinstance(extra, list) or len(extra) > 16:
            raise VisionRegistryError("m5_invalid_vision_model_config")
        for item in extra:
            if not isinstance(item, dict):
                raise VisionRegistryError("m5_invalid_vision_model_config")
            model_id, name, actual = (item.get("id"), item.get("name"),
                                       item.get("model"))
            if (not isinstance(model_id, str) or not _ID.fullmatch(model_id)
                    or model_id in ids or not isinstance(name, str)
                    or not 1 <= len(name.strip()) <= 80
                    or not isinstance(actual, str)
                    or not 1 <= len(actual.strip()) <= 160):
                raise VisionRegistryError("m5_invalid_vision_model_config")
            ids.add(model_id)
            models.append({
                "id": model_id, "name": name.strip(), "provider": "ollama",
                "model_id": actual.strip(), "enabled": bool(settings.vision_enabled),
                "default_for_roi": actual.strip() == settings.vision_feature_model,
            })
    return models


def resolve_vision_model(settings, model_id: str) -> dict:
    if not isinstance(model_id, str) or not _ID.fullmatch(model_id):
        raise VisionRegistryError("m5_unknown_vision_model")
    for entry in configured_vision_models(settings):
        if entry["id"] == model_id:
            return entry
    raise VisionRegistryError("m5_unknown_vision_model")
