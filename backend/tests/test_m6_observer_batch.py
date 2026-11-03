from app.m6_observer_batch import batch_evidence, quality_feedback, select_batch


def _members():
    return [{"camera": "58", "window": (54008.6, 54029.6), "rank": 1, "selected": True},
            {"camera": "37", "window": (54007.6, 54029.6), "rank": 2, "selected": True},
            {"camera": "39", "window": (54008.6, 54029.6), "rank": 3, "selected": False}]


def test_batch_selects_two_by_default_with_independent_windows():
    batch = select_batch(_members(), batch_size=2)
    assert [m["camera"] for m in batch["members"]] == ["58", "37"]
    assert batch["members"][0]["window"] != batch["members"][1]["window"]


def test_batch_size_is_configurable():
    batch = select_batch(_members(), batch_size=1)
    assert len(batch["members"]) == 1


def test_quality_feedback_boosts_only_when_a_route_is_blurry():
    batch = select_batch(_members(), batch_size=2)
    clear = quality_feedback({"58": {"band": "high", "basis": "measured"},
                              "37": {"band": "high", "basis": "measured"}},
                             batch["members"], {"mode": "prioritize"})
    assert clear["applied"] is False
    blurry = quality_feedback({"58": {"band": "low", "basis": "measured"},
                               "37": {"band": "high", "basis": "measured"}},
                              batch["members"], {"mode": "prioritize"})
    assert blurry["boost"] == ["37"]
    assert blurry["demote"] == ["58"]


def test_feedback_does_nothing_in_observe_only():
    batch = select_batch(_members(), batch_size=2)
    result = quality_feedback({"58": {"band": "low", "basis": "measured"}},
                              batch["members"], {"mode": "observe_only"})
    assert result["applied"] is False and result["boost"] == []


def test_batch_evidence_never_merges_across_cameras():
    batch = select_batch(_members(), batch_size=2)
    evidence = batch_evidence(batch, {"58": {"tracks": [{"track_id": 21}],
                                           "target_candidates": [{"track_id": 21}]},
                                      "37": {"tracks": [{"track_id": 4}],
                                             "target_candidates": []}})
    assert evidence["cross_camera_identity_merged"] is False
    assert [r["tracks"] for r in evidence["routes"]] == [[21], [4]]


def test_blurry_camera_stays_in_batch_evidence():
    batch = select_batch(_members(), batch_size=2)
    evidence = batch_evidence(batch, {"58": {"tracks": [{"track_id": 21}]}},
                              {"58": {"band": "low", "basis": "measured"}})
    assert len(evidence["routes"]) == 2
    assert evidence["routes"][0]["quality"]["band"] == "low"
