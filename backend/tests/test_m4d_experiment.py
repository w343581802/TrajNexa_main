"""Offline experiment orchestration tests; never contact a model server."""
from hashlib import sha256
from types import SimpleNamespace

import pytest

from app.m4d_experiment import (
    ExperimentAuthorizationError,
    ExperimentSource,
    run_readonly_experiment,
)

JPEG = b"\xff\xd8\xff" + b"offline-fixture" + b"\xff\xd9"


class FakeClient:
    def __init__(self, text):
        self.calls = []
        self.text = text
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="offline-request",
            usage=None,
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=self.text, refusal=None),
            )],
        )


def source(**kwargs):
    values = dict(case_id="case-test", media_id="media-test", frame_offset_ms=1200,
                  image_sha256=sha256(JPEG).hexdigest(), authorization_checked=True)
    values.update(kwargs)
    return ExperimentSource(**values)


def test_readonly_experiment_preserves_source_and_unverified_attributes():
    client = FakeClient('{"attributes":[{"name":"clothing_upper","state":"visible","value":"red"}]}')
    review = run_readonly_experiment(JPEG, source=source(), prompt="describe", model="test", client=client)
    assert review.requires_human_review is True
    assert review.source.case_id == "case-test"
    assert review.vlm.status == "completed"
    assert review.attributes.status == "parsed"
    assert review.attributes.candidates[0].source == "vlm_unverified"
    assert len(client.calls) == 1


@pytest.mark.parametrize("changes", [
    {"authorization_checked": False},
    {"case_id": ""},
    {"media_id": ""},
    {"frame_offset_ms": -1},
    {"frame_offset_ms": True},
    {"image_sha256": "0" * 64},
])
def test_rejects_unverified_source_before_model_call(changes):
    client = FakeClient('{"attributes":[]}')
    with pytest.raises(ExperimentAuthorizationError):
        run_readonly_experiment(JPEG, source=source(**changes), prompt="describe", model="test", client=client)
    assert client.calls == []


def test_malformed_model_output_stays_unverified():
    client = FakeClient("looks like the same person")
    review = run_readonly_experiment(JPEG, source=source(), prompt="describe", model="test", client=client)
    assert review.vlm.status == "completed"
    assert review.attributes.status == "invalid"
    assert not review.attributes.candidates
