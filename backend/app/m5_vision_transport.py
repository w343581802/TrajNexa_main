"""M5 VLM transport for Rebucca-compatible remote OpenAI APIs and local Ollama.

Model names and endpoints are always resolved server-side. Never allow a
browser-provided URL/key, follow HTTP redirects, or log response contents.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from app import m4_vision


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def remote_chat(entry: dict, prompt: str, jpeg: bytes, timeout: float) -> str:
    if entry.get("provider") != "openai_compatible":
        raise m4_vision.VisionError("m5_invalid_provider")
    key = os.environ.get(entry["api_key_env"], "")
    if not key:
        raise m4_vision.VisionError("m5_remote_key_missing")
    url = entry["api_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": entry["model_id"], "temperature": 0,
        "max_tokens": 512, "stream": False,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url":
                "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")}},
        ]}],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST")
    # Never let machine-wide proxy settings redirect loopback or remote calls.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(req, timeout=min(120, max(1, timeout))) as response:
            raw = response.read(512 * 1024 + 1)
    except urllib.error.HTTPError:
        raise m4_vision.VisionError("m5_remote_http_error") from None
    except (TimeoutError, urllib.error.URLError, OSError):
        raise m4_vision.VisionError("m5_remote_unavailable") from None
    if len(raw) > 512 * 1024:
        raise m4_vision.VisionError("m5_remote_bad_response")
    try:
        data = json.loads(raw)
        choice = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError):
        raise m4_vision.VisionError("m5_remote_bad_response") from None
    if isinstance(choice, list):
        choice = "\n".join(str(x.get("text", "")) for x in choice if isinstance(x, dict))
    if not isinstance(choice, str) or not choice.strip():
        raise m4_vision.VisionError("m5_remote_empty_response")
    return choice.strip()


def chat_for(entry: dict):
    """Same signature as m4_vision._chat; allows existing parsers and ROI crop."""
    if entry["provider"] == "ollama":
        return m4_vision._chat
    if entry["provider"] == "openai_compatible":
        return lambda _base, _model, prompt, jpeg, timeout: remote_chat(
            entry, prompt, jpeg, timeout)
    raise m4_vision.VisionError("m5_invalid_provider")
