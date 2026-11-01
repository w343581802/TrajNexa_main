"""P1-A experiment contract: person-only, no case writes or fake identity claims."""
import io

import cv2
import numpy as np
import pytest
from sqlalchemy import select, func

from app import detectors, m6_person_reid
from app.config import settings
from app.models import MediaAsset, TargetObservation


def photo(width, height):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[10:height-10, 20:width-20] = 180
    ok, enc = cv2.imencode(".png", image)
    assert ok
    return enc.tobytes()


def send(client, **form):
    return client.post("/api/v1/ai/model-test/reid-search", files={
        "reference_file": ("ref.png", photo(160, 120), "image/png"),
        "search_file": ("search.png", photo(240, 180), "image/png"),
    }, data={"reference_bbox_normalized": "[0.1,0.1,0.9,0.9]",
              "detector_model_id": "test-person", **form})


class FakeReID:
    def extract_embeddings(self, image, boxes):
        vecs = np.zeros((len(boxes), 32), np.float32)
        if image.shape[1] == 160:
            vecs[0, 0] = 1.
        else:
            vecs[0, 0] = .9; vecs[0, 1] = .1
            if len(boxes) > 1:
                vecs[1, 0] = .1; vecs[1, 1] = .9
        return list(range(len(boxes))), vecs


@pytest.fixture
def setup_models(monkeypatch):
    monkeypatch.setattr(detectors, "list_models",
                        lambda: [{"id": "test-person", "name": "Fake detector"}])
    monkeypatch.setattr(detectors, "detect_flat",
                        lambda image, model_ids, conf: {
        "candidates": [
            {"box": [20, 15, 110, 170], "label": "人", "score": .9,
             "label_index": 0, "model_id": "test-person", "model_name": "Test"},
            {"box": [120, 20, 220, 160], "label": "人", "score": .75,
             "label_index": 0, "model_id": "test-person", "model_name": "Test"},
            {"box": [15, 30, 55, 100], "label": "汽车", "score": .95,
             "label_index": 2, "model_id": "test-person", "model_name": "Test"},
        ], "model_runs": [{"id": "test-person", "candidate_count": 3}]})
    monkeypatch.setattr(m6_person_reid, "_get_engine",
                        lambda: (FakeReID(), "0123456789abcdef" * 4))


def test_person_search_ranks_candidates_not_vehicles_and_writes_nothing(env, setup_models):
    client, Session, _ = env
    response = send(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["test_only"] is True
    assert body["identity_established"] is False
    assert body["case_id"] is None
    assert body["person_candidate_count"] == 2
    assert len(body["ranking"]) == 2
    assert body["ranking"][0]["rank"] == 1
    assert body["ranking"][0]["cosine_similarity"] > body["ranking"][1]["cosine_similarity"]
    assert [x["status"] for x in body["ranking"]] == [
        "unverified_similarity", "unverified_similarity"]
    assert not any(x["detector_label"] == "汽车" for x in body["ranking"])
    assert "embedding" not in str(body).lower()
    with Session() as db:
        assert db.scalar(select(func.count(MediaAsset.id))) == 0
        assert db.scalar(select(func.count(TargetObservation.id))) == 0


def test_reference_box_rejected_if_too_small_or_invalid(env, setup_models):
    client, _, _ = env
    for box in ('[0,0,0.01,0.01]', '[1,0,0,1]', '["bad",0,1,1]'):
        response = send(client, reference_bbox_normalized=box)
        assert response.status_code == 422, response.text


def test_unknown_detector_rejected(env, setup_models):
    client, _, _ = env
    response = send(client, detector_model_id="not-installed")
    assert response.status_code == 422, response.text


def test_reid_missing_weight_never_returns_faked_similarity(env, setup_models, monkeypatch):
    client, _, _ = env
    # Use real resolver, which requires explicit local model path.
    monkeypatch.setattr(m6_person_reid, "_get_engine",
                        lambda: m6_person_reid._model_path())
    monkeypatch.setattr(settings, "m6_person_reid_model_path", "")
    response = send(client)
    assert response.status_code == 503, response.text
    assert "cosine_similarity" not in response.text


def test_no_person_candidates_produce_empty_unknown_result(env, setup_models, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(detectors, "detect_flat", lambda *args: {
        "candidates": [{"box": [30, 30, 150, 150], "label": "汽车",
                        "label_index": 2, "score": .9}],
        "model_runs": [{"id": "test-person", "candidate_count": 1}]})
    response = send(client)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_person_candidates"
    assert response.json()["ranking"] == []


def test_person_model_path_must_stay_in_backend_models(monkeypatch):
    monkeypatch.setattr(settings, "m6_person_reid_model_path", "../secret.onnx")
    with pytest.raises(m6_person_reid.ReIDError, match="m6_reid_model_path_invalid"):
        m6_person_reid._model_path()
