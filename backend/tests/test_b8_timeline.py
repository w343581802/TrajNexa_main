"""B8 时间轴读模型（V1.1 P0）：卡片联合类型、无坐标不造点、事件增量。"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app import timeline_projection as projection
from app.models import (Case, MediaAsset, TargetObservation, TargetSpatialObservation,
                        TrackingCandidate, TrackingReviewEvent, TrackingRun, TrackingWindow)

NOW = datetime(2026, 10, 23, 7, 0, 0, tzinfo=timezone.utc)


def _seed(Session, *, with_run=True, status="awaiting_review"):
    case_id, media_id, obs_id = str(uuid4()), str(uuid4()), str(uuid4())
    run_id, window_id, candidate_id, review_id = (str(uuid4()) for _ in range(4))
    with Session() as db:
        db.add(Case(id=case_id, code="C-" + case_id[:8], name="case"))
        db.add(MediaAsset(id=media_id, case_id=case_id, kind="image", original_name="a.jpg",
                          mime_detected="image/jpeg", sha256="0" * 64,
                          storage_relpath="media/a.jpg", size_bytes=1))
        db.add(TargetObservation(id=obs_id, case_id=case_id, media_id=media_id,
                                 object_class="person",
                                 analysis_json={"bbox_pixels": [10, 20, 110, 220]},
                                 review_note="n", review_state="pending"))
        if with_run:
            db.add(TrackingRun(id=run_id, case_id=case_id, observation_id=obs_id,
                               status=status, attempt=1,
                               anchor_json={"camera_label": "1-LT-桂康路松山巷",
                                            "clip_id": "clip-1",
                                            "offset_in_clip_seconds": 5.0,
                                            "clock_quality": "uncalibrated"},
                               scope_json={},
                               progress_json={"windows_done": 1, "frames_used": 120},
                               created_at=NOW, started_at=NOW + timedelta(seconds=1),
                               finished_at=NOW + timedelta(seconds=9)))
            db.add(TrackingWindow(id=window_id, run_id=run_id, camera_label="1-LT-桂康路松山巷",
                                  clip_id="clip-1", window_start=54002.0, window_end=54015.0,
                                  status="no_track", tracks=0, cost_json={}, note="",
                                  created_at=NOW + timedelta(seconds=3)))
            db.add(TrackingCandidate(id=candidate_id, run_id=run_id, candidate_key="k1",
                                     camera_label="1-LT-桂康路松山巷", track_id="a:1",
                                     state="weak_candidate",
                                     identity_status="candidate_unverified",
                                     retrieval_score=0.66, margin_top1_top2=0.03,
                                     window_start=54002.0, window_end=54015.0,
                                     evidence_refs=["tracking_runs/w/0001.jpg"],
                                     details_json={"clip_id": "clip-1"},
                                     created_at=NOW + timedelta(seconds=4)))
            db.add(TrackingReviewEvent(id=review_id, run_id=run_id, candidate_id=candidate_id,
                                       decision="approve", reviewer="human_reviewer",
                                       note="ok", created_at=NOW + timedelta(seconds=5)))
        db.commit()
    return {"case_id": case_id, "observation_id": obs_id, "run_id": run_id,
            "window_id": window_id, "candidate_id": candidate_id}


def test_anchor_only_target_has_no_position(env):
    _, Session, _ = env
    ids = _seed(Session, with_run=False)
    with Session() as db:
        timeline = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
    assert timeline["phase"] == "not_started"
    assert timeline["counts"]["total"] == 1
    card = timeline["cards"][0]
    assert card["kind"] == "anchor"
    assert card["position"] is None
    assert card["displayHint"] == "no_position"
    assert card["cardId"].startswith("anchor:")


def test_cards_cover_task_gap_segment_without_coordinates(env):
    _, Session, _ = env
    ids = _seed(Session)
    with Session() as db:
        timeline = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
    kinds = [card["kind"] for card in timeline["cards"]]
    assert kinds.count("anchor") == 1
    assert kinds.count("camera_task") == 1
    assert kinds.count("gap") == 1
    assert kinds.count("track_segment") == 1
    for card in timeline["cards"]:
        assert card["position"] is None
    segment = next(card for card in timeline["cards"] if card["kind"] == "track_segment")
    assert segment["identityStatus"] == "candidate_unverified"
    assert segment["segment"]["trackId"] == "a:1"
    assert timeline["phase"] == "awaiting_review"


def test_spatial_card_exposes_point_only_for_l2(env):
    _, Session, _ = env
    ids = _seed(Session, with_run=False)
    with Session() as db:
        db.add(TargetSpatialObservation(id=str(uuid4()), case_id=ids["case_id"],
                                        observation_id=ids["observation_id"],
                                        spatial_level="L1", quality="region_only_unverified",
                                        identity_status="candidate_unverified",
                                        ground_point={"lat": 25.256, "lon": 110.211},
                                        payload={}, created_at=NOW))
        db.add(TargetSpatialObservation(id=str(uuid4()), case_id=ids["case_id"],
                                        observation_id=ids["observation_id"],
                                        spatial_level="L2", quality="experimental_estimate",
                                        identity_status="candidate_unverified",
                                        ground_point={"lat": 25.257, "lon": 110.212},
                                        uncertainty_radius_m=3.5, payload={},
                                        created_at=NOW + timedelta(seconds=1)))
        db.commit()
        timeline = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
    spatial = [card for card in timeline["cards"] if card["kind"] == "spatial_observation"]
    assert len(spatial) == 2
    l1 = next(card for card in spatial if card["spatialQuality"] == "L1")
    l2 = next(card for card in spatial if card["spatialQuality"] == "L2")
    assert l1["position"] is None
    assert l1["degradeReason"] == "no_ground_point"
    assert l2["position"] == {"lat": 25.257, "lon": 110.212}
    assert l2["displayHint"] == "device_point"


def test_events_are_ordered_and_incremental(env):
    _, Session, _ = env
    ids = _seed(Session)
    with Session() as db:
        full = projection.build_events(db, ids["case_id"], ids["run_id"])
        tail = projection.build_events(db, ids["case_id"], ids["run_id"], after_seq=3)
    types = [event["type"] for event in full["events"]]
    assert types[0] == "task.queued"
    assert types[1] == "task.running"
    assert types[-1] == "task.finished"
    assert {event["seq"] for event in full["events"]} == set(range(1, len(types) + 1))
    assert all(event["seq"] > 3 for event in tail["events"])
    assert len(tail["events"]) == len(full["events"]) - 3
    assert full["derived"] is True


def test_timeline_rejects_foreign_case(env):
    _, Session, _ = env
    ids = _seed(Session)
    with Session() as db:
        with pytest.raises(projection.TimelineError):
            projection.build_timeline(db, "another-case", ids["observation_id"])


def test_timeline_endpoint_and_events_endpoint(env):
    client, Session, _ = env
    ids = _seed(Session)
    response = client.get(f"/api/v1/cases/{ids['case_id']}/targets/{ids['observation_id']}/timeline")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"]["total"] >= 4
    assert all("cardId" in card for card in body["cards"])
    events = client.get(f"/api/v1/cases/{ids['case_id']}/tracking-runs/{ids['run_id']}/events?after_seq=1")
    assert events.status_code == 200, events.text
    assert all(event["seq"] > 1 for event in events.json()["events"])
    missing = client.get(f"/api/v1/cases/{ids['case_id']}/targets/nope/timeline")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "target_not_found"