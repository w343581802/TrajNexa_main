"""Local-only OpenAI-compatible VLM transport for the opt-in M4d demo."""
from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler


def local_base_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost", "::1")
            or parts.username or parts.password or parts.query or parts.fragment
            or not parts.port):
        raise ValueError("m4d_local_endpoint_required")
    return value.rstrip("/")


class LocalChatClient:
    """Minimal client shape expected by review_jpeg; no SDK dependency."""

    def __init__(self, base_url: str, timeout: float):
        self.base_url = local_base_url(base_url)
        if not 0 < timeout <= 120:
            raise ValueError("m4d_invalid_timeout")
        self.timeout = timeout
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, *, model: str, messages: list):
        payload = json.dumps({"model": model, "messages": messages}).encode("utf-8")
        request = Request(self.base_url + "/chat/completions", data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
        # Do not route localhost evidence through environment-configured HTTP proxies.
        with build_opener(ProxyHandler({})).open(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise ValueError("m4d_model_http_error")
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("m4d_model_response_too_large")
        data = json.loads(raw)
        choices = [SimpleNamespace(
            finish_reason=row.get("finish_reason"),
            message=SimpleNamespace(content=row.get("message", {}).get("content"),
                                    refusal=row.get("message", {}).get("refusal")),
        ) for row in data["choices"][:1]]
        return SimpleNamespace(id=data.get("id"), usage=None, choices=choices)
