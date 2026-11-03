import json
from pathlib import Path

import pytest

from app.m6_one_hop import run_one_hop
from app.m6_time_map import frame_time_map
from app.m6_truth_registry import (StartingAnchorError, observation, starting_observation)


def _time_maps():
    return {"1-1": frame_time_map("rec-1-1", "u11", 53997.0, 5.0, frame_count=370),
            "2-1": frame_time_map("rec-2-1", "u21", 53998.0, 5.0, frame_count=370)}


def _anchor(label="1-1", start=54006.2):
    return {"case_id": "purple", "target_id": "purple", "camera_uuid": "u11",
            "map_camera_id": "37", "camera_label": label, "tracklet_id": "111",
            "observed_start": start, "observed_end": start + 2.4,
            "camera_uuids": {"1-1": "u11", "2-1": "u21"},
            "recording_id": "rec-1-1", "evidence": ["frame:1-1/0047.jpg"],
            "camera_ids": {"37": "1-1", "58": "2-1"},
            "binding_status": "pending_human_review"}


def _predictions(base=54010.0):
    return [{"camera": "58", "priority": "high", "estimated_window": [base, base + 10.0],
             "search_window": [base, base + 16.0]}]


def _analyze(label, span, window_id):
    first = span["start_index"] + 3
    return {"status": "ok", "tracks": [
        {"track_id": "t1", "observations": [{"frame_id": first, "bbox": [0, 0, 9, 9]},
                                            {"frame_id": first + 10, "bbox": [0, 0, 9, 9]}]},
        {"track_id": "t2", "observations": [{"frame_id": first + 1, "bbox": [0, 0, 9, 9]}]}],
        "cost": {"frames_decoded": 1}}


def _score(label, window):
    return [{"track_id": "t1", "retrieval_score": 0.72},
            {"track_id": "t2", "retrieval_score": 0.53}]


def _spatial(observed_end, camera, camera_id):
    return {"source_time": observed_end, "from_camera": camera,
            "top5": [{"camera": "37", "window": [observed_end + 0.5, observed_end + 30]}]}


def test_hop_time_comes_from_detected_frames_not_constants():
    result = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                         predictions=_predictions(), analyze=_analyze, score=_score,
                         spatial=_spatial)
    hop = result["hop"]
    assert hop["first_frame"] == 63 and hop["last_frame"] == 73
    # 2-1 锚点 53998：窗口 54010 起第 3 帧 = 63 帧 → 54010.6，73 帧 → 54012.6
    assert hop["observed_start"] == pytest.approx(54010.6, abs=0.01)
    assert hop["observed_end"] == pytest.approx(54012.6, abs=0.01)
    assert result["next_round"]["result"]["source_time"] == hop["observed_end"]


def test_anchor_window_drives_search_without_b_constants():
    early = run_one_hop(anchor=_anchor(start=54006.2), time_maps=_time_maps(),
                        predictions=_predictions(54010.0), analyze=_analyze, score=_score,
                        spatial=_spatial)
    late = run_one_hop(anchor=_anchor(start=54020.0), time_maps=_time_maps(),
                       predictions=_predictions(54024.0), analyze=_analyze, score=_score,
                       spatial=_spatial)
    assert late["hop"]["observed_start"] - early["hop"]["observed_start"] == pytest.approx(14.0)
    assert (late["next_round"]["result"]["source_time"]
            != early["next_round"]["result"]["source_time"])


def test_no_review_keeps_profile_at_v1():
    result = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                         predictions=_predictions(), analyze=_analyze, score=_score,
                         spatial=_spatial)
    assert result["profile"]["version"] == "V1"
    assert result["review"]["profile_advanced"] is False


def test_simulated_confirmation_never_advances_to_v2():
    result = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                         predictions=_predictions(), analyze=_analyze, score=_score,
                         spatial=_spatial,
                         review_events=[{"event_type": "confirm", "source": "simulated",
                                         "reviewer": "sim-op", "reviewed_at": "t",
                                         "evidence": []}])
    assert result["review"]["decisions"][0]["status"] == "simulated_only"
    assert result["next_round"]["kind"] == "hypothesis_next_round"
    assert result["profile"]["version"] == "V1"
    assert result["review"]["profile_advanced"] is False


def test_human_confirmation_with_evidence_advances_to_v2(tmp_path):
    frames = tmp_path / "2-1"
    frames.mkdir()
    (frames / "0047.jpg").write_bytes(b"x")
    (frames / "0057.jpg").write_bytes(b"x")
    result = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                         predictions=_predictions(), analyze=_analyze, score=_score,
                         spatial=_spatial, evidence_root=str(tmp_path),
                         review_events=[{"event_type": "confirm", "source": "human_review",
                                         "reviewer": "case-owner", "reviewed_at": "2026-11-03T12:00:00Z",
                                         "evidence": ["frame:2-1/0047.jpg"]}])
    assert result["review"]["decisions"][0]["status"] == "confirmed"
    assert result["profile"]["version"] == "V2"
    assert result["review"]["profile_advanced"] is True
    assert result["next_round"]["kind"] == "confirmed_next_round"


def test_starting_observation_requires_confirmed_anchor():
    package = {"case_id": "purple", "observations": [], "bindings": []}
    with pytest.raises(StartingAnchorError):
        starting_observation(package, "purple")


def test_starting_observation_returns_earliest_confirmed_anchor():
    package = {"case_id": "purple", "observations": [],
               "bindings": [{"camera_uuid": "u11", "label": "1-1",
                             "binding_status": "pending_human_review"}]}
    first = observation("purple", "purple", "u11", "37", "111", 54006.2, "rec-1-1",
                        osd_end_seconds=54008.6, evidence=["frame:1-1/0047.jpg"])
    second = observation("purple", "purple", "u21", "58", "37", 54012.6, "rec-2-1",
                         osd_end_seconds=54015.6, evidence=["frame:2-1/0074.jpg"])
    for record in (second, first):
        package["observations"].append({**record, "status": "human_confirmed"})
    anchor = starting_observation(package, "purple")
    assert anchor["tracklet_id"] == "111"
    assert anchor["camera_label"] == "1-1"


def test_starting_observation_refuses_to_invent_end_time():
    package = {"case_id": "purple", "bindings": [],
               "observations": [observation("purple", "purple", "u11", "37", "111", 54006.2,
                                            "rec-1-1", evidence=["frame:1-1/0047.jpg"])]}
    package["observations"][0]["status"] = "human_confirmed"
    with pytest.raises(StartingAnchorError) as exc:
        starting_observation(package, "purple")
    assert str(exc.value) == "anchor_end_time_unknown"


def test_weak_candidate_is_kept_and_outranks_later_strong_candidate():
    state = {"call": 0}

    def analyze(label, span, window_id):
        state["call"] += 1
        track = "weak@%d" % state["call"] if state["call"] == 1 else "strong@%d" % state["call"]
        return {"status": "ok",
                "tracks": [{"track_id": track, "observations": [
                    {"frame_id": span["start_index"] + 2, "bbox": [0, 0, 9, 9]},
                    {"frame_id": span["start_index"] + 5, "bbox": [0, 0, 9, 9]}]}],
                "cost": {"frames_decoded": 4}}

    def score(label, window):
        track = list(window["tracks"])[0]["track_id"]
        if track.startswith("weak"):
            return [{"track_id": track, "retrieval_score": 0.733},
                    {"track_id": "other", "retrieval_score": 0.658}]
        return [{"track_id": track, "retrieval_score": 0.661},
                {"track_id": "other2", "retrieval_score": 0.549}]

    result = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                         predictions=_predictions(54010.0) + _predictions(54020.0),
                         analyze=analyze, score=score, spatial=_spatial)
    pool = result["candidate_pool"]
    assert len(pool) >= 2
    assert any(c["state"] == "candidate" for c in pool)
    assert pool[0]["track_id"].startswith("weak")      # 高分弱候选不会被后来的强候选顶掉
    assert pool[0]["state"] == "weak_candidate"
    assert result["hop"]["track_id"].startswith("weak")


def test_review_of_other_candidate_does_not_confirm_hop():
    def score(label, window):
        return [{"track_id": "t1", "retrieval_score": 0.72},
                {"track_id": "t2", "retrieval_score": 0.66},
                {"track_id": "t3", "retrieval_score": 0.51}]

    result = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                         predictions=_predictions(), analyze=_analyze, score=score,
                         spatial=_spatial,
                         review_events=[{"event_type": "confirm", "source": "human_review",
                                         "candidate_id": "2-1/not-a-pool-candidate",
                                         "reviewer": "case-owner",
                                         "reviewed_at": "2026-11-03T12:00:00Z",
                                         "evidence": ["frame:2-1/0047.jpg"]}])
    assert result["profile"]["version"] == "V1"
    assert result["review"]["decisions"][0]["status"] == "rejected"


def test_no_usable_candidate_ends_unresolved():
    def empty_score(label, window):
        return [{"track_id": "t1", "retrieval_score": None}]

    result = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                         predictions=_predictions(), analyze=_analyze, score=empty_score,
                         spatial=_spatial)
    assert result["hop"] is None
    assert result["session"]["status"] == "unresolved"
    assert result["profile"]["version"] == "V1"


def test_resume_from_snapshot_skips_completed_windows():
    first = run_one_hop(anchor=_anchor(), time_maps=_time_maps(),
                        predictions=_predictions(), analyze=_analyze, score=_score,
                        spatial=_spatial)
    state = first["scheduler_state"]
    second = run_one_hop(anchor=_anchor(), time_maps=_time_maps(), predictions=_predictions(),
                         analyze=_analyze, score=_score, spatial=_spatial, snapshot=state)
    done = [step["scheduled_window"] for step in first["steps"]]
    again = [step["scheduled_window"] for step in second["steps"]]
    assert not (set(map(tuple, again)) & set(map(tuple, done))) or again == []
