from app.m6_target_candidate_adapter import candidates_from_scores, target_candidates


def test_no_tracks_returns_empty():
    result = target_candidates({"tracks": []})
    assert result["status"] == "no_tracks"
    assert result["target_candidates"] == []


def test_missing_backend_is_not_no_target():
    result = target_candidates({"tracks": [{"track_id": "w:1"}]})
    assert result["status"] == "candidate_backend_missing"
    assert result["target_candidates"] == []
    assert "candidate_selection_unavailable" in result["uncertainties"]


def test_clear_leader_becomes_candidate():
    scored = [{"track_id": "w:20", "retrieval_score": 0.705},
              {"track_id": "w:25", "retrieval_score": 0.551}]
    gated = candidates_from_scores(scored)
    assert gated["target_candidates"][0]["state"] == "candidate"
    assert gated["margin"] == 0.154


def test_crowded_scores_are_weak_candidate_not_stop():
    scored = [{"track_id": "w:3", "retrieval_score": 0.628},
              {"track_id": "w:9", "retrieval_score": 0.603}]
    gated = candidates_from_scores(scored)
    assert gated["target_candidates"][0]["state"] == "weak_candidate"
    assert gated["target_candidates"][0]["unknown"] == ["spatiotemporal_unverified"]


def test_no_scored_tracks():
    assert candidates_from_scores([])["status"] == "no_scored_tracks"
