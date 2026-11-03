import pytest

from app.m6_transition_graph import (eligible_for_statistics, is_human_confirmed,
                                     load_graph, new_transition, record_transition, revoke,
                                     save_graph, support_count, transition_time_summary,
                                     transitions_between)


def _graph():
    return {"transitions": []}


def _confirmed(track="111", target="37", event="evt-1", gap=4.0, synthetic=False):
    return new_transition("2-2", "2-1", source_track=track, target_track=target,
                          observed_gap_seconds=gap, clock_quality="uncalibrated",
                          movement_mode="electric_bicycle", review_state="human_confirmed",
                          reviewer="case-owner", association_event_id=event,
                          target_id="purple-rider", evidence=["tracks:2-2#111"],
                          synthetic=synthetic)


def _pending(track="225"):
    return new_transition("2-1", "1-1", source_track=track, target_track="x",
                          observed_gap_seconds=3.0, review_state="pending_review",
                          association_event_id="evt-pending")


def test_record_and_read_single_transition():
    graph = _graph()
    record_transition(graph, _confirmed())
    assert len(transitions_between(graph, "2-2", "2-1")) == 1


def test_unknown_pair_is_empty_not_unreachable():
    graph = _graph()
    record_transition(graph, _confirmed())
    assert transitions_between(graph, "2-2", "1-1") == []


def test_pending_records_never_enter_statistics():
    graph = _graph()
    for _ in range(5):
        record_transition(graph, _pending())
    assert support_count(graph, "2-1", "1-1") == 0
    assert transition_time_summary(graph, "2-1", "1-1")["status"] == "insufficient_support"


def test_synthetic_records_never_enter_statistics():
    graph = _graph()
    record_transition(graph, _confirmed(synthetic=True))
    assert eligible_for_statistics(graph["transitions"][0]) is False
    assert support_count(graph, "2-2", "2-1") == 0


def test_confirmation_requires_reviewer():
    graph = _graph()
    record = _confirmed()
    record["reviewer"] = None
    with pytest.raises(ValueError):
        record_transition(graph, record)


def test_same_track_id_in_two_cameras_is_one_sample():
    graph = _graph()
    record_transition(graph, _confirmed(event="evt-1"))
    record_transition(graph, _confirmed(event="evt-1"))     # 同一确认事件重复写入
    assert support_count(graph, "2-2", "2-1") == 1


def test_distinct_events_count_separately():
    graph = _graph()
    record_transition(graph, _confirmed(event="evt-1"))
    record_transition(graph, _confirmed(event="evt-2", track="120"))
    assert support_count(graph, "2-2", "2-1") == 2


def test_single_sample_is_not_a_probability():
    graph = _graph()
    record_transition(graph, _confirmed())
    summary = transition_time_summary(graph, "2-2", "2-1")
    assert summary["status"] == "insufficient_support"
    assert "median_gap_seconds" not in summary


def test_multi_level_revoke_propagates_and_is_idempotent():
    graph = _graph()
    base = record_transition(graph, _confirmed())
    b = new_transition("2-1", "1-1", review_state="pending_review")
    b["depends_on"] = base["transition_id"]
    record_transition(graph, b)
    c = new_transition("1-1", "3-3", review_state="pending_review")
    c["depends_on"] = b["transition_id"]
    record_transition(graph, c)
    d = new_transition("3-3", "4-4", review_state="pending_review")
    d["depends_on"] = c["transition_id"]
    record_transition(graph, d)

    revoke(graph, base["transition_id"], "human_revoked_association")
    assert all(r["review_state"] == "revoked" for r in graph["transitions"])
    revoke(graph, base["transition_id"], "again")            # 幂等
    assert len([a for a in graph["audit"] if a["action"] == "revoked"]) >= 4


def test_revoked_records_are_kept_for_audit():
    graph = _graph()
    base = record_transition(graph, _confirmed())
    revoke(graph, base["transition_id"], "wrong_person")
    audit = transitions_between(graph, "2-2", "2-1", include_revoked=True)
    assert audit and audit[0]["revoked_reason"] == "wrong_person"


def test_rejected_records_not_statistics():
    graph = _graph()
    record = _confirmed()
    record["review_state"] = "human_rejected"
    record_transition(graph, record)
    assert is_human_confirmed(record) is False
    assert support_count(graph, "2-2", "2-1") == 0


def test_save_and_load_roundtrip(tmp_path):
    graph = _graph()
    record_transition(graph, _confirmed())
    path = tmp_path / "graph.json"
    save_graph(graph, path)
    assert support_count(load_graph(path), "2-2", "2-1") == 1
