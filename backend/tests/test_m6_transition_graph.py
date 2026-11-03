import pytest

from app.m6_transition_graph import (load_graph, new_transition, record_transition,
                                     revoke, save_graph, support_count,
                                     transition_time_summary, transitions_between, usable)


def _graph():
    return {"transitions": []}


def _verified():
    return new_transition("2-2", "2-1", source_track="111", target_track="37",
                          observed_gap_seconds=4.0, clock_quality="uncalibrated",
                          movement_mode="electric_bicycle",
                          review_state="human_confirmed",
                          evidence=["tracks:2-2#111", "tracks:2-1#37"])


def test_record_and_read_single_transition():
    graph = _graph()
    record_transition(graph, _verified())
    hits = transitions_between(graph, "2-2", "2-1")
    assert len(hits) == 1
    assert hits[0]["target_track"] == "37"


def test_unknown_pair_returns_empty_not_unreachable():
    graph = _graph()
    record_transition(graph, _verified())
    assert transitions_between(graph, "2-2", "1-1") == []


def test_single_sample_is_not_a_probability():
    graph = _graph()
    record_transition(graph, _verified())
    summary = transition_time_summary(graph, "2-2", "2-1")
    assert summary["status"] == "insufficient_support"
    assert summary["support_count"] == 1
    assert "probability" not in summary


def test_same_track_counted_once():
    graph = _graph()
    record_transition(graph, _verified())
    record_transition(graph, _verified())
    assert support_count(graph, "2-2", "2-1") == 1


def test_revoke_invalidates_dependent_records():
    graph = _graph()
    base = record_transition(graph, _verified())
    derived = new_transition("2-1", "1-1", source_track="37", target_track="225",
                             review_state="pending_review")
    derived["depends_on"] = base["transition_id"]
    record_transition(graph, derived)
    revoke(graph, base["transition_id"], "human_revoked_association")
    assert transitions_between(graph, "2-2", "2-1") == []
    assert transitions_between(graph, "2-1", "1-1") == []
    audit = transitions_between(graph, "2-2", "2-1", include_revoked=True)
    assert audit[0]["revoked_reason"] == "human_revoked_association"


def test_rejected_records_are_not_usable():
    graph = _graph()
    record = _verified()
    record["review_state"] = "human_rejected"
    record_transition(graph, record)
    assert usable(record) is False
    assert transitions_between(graph, "2-2", "2-1") == []


def test_save_and_load_roundtrip(tmp_path):
    graph = _graph()
    record_transition(graph, _verified())
    path = tmp_path / "graph.json"
    save_graph(graph, path)
    again = load_graph(path)
    assert support_count(again, "2-2", "2-1") == 1


def test_invalid_state_rejected():
    graph = _graph()
    record = _verified()
    record["review_state"] = "auto_confirmed"
    with pytest.raises(ValueError):
        record_transition(graph, record)
