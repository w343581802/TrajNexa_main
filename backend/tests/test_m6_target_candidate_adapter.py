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


def test_top_k_keeps_ranked_candidates_for_visual_review():
    scored = [
        {"track_id": "a", "retrieval_score": 0.82, "frame_no": 3, "bbox_pixels": [1, 2, 3, 4]},
        {"track_id": "b", "retrieval_score": 0.70, "frame_no": 5, "bbox_pixels": [5, 6, 7, 8]},
        {"track_id": "c", "retrieval_score": 0.61, "frame_no": 9, "bbox_pixels": [9, 10, 11, 12]},
        {"track_id": "d", "retrieval_score": 0.40},
    ]
    out = candidates_from_scores(scored, top_k=3)
    assert [c["track_id"] for c in out["target_candidates"]] == ["a", "b", "c"]
    assert [c["rank"] for c in out["target_candidates"]] == [1, 2, 3]
    assert out["target_candidates"][0]["state"] == "candidate"
    assert all(c["state"] == "weak_candidate" for c in out["target_candidates"][1:])
    assert out["target_candidates"][1]["bbox_pixels"] == [5, 6, 7, 8]


def test_top_k_default_keeps_previous_top1_behaviour():
    out = candidates_from_scores([{"track_id": "a", "retrieval_score": 0.9},
                                  {"track_id": "b", "retrieval_score": 0.7}])
    assert len(out["target_candidates"]) == 1
