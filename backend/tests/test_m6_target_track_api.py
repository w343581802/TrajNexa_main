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
