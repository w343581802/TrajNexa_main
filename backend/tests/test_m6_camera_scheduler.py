import pytest

from app.m6_camera_scheduler import CameraScheduler, build_actions


def _available(camera, start, end):
    return {"available": True}


def _preds():
    return [
        {"camera": "1-1", "priority": "high", "estimated_window": (54007.6, 54029.6),
         "search_window": (54007.6, 54029.6), "exploratory": True,
         "uncertainties": ["fov_not_field_verified"]},
        {"camera": "2-1", "priority": "high", "estimated_window": (54008.6, 54029.6),
         "search_window": (54008.6, 54029.6), "exploratory": True, "uncertainties": []},
        {"camera": "2-2", "priority": "high", "estimated_window": (54008.6, 54029.6),
         "search_window": (54008.6, 54029.6), "exploratory": True, "uncertainties": []},
    ]


def _with_target(track_id=37, frames=30):
    return {"status": "ok", "tracks": [{"track_id": "w:%s" % track_id}],
            "target_candidates": [{"track_id": "w:%s" % track_id, "state": "candidate"}],
            "cost": {"frames_decoded": frames, "detector_frames": frames},
            "time_status": "absolute"}


def _people_only(frames=30):
    return {"status": "ok", "tracks": [{"track_id": "w:%d" % i} for i in range(5)],
            "target_candidates": [],
            "cost": {"frames_decoded": frames, "detector_frames": frames},
            "time_status": "absolute"}


def _missed(frames=30):
    return {"status": "detection_missed", "tracks": [], "target_candidates": [],
            "cost": {"frames_decoded": frames, "frames_with_person": 0,
                     "detector_frames": frames},
            "time_status": "absolute"}


def test_actions_are_per_camera_not_per_site():
    actions = build_actions(_preds())
    assert [a["camera"] for a in actions] == ["1-1", "2-1", "2-2"]
    assert len({a["action_id"] for a in actions}) == 3


def test_ranking_order_preserved():
    assert [a["rank"] for a in build_actions(_preds())] == [1, 2, 3]


def test_people_without_target_never_triggers_review():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    state = sched.submit(_people_only())
    assert state == "running"                       # 有人但没有目标候选，继续查
    assert sched.actions[0]["tracks_seen"] == 5
    assert sched.ledger["review_count"] == 0


def test_top1_without_target_continues_to_next_camera():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0, "allow_expand": False})
    nxt = sched.next_action()
    assert nxt["action"]["camera"] == "1-1"
    while nxt is not None and nxt["action"]["camera"] == "1-1":
        sched.submit(_people_only())
        nxt = sched.next_action()
    assert nxt is not None and nxt["action"]["camera"] == "2-1"


def test_target_candidate_enters_review_but_is_not_confirmed():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    assert sched.submit(_with_target(), appearance_score=0.93) == "needs_review"
    assert sched.ledger["review_count"] == 1
    with pytest.raises(ValueError):
        sched.confirm_hop(human=False)


def test_confirmation_needs_evidence_or_explicit_simulation():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_with_target())
    with pytest.raises(ValueError):
        sched.confirm_hop()                          # 没有证据引用
    action = sched.confirm_hop(simulated=True)
    assert action["simulated_review"] is True
    assert sched.ledger["simulated_reviews"] == 1
    assert sched.stopped == "hop_confirmed"


def test_reject_continues_same_camera_remaining_window():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_with_target())
    rejected = sched.reject(reason="wrong_person")
    assert rejected["review_state"] == "rejected"
    assert rejected["state"] == "running"
    follow = sched.next_action()
    assert follow is not None and follow["action"]["camera"] == "1-1"
    assert follow["window"][0] > 54007.6


def test_rejected_candidate_is_not_sent_to_review_again():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_with_target())
    sched.reject(reason="wrong_person")
    sched.next_action()
    assert sched.submit(_with_target()) != "needs_review"


def test_measured_frames_track_real_cost_not_estimate():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_missed(frames=37))
    assert sched.ledger["measured_frames"] == 37     # 实测
    assert sched.ledger["detector_frames"] == 37
    assert sched.ledger["estimated_frames_run"] == 40  # 计划
    assert sched.actions[0]["measured_frames"] == 37


def test_detection_missed_is_distinct_state():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0, "allow_expand": False})
    sched.next_action()
    sched.submit(_missed())
    assert sched.actions[0]["last_result"] == "detection_missed"
    assert sched.actions[0]["state"] == "running"


def test_recording_unavailable_skips_without_analysis():
    sched = CameraScheduler(_preds(), recording_index=lambda c, s, e: {"available": False},
                            params={"segment_seconds": 8.0})
    assert sched.next_action() is None
    assert all(a["state"] in ("recording_unavailable", "recording_unknown") for a in sched.actions)
    assert sched.ledger["measured_frames"] == 0


def test_recording_unknown_is_not_unavailable():
    sched = CameraScheduler(_preds(), recording_index=lambda c, s, e: {"available": None},
                            params={"segment_seconds": 8.0})
    assert sched.next_action() is None
    assert sched.actions[0]["state"] == "recording_unknown"


def test_budget_exhausted_is_resumable():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 6.0,
                                              "max_decoded_frames": 40,
                                              "allow_expand": False})
    for _ in range(6):
        nxt = sched.next_action()
        if nxt is None:
            break
        sched.submit(_missed())
    assert sched.stopped == "budget_exhausted"
    sched.params["max_decoded_frames"] = 10_000
    sched.resume()
    assert sched.stopped is None


def test_segmented_expands_into_search_window():
    preds = [{"camera": "9-9", "priority": "high", "estimated_window": (1000.0, 1006.0),
              "search_window": (1000.0, 1018.0)}]
    sched = CameraScheduler(preds, recording_index=_available, params={"segment_seconds": 6.0})
    sched.next_action()
    sched.submit(_missed())
    second = sched.next_action()
    assert second["window"][0] == 1006.0
    assert sched.actions[0]["estimated_window"][1] == 1018.0


def test_defer_and_resume_keep_state():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.defer("operator_paused")
    assert sched.next_action() is None
    sched.resume()
    assert sched.actions[0]["state"] == "deferred"


def _two_candidates():
    return {"status": "ok", "tracks": [], "cost": {"frames_decoded": 4},
            "target_candidates": [{"track_id": "t1", "retrieval_score": 0.72,
                                   "margin_top1_top2": 0.2, "state": "candidate"},
                                  {"track_id": "t2", "retrieval_score": 0.66,
                                   "margin_top1_top2": 0.12, "state": "candidate"}]}


def test_reject_one_candidate_keeps_the_other_pending():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_two_candidates())
    sched.reject(candidate_id="t1", reason="wrong_person")
    target = sched._find(None)
    assert target["review_state"] == "pending"
    assert target["candidates"] == ["t2"]
    assert target["state"] == "needs_review"


def test_reject_last_candidate_resumes_window():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_two_candidates())
    sched.reject(candidate_id="t1")
    sched.reject(candidate_id="t2")
    target = sched._find(None)
    assert target is None
    follow = sched.next_action()
    assert follow is not None


def test_confirm_only_the_named_candidate():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_two_candidates())
    target = sched.confirm_candidate("t2", evidence=["frame:x.jpg"])
    assert target["confirmed_track"] == "t2"
    assert target["state"] == "hop_confirmed"


def test_snapshot_and_restore_do_not_reanalyse_done_windows():
    sched = CameraScheduler(_preds(), recording_index=_available, params={"segment_seconds": 8.0})
    first = sched.next_action()
    sched.submit(_with_target())
    sched.reject(candidate_id="w:37")
    snapshot = sched.snapshot()
    restored = CameraScheduler.restore(snapshot, recording_index=_available)
    nxt = restored.next_action()
    assert nxt is None or nxt["window"][0] >= first["window"][0] + 8.0


def test_partial_recording_only_analyses_available_range():
    def partial(camera, start, end):
        return {"available": True, "ranges": [[54008.0, 54012.0]]}

    sched = CameraScheduler(_preds(), recording_index=partial, params={"segment_seconds": 10.0})
    action = sched.next_action()
    assert action["window"] == (54008.0, 54012.0)


def test_cancel_derived_actions_skips_them_without_touching_others():
    sched = CameraScheduler(_preds(), recording_index=_available)
    cancelled = sched.cancel_actions(["1-1#1"])
    assert cancelled == ["1-1#1"]
    first = sched.next_action()
    assert first["action"]["camera"] == "2-1"
