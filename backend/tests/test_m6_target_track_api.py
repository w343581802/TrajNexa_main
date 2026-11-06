import pytest


def test_target_track_sample_endpoint(env):
    client, _, _ = env
    response = client.get("/api/v1/target-track/sample")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["persisted"] is False
    assert data["identity_status"] in ("candidate_unverified", "ambiguous")
    assert data["summary"]["segments"] >= 1
    assert data["summary"]["projected"] >= 1
    point = data["segments"][0]["points"][0]
    assert point["evidence"]["track_id"]
    assert point["evidence"]["clip"]
    assert data["playback"]["clip_id"]
    first_point = data["segments"][0]["points"][0]
    assert first_point["evidence"]["offset_seconds"] == pytest.approx(
        first_point["evidence"]["day_seconds"] - 54000.0)
    assert first_point["evidence"]["time_quality"] == "uncalibrated_osd_ocr"


def test_ambiguity_probe_is_flagged_synthetic(env):
    client, _, _ = env
    response = client.get("/api/v1/target-track/sample?variant=ambiguity-probe")
    assert response.status_code == 200
    data = response.json()
    assert data["synthetic"] is True
    assert data["identity_status"] == "ambiguous"
    assert data["bind_evidence"]["ambiguous"] is True
    assert data["bind_evidence"]["runner_up"]
