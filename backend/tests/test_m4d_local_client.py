"""Local model endpoint validation and malformed response behavior."""
import json
from io import BytesIO

import pytest

from app.m4d_local_client import LocalChatClient, local_base_url


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:11434/v1",
    "http://example.com:11434/v1",
    "http://127.0.0.1:11434/other",
    "http://127.0.0.1:11434/v1?x=1",
    "http://user:password@localhost:11434/v1",
    "http://127.0.0.1/v1",
    "http://0.0.0.0:11434/v1",
    "http://192.168.1.10:11434/v1",
])
def test_reject_nonlocal_or_unexpected_endpoint(url):
    with pytest.raises(ValueError, match="m4d_local_endpoint_required"):
        local_base_url(url)


def test_accept_loopback_endpoint():
    assert local_base_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"


@pytest.mark.parametrize("response", [
    {},
    {"choices": []},
    {"choices": [None]},
    {"choices": [{"message": None}]},
])
def test_malformed_model_response_rejected(monkeypatch, response):
    class FakeResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, count):
            return json.dumps(response).encode("utf-8")

    class FakeOpener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:11434/v1/chat/completions"
            assert timeout == 5
            return FakeResponse()

    monkeypatch.setattr("app.m4d_local_client.build_opener", lambda *args: FakeOpener())
    client = LocalChatClient("http://127.0.0.1:11434/v1", 5)
    with pytest.raises(ValueError, match="m4d_invalid_model_response"):
        client.create(model="local-vision", messages=[])
