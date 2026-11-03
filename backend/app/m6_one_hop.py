"""B4.2 / B5-A：单跳执行（候选池版）。

原则：证据不足的候选不丢弃。
  - strong candidate 进入优先审核队列；
  - weak candidate 保留在候选池，可被重新排序或人工复核；
  - 后续窗口出现更高分候选时，不会删除先前候选；
  - 只有人工审核事件能推进主链。
"""
from __future__ import annotations

from .m6_camera_scheduler import CameraScheduler
from .m6_review_events import apply_events, review_event
from .m6_target_candidate_adapter import candidates_from_scores
from .m6_target_profile import advance_from_review, attach_candidate, new_profile
from .m6_time_map import span_for_window, time_at
from .m6_tracking_session import TrackingSession
from .m6_truth_registry import add_observation, observation


def _entry(candidate, name, camera_id, action_id, window, track, time_map):
    frames = [int(o["frame_id"]) for o in track["observations"]]
    return {"candidate_id": "%s/%s" % (name, candidate["track_id"]),
            "camera_label": name, "map_camera_id": camera_id,
            "action_id": action_id, "window": list(window),
            "track_id": candidate["track_id"],
            "retrieval_score": candidate["retrieval_score"],
            "margin_top1_top2": candidate.get("margin_top1_top2"),
            "state": candidate["state"],
            "first_frame": min(frames), "last_frame": max(frames),
            "observed_start": time_at(time_map, min(frames)),
            "observed_end": time_at(time_map, max(frames)),
            "evidence_refs": ["frame:%s/%04d.jpg" % (name, min(frames) + 1),
                              "frame:%s/%04d.jpg" % (name, max(frames) + 1)]}


def _ranked(pool):
    return sorted(pool, key=lambda c: ((c["retrieval_score"] or 0),
                                       (c["margin_top1_top2"] or 0)), reverse=True)


def run_one_hop(*, anchor, time_maps, predictions, analyze, score, spatial,
                review_events=(), evidence_root=None, max_actions=80,
                segment_seconds=6.0, snapshot=None, max_decoded_frames=10 ** 6):
    """anchor 只给 A；B 的镜头、时间与轨迹全部由运行中的真实分析产生。"""
    labels = dict(anchor["camera_ids"])
    uuids = dict(anchor.get("camera_uuids") or {})

    def recording_index(camera, start, end):
        name = labels.get(camera)
        if name is None or name not in time_maps:
            return {"available": False, "reason": "no_recording"}
        time_map = time_maps[name]
        first = time_at(time_map, time_map["start_index"])
        last = time_at(time_map, time_map["start_index"] + time_map["frame_count"] - 1)
        if first is None or start < first - 0.2 or end > last + 0.2:
            return {"available": False, "reason": "outside_frozen_frames"}
        return {"available": True}

    params = {"segment_seconds": segment_seconds, "max_decoded_frames": max_decoded_frames}
    scheduler = (CameraScheduler.restore(snapshot, recording_index=recording_index,
                                         params=params) if snapshot else
                 CameraScheduler(predictions, recording_index=recording_index,
                                 params=params, mode="segmented"))
    session = TrackingSession(anchor["case_id"], anchor["target_id"],
                              {"observation_id": "A", "camera": anchor["camera_label"],
                               "observed_start": anchor["observed_start"],
                               "observed_end": anchor["observed_end"]})
    anchor_id = session.main_chain[-1]["observation_id"]
    steps, pool = [], []
    for _ in range(max_actions):
        current = scheduler.next_action()
        if current is None:
            break
        act = current["action"]
        camera = act["camera"]
        name = labels[camera]
        window = current["window"]
        span = span_for_window(time_maps[name], *window)
        result = analyze(name, span, "%s@%.1f" % (name, window[0]))
        result["target_candidates"] = candidates_from_scores(score(name, result))["target_candidates"]
        state = scheduler.submit(result)
        tracks = {t["track_id"]: t for t in result.get("tracks") or []}
        window_entries = [_entry(candidate, name, camera, act["action_id"], window,
                                 tracks[candidate["track_id"]], time_maps[name])
                          for candidate in result["target_candidates"]]
        pool.extend(window_entries)
        session.observe(window_entries, from_anchor_id=anchor_id)
        if state == "budget_exhausted":
            session.budget_exhausted()
        steps.append({"camera": name, "scheduled_window": list(window),
                      "actual_seconds": [span["start_seconds"], span["end_seconds"]],
                      "actual_frames": [span["start_index"], span["end_index"]],
                      "tracks": len(result.get("tracks") or []),
                      "candidates": result["target_candidates"], "state": state})

    ranked = _ranked(pool)
    if not ranked:
        session.status = "unresolved"
    lookups = {c["candidate_id"]: c for c in ranked}
    profile = new_profile(anchor["target_id"], anchor["camera_label"],
                          anchor["tracklet_id"], anchor["observed_start"],
                          anchor["observed_end"])
    registry = {"case_id": anchor["case_id"], "observations": [], "applied_events": []}
    review = {"decisions": [], "reviews": [], "profile_advanced": False}
    best = session.best()
    hop = (lookups.get(best["candidate_id"]) if best else (ranked[0] if ranked else None))
    if hop:
        profile = attach_candidate(profile, hop["camera_label"], hop["track_id"],
                                   hop["observed_start"], hop["observed_end"], [])
        add_observation(registry, observation(
            anchor["case_id"], anchor["target_id"], uuids.get(hop["camera_label"]),
            hop["map_camera_id"], hop["track_id"], hop["observed_start"],
            time_maps[hop["camera_label"]]["recording_id"],
            osd_end_seconds=hop["observed_end"], osd_uncertainty_seconds=0.2,
            evidence=hop["evidence_refs"], note="模型候选，等待审核"))

    registry_events = []
    for item in review_events:
        cid = item.get("candidate_id") or (hop["candidate_id"] if hop else None)
        event = review_event(item["event_type"], anchor["case_id"], anchor["target_id"], cid,
                             item.get("reviewer"), item.get("reviewed_at", ""),
                             evidence=item.get("evidence"), reason=item.get("reason"),
                             source=item.get("source", "human_review"),
                             event_id=item.get("event_id"))
        if event["source"] == "simulated":
            review["reviews"].append({"candidate_id": cid, "event": event["event_type"],
                                      "source": "simulated"})
            if hop and cid == hop["candidate_id"]:
                review["decisions"].append({"candidate_id": cid, "status": "simulated_only"})
            continue
        try:
            if event["event_type"] == "confirm":
                session.confirm(cid, event)
            elif event["event_type"] == "reject":
                session.reject(cid, event)
            else:
                session.undecided(cid, event)
        except Exception as exc:
            review["decisions"].append({"candidate_id": cid, "status": "rejected",
                                        "code": str(exc)})
            continue
        review["reviews"].append({"candidate_id": cid, "event": event["event_type"],
                                  "source": event["source"]})
        if hop and cid == hop["candidate_id"]:
            registry_events.append(event)

    if registry_events:
        record = registry["observations"][0]
        bound = [{**e, "candidate_id": record["observation_id"]} for e in registry_events]
        review["decisions"] = apply_events(registry, bound, evidence_root=evidence_root)

    if hop:
        confirmed = any(r["status"] == "confirmed" for r in review["decisions"])
        rejected = any(r["status"] == "rejected" for r in review["decisions"])
        decision = "confirmed" if confirmed else ("rejected" if rejected else "candidate")
        profile = advance_from_review(profile, profile["candidates"][0]["candidate_id"],
                                      decision)
        review["profile_advanced"] = profile["version"] == "V2"

    next_round = None
    if hop:
        result = spatial(hop["observed_end"], hop["camera_label"], hop["map_camera_id"])
        next_round = {"kind": ("confirmed_next_round" if review["profile_advanced"]
                               else "hypothesis_next_round"),
                      "based_on": hop["candidate_id"], "result": result}
    return {"anchor": anchor, "steps": steps, "hop": hop,
            "candidate_pool": ranked, "review": review,
            "profile": {"version": profile["version"],
                        "observations": profile["observations"],
                        "candidates": profile["candidates"]},
            "next_round": next_round, "session": session.summary(),
            "scheduler_state": scheduler.snapshot(), "ledger": scheduler.ledger}
