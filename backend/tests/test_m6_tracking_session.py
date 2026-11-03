import pytest

from app.m6_review_events import ReviewEventError, review_event
from app.m6_tracking_session import TrackingSession


def _session(target="purple"):
    return TrackingSession("case", target, {"observation_id": "A", "camera": "2-2",
                                            "observed_start": 54006.2, "observed_end": 54008.6})


def _cand(cid, score, state="candidate", camera="2-1"):
    return {"candidate_id": cid, "camera_label": camera, "retrieval_score": score,
            "margin_top1_top2": 0.1, "state": state, "observed_start": 54012.6,
            "observed_end": 54015.6, "evidence_refs": ["frame:2-1/0074.jpg"]}


def _event_for(kind, cid, case="case", target="purple"):
    return review_event(kind, case, target, cid, "case-owner", "2026-11-03T12:00:00Z",
                        evidence=["frame:2-1/0074.jpg"], source="human_review",
                        reason="manual")

def _event(kind, reviewer="case-owner", source="human_review", evidence=None):
    return review_event(kind, "case", "purple", "B1", reviewer, "2026-11-03T12:00:00Z",
                        evidence=evidence if evidence is not None else ["frame:2-1/0074.jpg"],
                        source=source, reason="manual")


def test_only_reviewed_candidate_enters_main_chain():
    s = _session()
    s.observe([_cand("B1", 0.72), _cand("B2", 0.66)], from_anchor_id="A")
    assert s.summary()["main_chain"] == ["A"]
    s.confirm("B1", _event("confirm"))
    assert s.summary()["main_chain"] == ["A", "B1"]
    states = {c["id"]: c["state"] for c in s.summary()["candidates"]}
    assert states["B2"] == "candidate"        # 未审核的候选留在池里


def test_confirmation_requires_human_source():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    with pytest.raises(ReviewEventError):
        s.confirm("B1", _event("confirm", source="simulated"))
    assert s.summary()["main_chain"] == ["A"]


def test_reject_keeps_searching_other_candidates():
    s = _session()
    s.observe([_cand("B1", 0.72), _cand("B2", 0.70)], from_anchor_id="A")
    s.reject("B1", _event("reject"))
    assert s.status == "running"
    assert s.best()["candidate_id"] == "B2"


def test_weak_candidate_neither_disappears_nor_auto_confirms():
    s = _session()
    s.observe([_cand("B1", 0.733, "weak_candidate")], from_anchor_id="A")
    assert s.summary()["main_chain"] == ["A"]
    assert s.best()["candidate_id"] == "B1"
    s.confirm("B1", _event("confirm"))
    assert s.summary()["main_chain"] == ["A", "B1"]


def test_confirm_b_plans_c_and_stays_unresolved_without_c_data():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    s.confirm("B1", _event("confirm"))
    plan = s.plan_next(lambda anchor: {"top5": [{"camera": "1-1"}]})
    assert plan["top5"][0]["camera"] == "1-1"
    empty = s.plan_next(lambda anchor: {"top5": []})
    assert empty["top5"] == []
    assert s.status == "unresolved"          # 没有 C 真值 ≠ 目标不存在


def test_revoke_b_invalidates_everything_downstream():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    s.confirm("B1", _event("confirm"))
    s.observe([_cand("C1", 0.70, camera="1-1")], from_anchor_id="B1")
    s.confirm("C1", _event_for("confirm", "C1"))
    s.observe([_cand("D1", 0.69, camera="1-2")], from_anchor_id="C1")
    cascade = s.revoke("B1", reason="wrong_person")["observations"]
    assert "C1" in cascade and "D1" in cascade
    assert s.summary()["main_chain"] == ["A"]
    states = {c["id"]: c["state"] for c in s.summary()["candidates"]}
    assert states["C1"] == "revoked" and states["D1"] == "revoked"


def test_targets_do_not_share_sessions():
    purple = _session("purple")
    cyan = _session("cyan")
    purple.observe([_cand("B1", 0.72)], from_anchor_id="A")
    purple.confirm("B1", _event("confirm"))
    assert cyan.summary()["main_chain"] == ["A"]
    assert cyan.summary()["candidates"] == []


def test_budget_exhaustion_is_not_absence():
    s = _session()
    s.budget_exhausted()
    assert s.status == "budget_exhausted"
    s.pause("operator")
    assert s.status == "paused"
    s.resume()
    assert s.status == "running"


def test_revoke_cancels_derived_scheduler_actions_only():
    s = _session()
    s.observe([_cand("B1", 0.72), _cand("B9", 0.60)], from_anchor_id="A")
    s.confirm("B1", _event("confirm"))
    s.register_actions("B1", ["58#1", "37#2"])
    s.register_actions("A", ["39#1"])          # 独立于 B 的分支
    result = s.revoke("B1", reason="wrong_person")
    assert set(result["actions"]) == {"58#1", "37#2"}
    states = {a["action_id"]: a["state"] for a in s.derived_actions}
    assert states["58#1"] == "cancelled_derived"
    assert states["39#1"] == "scheduled"        # 其它分支不被误删


def test_settle_reports_awaiting_review_then_unresolved():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    assert s.settle([(54010.0, 54016.0)])["status"] == "awaiting_review"
    empty = _session()
    assert empty.settle([])["status"] == "unresolved"
    assert "without_confirmation" in empty.plan["reason"]


def test_settle_reports_budget_exhausted_with_remaining_actions():
    s = _session()
    s.budget_exhausted()
    result = s.settle([(54010.0, 54016.0)], remaining_actions=3)
    assert result["status"] == "budget_exhausted"
    assert s.plan["remaining_actions"] == 3


def test_confirmed_candidate_leaves_awaiting_review():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    s.confirm("B1", _event("confirm"))
    assert s.settle([])["status"] == "unresolved"
    assert [n["observation_id"] for n in s.main_chain] == ["A", "B1"]


def test_budget_state_wins_over_pending_review():
    s = _session()
    s.observe([_cand("C1", 0.61)], from_anchor_id="A")
    s.budget_exhausted()
    result = s.settle([(54020.0, 54026.0)], remaining_actions=2)
    assert result["status"] == "budget_exhausted"
    assert result["reason"] == "analysis_budget_exhausted_with_pending_review"


def test_unfinished_without_budget_is_paused_not_budget_exhausted():
    s = _session()
    result = s.settle([(54020.0, 54026.0)], remaining_actions=4, stop_reason=None)
    assert result["status"] == "paused"
    assert result["reason"] == "search_incomplete:no_budget_claim"


def test_paused_reason_keeps_scheduler_cause():
    s = _session()
    result = s.settle([], remaining_actions=1, stop_reason="max_actions_reached")
    assert result["status"] == "paused"
    assert result["reason"] == "search_incomplete:max_actions_reached"



def test_snapshot_restore_keeps_candidates_and_review_state():
    s = _session()
    s.observe([_cand("B1", 0.72), _cand("B2", 0.66)], from_anchor_id="A")
    s.confirm("B1", _event_for("confirm", "B1"))
    s.reject("B2", _event_for("reject", "B2"))
    s.register_actions("B1", ["58#1"])
    s.budget_exhausted()
    snap = s.snapshot()
    restored = TrackingSession.restore(snap)
    assert restored.case_id == s.case_id and restored.target_id == s.target_id
    states = {c["candidate_id"]: c["state"] for c in restored.candidates}
    assert states == {"B1": "human_confirmed", "B2": "human_rejected"}
    assert [n["observation_id"] for n in restored.main_chain] == ["A", "B1"]
    assert restored.derived_actions[0]["action_id"] == "58#1"
    assert restored.budget_hit is True
    again = TrackingSession.restore(restored.snapshot())
    assert [c["state"] for c in again.candidates] == [c["state"] for c in restored.candidates]


def test_review_of_other_case_or_target_is_rejected():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    with pytest.raises(ReviewEventError):
        s.confirm("B1", _event_for("confirm", "B1", case="another-case"))
    with pytest.raises(ReviewEventError):
        s.confirm("B1", _event_for("confirm", "B1", target="cyan"))
    with pytest.raises(ReviewEventError):
        s.confirm("B1", _event_for("confirm", "B9"))
    assert [n["observation_id"] for n in s.main_chain] == ["A"]
    assert s.candidates[0]["state"] == "candidate"


def test_revoked_candidate_cannot_be_confirmed_again():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    s.confirm("B1", _event_for("confirm", "B1"))
    s.observe([_cand("C1", 0.7)], from_anchor_id="B1")
    s.revoke("B1")
    with pytest.raises(ReviewEventError):
        s.confirm("B1", _event_for("confirm", "B1"))
    with pytest.raises(ReviewEventError):
        s.confirm("C1", _event_for("confirm", "C1"))


def test_double_confirm_is_rejected():
    s = _session()
    s.observe([_cand("B1", 0.72)], from_anchor_id="A")
    s.confirm("B1", _event_for("confirm", "B1"))
    with pytest.raises(ReviewEventError):
        s.confirm("B1", _event_for("confirm", "B1"))
