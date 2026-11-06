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

def _cards(timeline: dict, kind: str) -> list[dict]:
    return [card for card in timeline["cards"] if card["kind"] == kind]


def test_region_card_aggregates_windows_without_position(env):
    _, Session, _ = env
    ids = _seed(Session)
    with Session() as db:
        timeline = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
    region = _cards(timeline, "region_investigation")[0]
    assert region["presentation"] == "investigating_region"
    assert region["position"] is None
    assert region["displayHint"] == "no_position"
    body = region["region"]
    assert body["status"] == "awaiting_review"
    assert body["plannedWindows"] == 1
    assert body["completedWindows"] == 1  # no_track = 已执行但无轨迹，不是未开始
    assert body["deviceCount"] == 1
    assert body["phaseCounts"]["completed"] == 1
    assert body["phaseCounts"]["failed"] == 0
    assert body["candidateCount"] == 1
    assert body["pendingCandidates"] == 1
    assert body["regionGeometryRef"] is None
    assert body["regionSource"] == "planned_windows"
    assert body["timeQuality"] == "uncalibrated"
    task = body["cameraTasks"][0]
    assert task["windowId"] == ids["window_id"]
    assert task["phase"] == "completed"  # no_track 视为已执行完成
    assert task["status"] == "no_track"
    assert task["plannedStartAt"] is None  # 没有 clip_start_local 就不猜墙钟时间
    assert f"segment:{ids['candidate_id']}" in body["derivedCardIds"]
    assert timeline["anchorLinked"] is True


def test_run_without_windows_is_planned_and_invents_no_cameras(env):
    _, Session, _ = env
    ids = _seed(Session)
    run_id = str(uuid4())
    with Session() as db:
        db.add(TrackingRun(id=run_id, case_id=ids["case_id"], observation_id=ids["observation_id"],
                           status="queued", attempt=0, anchor_json={}, scope_json={},
                           progress_json={}, created_at=NOW + timedelta(seconds=10)))
        db.commit()
        timeline = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
    planned = [card for card in _cards(timeline, "region_investigation")
               if card["cardId"] == f"region:{run_id}:0:planned"]
    assert len(planned) == 1
    body = planned[0]["region"]
    assert body["status"] == "planned"
    assert body["regionSource"] == "none"
    assert body["cameraTasks"] == []
    assert body["plannedObservationWindow"] is None
    assert planned[0]["degradeReason"] == "plan_missing"


def test_confirmed_track_requires_identity_and_frames(env):
    _, Session, _ = env
    ids = _seed(Session)
    with Session() as db:
        db.get(TrackingCandidate, ids["candidate_id"]).identity_status = "human_confirmed"
        db.commit()
        without_frames = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
        segment = _cards(without_frames, "track_segment")[0]
        assert segment["presentation"] == "review_track"
        candidate = db.get(TrackingCandidate, ids["candidate_id"])
        candidate.details_json = {"clip_id": "clip-1", "frames": 31}
        db.commit()
        with_frames = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
        promoted = _cards(with_frames, "track_segment")[0]
    assert promoted["presentation"] == "confirmed_track"


def test_anchor_only_timeline_marks_anchor_unlinked(env):
    _, Session, _ = env
    ids = _seed(Session, with_run=False)
    with Session() as db:
        timeline = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
    assert timeline["anchorLinked"] is False
    assert timeline["cards"][0]["presentation"] == "review_track"


def test_region_card_resolves_camera_uuid_only_by_exact_catalog_name(env):
    _, Session, _ = env
    ids = _seed(Session)
    directory = {projection.canonical_name("1-LT-桂康路松山巷"): "79cec34c-009d-4d6d-befd-6fb64337ee0a"}
    with Session() as db:
        resolved = projection.build_timeline(db, ids["case_id"], ids["observation_id"],
                                             camera_directory=directory)
        unresolved = projection.build_timeline(db, ids["case_id"], ids["observation_id"])
    task = _cards(resolved, "region_investigation")[0]["region"]["cameraTasks"][0]
    assert task["cameraUuid"] == "79cec34c-009d-4d6d-befd-6fb64337ee0a"
    assert task["cameraUuidSource"] == "catalog_name_exact"
    plain = _cards(unresolved, "region_investigation")[0]["region"]["cameraTasks"][0]
    assert plain["cameraUuid"] is None
    assert plain["cameraUuidSource"] is None


def test_explicit_map_camera_id_wins_over_name_match(env):
    _, Session, _ = env
    ids = _seed(Session)
    with Session() as db:
        db.get(TrackingCandidate, ids["candidate_id"]).map_camera_id = "explicit-camera"
        db.commit()
        timeline = projection.build_timeline(db, ids["case_id"], ids["observation_id"],
                                             camera_directory={projection.canonical_name("1-LT-桂康路松山巷"): "name-camera"})
    task = _cards(timeline, "region_investigation")[0]["region"]["cameraTasks"][0]
    assert task["cameraUuid"] == "explicit-camera"
    assert task["cameraUuidSource"] == "candidate_explicit"


def test_window_iso_accepts_start_local_alias():
    anchor = {"start_local": "2026-10-23T15:00:00", "start_of_day_seconds": 54000.0}
    assert projection._window_iso(anchor, 54007.0) == "2026-10-23T15:00:07"
    assert projection._window_iso({"clip_start_local": "2026-10-23T15:00:00",
                                  "start_of_day_seconds": 54000.0}, 54007.0) == "2026-10-23T15:00:07"
    assert projection._window_iso({}, 54007.0) is None


def _view_region(card_id, run_id, camera, window_id, start, end, **extra):
    window_seconds = extra.pop("window_seconds", [54002.0, 54015.0])
    return {
        "cardId": card_id, "kind": "region_investigation", "cameraLabel": camera,
        "observedAt": extra.pop("observedAt", "2026-10-23T15:00:00"),
        "analysisAt": "2026-10-23T15:10:00", "identityStatus": "candidate_unverified",
        "region": {
            "runId": run_id, "attempt": 1, "runStatus": extra.pop("run_status", "awaiting_review"),
            "status": "awaiting_review", "timeBasis": "clip_start_local",
            "timeQuality": "uncalibrated_osd_ocr", "cameraTasks": [{
                "windowId": window_id, "cameraLabel": camera, "cameraUuid": None,
                "cameraUuidSource": None, "clipId": "clip-1",
                "plannedStartAt": start, "plannedEndAt": end,
                "windowSeconds": window_seconds, "phase": "completed",
                "status": "ok", "tracks": 1, "errorCode": None, "note": None,
            }], "derivedCardIds": [card_id], "researchBudgetSeconds": 120,
            "researchSecondsUsed": 30, "executionCount": 1, "stopReason": None,
            "lastUpdatedAt": "2026-10-23T15:10:00",
        },
    }


def _view_segment(card_id, camera, track_id, identity="candidate_unverified", frames=None):
    return {
        "cardId": card_id, "kind": "track_segment", "cameraLabel": camera,
        "observedAt": "2026-10-23T15:00:02", "observedAtSource": "event_time",
        "analysisAt": "2026-10-23T15:10:00", "identityStatus": identity,
        "spatialQuality": None, "segment": {"window": [54002.0, 54015.0], "frames": frames},
        "evidence": {"trackId": track_id, "frames": frames},
    }


def test_view_groups_adjacent_cameras_and_hides_technical_cards():
    from app.timeline_view import compose_view
    cards = [
        _view_region("region:r1:1:windows", "r1", "1-LT-桂康路松山巷", "w1",
                     "2026-10-23T15:00:02", "2026-10-23T15:00:15"),
        _view_region("region:r2:1:windows", "r2", "1-LT-桂康路松山巷-向北", "w2",
                     "2026-10-23T15:00:02", "2026-10-23T15:00:15"),
        _view_region("region:r3:1:windows", "r3", "1-LT-桂康路正通路口-向西", "w3",
                     "2026-10-23T15:00:03", "2026-10-23T15:00:15"),
        _view_segment("segment:v1", "1-LT-桂康路松山巷", "t1"),
        _view_segment("segment:v2", "1-LT-桂康路松山巷", "t1",
                      identity="human_confirmed"),
    ]
    positions = {
        projection.canonical_name("1-LT-桂康路松山巷"): {"lat": 25.257477, "lon": 110.204471},
        projection.canonical_name("1-LT-桂康路松山巷-向北"): {"lat": 25.257477, "lon": 110.204471},
        projection.canonical_name("1-LT-桂康路正通路口-向西"): {"lat": 25.257734, "lon": 110.214201},
    }
    view = compose_view({"caseId": "c", "targetId": "t", "phase": "awaiting_review",
                         "anchorLinked": True, "cards": cards}, positions)
    assert view["counts"]["region"] == 2
    assert view["counts"]["review"] == 1
    assert all(card["kind"] != "camera_task" for card in view["cards"])
    regions = [card for card in view["cards"] if card["kind"] == "region_investigation"]
    merged = next(card for card in regions if card["place"].startswith("桂康路松山巷"))
    assert merged["region"]["deviceCount"] == 2
    assert merged["region"]["plannedWindows"] == 2
    assert merged["region"]["candidateCount"] == 1  # 同一 track+window 去重
    cam = next(task for task in merged["region"]["cameraTasks"]
               if task["cameraLabel"] == "1-LT-桂康路松山巷")
    assert cam["candidateCount"] == 1
    # 去重后保留的是已确认身份但无逐帧轨迹的候选，因此这台设备不再需要“待核查”标记。
    assert cam["pendingCandidateCount"] == 0
    clue = next(card for card in view["cards"] if card["kind"] == "candidate_clue")
    assert clue["candidate"]["count"] == 1
    assert "已确认身份但无逐帧轨迹" in clue["subtitle"]


def test_view_marks_camera_with_pending_candidates():
    from app.timeline_view import compose_view
    cards = [
        _view_region("region:r1:1:windows", "r1", "cam-a", "w1",
                     "2026-10-23T15:00:02", "2026-10-23T15:00:15"),
        _view_segment("segment:v1", "cam-a", "t1"),
    ]
    view = compose_view({"caseId": "c", "targetId": "t", "cards": cards},
                        {projection.canonical_name("cam-a"): {"lat": 25.0, "lon": 110.0}})
    region = next(card for card in view["cards"]
                  if card["kind"] == "region_investigation")["region"]
    task = region["cameraTasks"][0]
    assert task["candidateCount"] == 1
    assert task["pendingCandidateCount"] == 1


def test_view_merges_repeated_searches_of_one_camera():
    from app.timeline_view import compose_view
    cards = [
        _view_region("region:r1:1:windows", "r1", "1-LT-桂康路松山巷", "w1",
                     "2026-10-23T15:00:02", "2026-10-23T15:00:10",
                     window_seconds=[54002.0, 54010.0]),
        _view_region("region:r2:1:windows", "r2", "1-LT-桂康路松山巷", "w2",
                     "2026-10-23T15:00:03", "2026-10-23T15:00:11",
                     window_seconds=[54003.0, 54011.0]),
    ]
    positions = {projection.canonical_name("1-LT-桂康路松山巷"): {"lat": 25.257477, "lon": 110.204471}}
    view = compose_view({"caseId": "c", "targetId": "t", "cards": cards}, positions)
    region = next(card for card in view["cards"]
                  if card["kind"] == "region_investigation")["region"]
    assert region["deviceCount"] == 1
    assert region["plannedWindows"] == 2
    assert len(region["cameraTasks"]) == 1
    task = region["cameraTasks"][0]
    assert task["windowCount"] == 2
    assert len(task["runIds"]) == 2
    assert len(task["windowRanges"]) == 1  # 54002–54011 连续，合并成一个覆盖范围
    assert (task["windowRanges"][0]["startSeconds"], task["windowRanges"][0]["endSeconds"]) == (54002.0, 54011.0)


def test_view_without_positions_does_not_guess_adjacency():
    from app.timeline_view import compose_view
    cards = [
        _view_region("region:r1:1:windows", "r1", "1-LT-桂康路松山巷", "w1", None, None),
        _view_region("region:r2:1:windows", "r2", "1-LT-桂康路松山巷-向北", "w2", None, None),
    ]
    view = compose_view({"caseId": "c", "targetId": "t", "cards": cards}, {})
    assert view["counts"]["region"] == 2
    assert all(card["observedAt"] is None for card in view["cards"])
    assert all(card["observedAtSource"] == "unmapped" for card in view["cards"])


def test_view_promotes_confirmed_candidate_only_with_frames():
    from app.timeline_view import compose_view
    cards = [
        _view_region("region:r1:1:windows", "r1", "cam-a", "w1", "2026-10-23T15:00:02",
                     "2026-10-23T15:00:15"),
        _view_segment("segment:no-frames", "cam-a", "t1", "human_confirmed"),
    ]
    view = compose_view({"caseId": "c", "targetId": "t", "cards": cards},
                        {projection.canonical_name("cam-a"): {"lat": 25.0, "lon": 110.0}})
    assert view["counts"]["confirmed"] == 0
    assert view["counts"]["review"] == 1
    cards.append(_view_segment("segment:with-frames", "cam-a", "t2", "confirmed", frames=31))
    view = compose_view({"caseId": "c", "targetId": "t", "cards": cards},
                        {projection.canonical_name("cam-a"): {"lat": 25.0, "lon": 110.0}})
    assert view["counts"]["confirmed"] == 1
    assert view["counts"]["review"] == 1

