import pytest

from app.m6_camera_scheduler import CameraScheduler, build_actions


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


def _ok(track_id=37):
    return {"status": "ok", "tracks": [{"track_id": track_id}]}


def _missed():
    return {"status": "detection_missed", "tracks": []}


def test_actions_are_per_camera_not_per_site():
    actions = build_actions(_preds())
    assert [a["camera"] for a in actions] == ["1-1", "2-1", "2-2"]
    assert len({a["action_id"] for a in actions}) == 3      # 同点位不合并


def test_ranked_order_is_preserved():
    actions = build_actions(_preds())
    assert [a["rank"] for a in actions] == [1, 2, 3]


def test_top1_without_candidate_continues_to_next_camera():
    sched = CameraScheduler(_preds(), params={"segment_seconds": 8.0, "allow_expand": False})
    first = sched.next_action()
    assert first["action"]["camera"] == "1-1"
    seen = ["1-1"]
    while True:
        nxt = sched.next_action()
        if nxt is None or nxt["action"]["camera"] != "1-1":
            break
        seen.append(nxt["action"]["camera"])
        sched.submit(_missed())
    assert len(seen) >= 2                                   # 1-1 分段查完后仍未放弃
    assert nxt["action"]["camera"] == "2-1"                 # 继续到下一台，而不是判目标没经过
    assert sched.actions[0]["state"] in ("detection_missed", "no_valid_observation")


def test_high_appearance_score_never_auto_confirms():
    sched = CameraScheduler(_preds(), params={"segment_seconds": 8.0})
    sched.next_action()
    state = sched.submit(_ok(37), appearance_score=0.93)
    assert state == "needs_review"                          # 只进复核，不是确认
    assert sched.stopped is None
    with pytest.raises(ValueError):
        sched.confirm_hop(human=False)


def test_detection_missed_is_distinct_from_no_target():
    sched = CameraScheduler(_preds(), params={"segment_seconds": 8.0, "allow_expand": False})
    sched.next_action()
    sched.submit(_missed())
    assert sched.actions[0]["last_result"] == "detection_missed"
    assert sched.actions[0]["state"] == "running"           # 还有段可查


def test_recording_unavailable_skips_without_empty_analysis():
    def index(camera, start, end):
        return {"available": camera != "1-1", "reason": "no_recording"}
    sched = CameraScheduler(_preds(), recording_index=index, params={"segment_seconds": 8.0})
    first = sched.next_action()
    assert first["action"]["camera"] == "2-1"
    assert sched.actions[0]["state"] == "recording_unavailable"
    assert sched.ledger["decoded_frames"] == 0


def test_budget_exhausted_is_resumable():
    sched = CameraScheduler(_preds(), params={"segment_seconds": 6.0,
                                              "max_decoded_frames": 40,
                                              "allow_expand": False})
    for _ in range(6):
        nxt = sched.next_action()
        if nxt is None:
            break
        sched.submit(_missed())
    assert sched.stopped == "budget_exhausted"
    assert sched.status()["stopped"] == "budget_exhausted"
    assert sched.ledger["decoded_frames"] <= 40
    sched.params["max_decoded_frames"] = 10_000
    sched.resume()
    assert sched.stopped is None


def test_segmented_expands_into_search_window_before_giving_up():
    preds = [{"camera": "9-9", "priority": "high", "estimated_window": (1000.0, 1006.0),
              "search_window": (1000.0, 1018.0)}]
    sched = CameraScheduler(preds, params={"segment_seconds": 6.0})
    sched.next_action()
    sched.submit(_missed())
    second = sched.next_action()
    assert second["window"][0] == 1006.0                     # 扩展到原估算窗外
    assert sched.actions[0]["estimated_window"][1] == 1018.0


def test_confirm_hop_ends_round_and_keeps_ledger():
    sched = CameraScheduler(_preds(), params={"segment_seconds": 8.0})
    sched.next_action()
    sched.submit(_ok(37))
    action = sched.confirm_hop()
    assert action["state"] == "hop_confirmed"
    assert sched.stopped == "hop_confirmed"
    assert sched.ledger["decoded_frames"] == 40
    assert sched.ledger["review_count"] == 1


def test_defer_and_resume_keep_state():
    sched = CameraScheduler(_preds(), params={"segment_seconds": 8.0})
    sched.next_action()
    sched.defer("operator_paused")
    assert sched.next_action() is None
    sched.resume()
    assert sched.actions[0]["state"] == "deferred"


def test_exploratory_flag_propagates():
    actions = build_actions(_preds())
    assert all(a["exploratory"] for a in actions)
