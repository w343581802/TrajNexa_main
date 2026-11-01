"""Server-allowlisted visual models: local Ollama and Rebucca-style remote APIs.

Remote URLs and key environment-variable names are deployer-controlled settings,
NEVER accepted from the public model-test request. API catalog must omit endpoint
and key material. See m5_vision_transport.py for request handling.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit


class VisionRegistryError(ValueError):
    pass


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_KEY_ENV = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$")
DEFAULT_MODELS = (
    ("qwen3vl", "Qwen3-VL 2B", "vision_model"),
    ("qwen3vl4b", "Qwen3-VL 4B", "vision_model_large"),
)


def _remote_url(url: str) -> bool:
    """Only TLS remote or loopback OpenAI-compatible /v1 endpoints."""
    try:
        u = urlsplit(url)
        if not u.hostname or not u.port and u.scheme == "http":
            return False
        if (u.username or u.password or u.fragment or u.query or
                u.path.rstrip("/") not in ("/v1", "") or u.scheme not in ("http", "https")):
            return False
        if u.scheme == "http":
            return u.hostname in ("127.0.0.1", "localhost", "::1") and u.port is not None
        return len(url) <= 500
    except (TypeError, ValueError):
        return False


def configured_vision_models(settings) -> list[dict]:
    models = []
    ids = set()
    for model_id, name, attr in DEFAULT_MODELS:
        model_name = getattr(settings, attr, "")
        if isinstance(model_name, str) and model_name.strip():
            models.append({
                "id": model_id, "name": name, "provider": "ollama",
                "location": "local", "model_id": model_name.strip(),
                "enabled": bool(settings.vision_enabled),
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
            provider = item.get("provider", "ollama")
            if provider not in ("ollama", "openai_compatible"):
                raise VisionRegistryError("m5_invalid_vision_model_config")
            entry = {
                "id": model_id, "name": name.strip(), "provider": provider,
                "location": "local" if provider == "ollama" else "remote",
                "model_id": actual.strip(),
                "enabled": bool(settings.vision_enabled) if provider == "ollama" else True,
                "default_for_roi": actual.strip() == settings.vision_feature_model,
            }
            if provider == "openai_compatible":
                url, key_env = item.get("api_url"), item.get("api_key_env")
                if (not isinstance(url, str) or not _remote_url(url)
                        or not isinstance(key_env, str) or not _KEY_ENV.fullmatch(key_env)):
                    raise VisionRegistryError("m5_invalid_vision_model_config")
                # Only internal resolver uses these fields; never return them
                # to the browser catalog. Keys themselves live in process env.
                entry["api_url"] = url.rstrip("/")
                entry["api_key_env"] = key_env
                entry["location"] = "local" if urlsplit(url).hostname in (
                    "127.0.0.1", "localhost", "::1") else "remote"
            ids.add(model_id)
            models.append(entry)
    return models


def public_vision_models(settings) -> list[dict]:
    return [{
        k: entry[k] for k in (
            "id", "name", "provider", "location", "model_id", "enabled",
            "default_for_roi")
    } for entry in configured_vision_models(settings)]


def resolve_vision_model(settings, model_id: str) -> dict:
    if not isinstance(model_id, str) or not _ID.fullmatch(model_id):
        raise VisionRegistryError("m5_unknown_vision_model")
    for entry in configured_vision_models(settings):
        if entry["id"] == model_id:
            return entry
    raise VisionRegistryError("m5_unknown_vision_model")
