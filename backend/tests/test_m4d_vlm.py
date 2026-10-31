"""Offline M4d adapter contract tests; no network/model/real video."""
from types import SimpleNamespace

import pytest

from app.m4d_vlm import VLMInputError, review_jpeg

JPEG = b"\xff\xd8\xff" + b"test-payload" + b"\xff\xd9"


class FakeClient:
    def __init__(self, *, text="visible red object", finish_reason="stop", refusal=None, error=None):
        self.calls = []
        self.text = text
        self.finish_reason = finish_reason
        self.refusal = refusal
        self.error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            id="test-request",
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 3}),
            choices=[SimpleNamespace(
                finish_reason=self.finish_reason,
                message=SimpleNamespace(content=self.text, refusal=self.refusal),
            )],
        )


def test_completed_is_unverified_text_not_identity_decision():
    client = FakeClient()
    result = review_jpeg(JPEG, prompt="Describe only visible details", model="test-vlm", client=client)
    assert result.status == "completed"
    assert result.model == "test-vlm"
    assert result.text == "visible red object"
    assert len(result.image_sha256) == 64
    assert result.request_id == "test-request"
    assert client.calls[0]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize("kwargs,code", [
    ({"image_bytes": b""}, "image_required"),
    ({"image_bytes": b"not a jpeg"}, "jpeg_required"),
    ({"image_bytes": JPEG, "prompt": " "}, "prompt_required"),
    ({"image_bytes": JPEG, "model": ""}, "model_required"),
    ({"image_bytes": JPEG, "max_image_bytes": 4}, "image_too_large"),
])
def test_invalid_input_never_calls_model(kwargs, code):
    client = FakeClient()
    params = dict(image_bytes=JPEG, prompt="describe", model="vlm", client=client)
    params.update(kwargs)
    with pytest.raises(VLMInputError, match=code):
        review_jpeg(**params)
    assert client.calls == []


def test_refusal_is_not_positive_observation():
    result = review_jpeg(JPEG, prompt="describe", model="vlm", client=FakeClient(refusal="cannot"))
    assert result.status == "refused" and result.text is None


def test_empty_response_is_not_success():
    result = review_jpeg(JPEG, prompt="describe", model="vlm", client=FakeClient(text=" "))
    assert result.status == "empty" and result.text is None


def test_sdk_error_does_not_leak_exception_message():
    result = review_jpeg(JPEG, prompt="describe", model="vlm", client=FakeClient(error=RuntimeError("secret-token")))
    assert result.status == "error"
    assert result.error_code == "RuntimeError"
    assert "secret-token" not in str(result)


def test_content_filter():
    result = review_jpeg(JPEG, prompt="describe", model="vlm", client=FakeClient(finish_reason="content_filter"))
    assert result.status == "refused"


def test_truncated_output_is_not_completed():
    result = review_jpeg(JPEG, prompt="describe", model="vlm", client=FakeClient(finish_reason="length"))
    assert result.status == "incomplete"
    assert result.text is None
    assert result.finish_reason == "length"


def test_unknown_finish_reason_is_not_completed():
    result = review_jpeg(JPEG, prompt="describe", model="vlm", client=FakeClient(finish_reason=None))
    assert result.status == "incomplete"


def test_prompt_hash_recorded_without_storing_prompt():
    result = review_jpeg(JPEG, prompt="describe", model="vlm", client=FakeClient())
    assert len(result.prompt_sha256) == 64
    assert result.finish_reason == "stop"
    assert "describe" not in str(result)
