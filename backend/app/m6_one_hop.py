"""B4.2：从真值包里的已确认起点驱动单跳检索。

编排层只依赖注入的 analyze/score/spatial 三个回调，
因此可以在没有 GPU、没有模型的单元测试里验证"不注入 B 答案也能走到候选"。
"""
from __future__ import annotations

from .m6_camera_scheduler import CameraScheduler
from .m6_review_events import apply_events, review_event
from .m6_target_candidate_adapter import candidates_from_scores
from .m6_target_profile import advance_from_review, attach_candidate, new_profile
from .m6_time_map import span_for_window, time_at
from .m6_truth_registry import add_observation, observation


def run_one_hop(*, anchor, time_maps, predictions, analyze, score, spatial,
                review_events=(), evidence_root=None, max_actions=80,
                segment_seconds=6.0):
    """anchor 只描述 A（起点）；B 的时间/轨迹在运行中由真实检测得出。"""
    label = anchor["camera_label"]
    labels = dict(anchor["camera_ids"])

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

    scheduler = CameraScheduler(predictions, recording_index=recording_index,
                                params={"segment_seconds": segment_seconds,
                                        "max_decoded_frames": 10 ** 6},
                                mode="segmented")
    steps = []
    hop = None
    for _ in range(max_actions):
        action = scheduler.next_action()
        if action is None:
            break
        camera = action["action"]["camera"]
        name = labels[camera]
        span = span_for_window(time_maps[name], *action["window"])
        window = analyze(name, span, "%s@%.1f" % (name, action["window"][0]))
        window["target_candidates"] = candidates_from_scores(score(name, window))["target_candidates"]
        state = scheduler.submit(window)
        steps.append({"camera": name, "scheduled_window": list(action["window"]),
                      "actual_seconds": [span["start_seconds"], span["end_seconds"]],
                      "actual_frames": [span["start_index"], span["end_index"]],
                      "tracks": len(window.get("tracks") or []),
                      "candidates": window["target_candidates"], "state": state})
        if state == "needs_review":
            candidate = window["target_candidates"][0]
            track = next(t for t in window["tracks"] if t["track_id"] == candidate["track_id"])
            frames = [o["frame_id"] for o in track["observations"]]
            hop = {"camera": name, "map_camera_id": camera,
                   "track_id": candidate["track_id"],
                   "retrieval_score": candidate["retrieval_score"],
                   "margin_top1_top2": candidate.get("margin_top1_top2"),
                   "first_frame": min(frames), "last_frame": max(frames),
                   "observed_start": time_at(time_maps[name], min(frames)),
                   "observed_end": time_at(time_maps[name], max(frames)),
                   "clock_quality": time_maps[name]["clock_quality"],
                   "evidence_refs": ["frame:%s/%04d.jpg" % (name, int(min(frames)) + 1),
                                     "frame:%s/%04d.jpg" % (name, int(max(frames)) + 1)]}
            break

    profile = new_profile(anchor["target_id"], label, anchor["tracklet_id"],
                          anchor["observed_start"], anchor["observed_end"])
    registry = {"case_id": anchor["case_id"], "observations": [], "applied_events": []}
    review = {"decisions": [], "profile_advanced": False}
    if hop:
        record = observation(anchor["case_id"], anchor["target_id"],
                             anchor["camera_uuid"], hop["map_camera_id"], hop["track_id"],
                             hop["observed_start"], hop["track_id"],
                             osd_uncertainty_seconds=0.2, evidence=hop["evidence_refs"],
                             note="模型候选，等待审核")
        add_observation(registry, record)
        events = [review_event(e["event_type"], anchor["case_id"], anchor["target_id"],
                               record["observation_id"], e.get("reviewer"),
                               e.get("reviewed_at", ""), evidence=e.get("evidence"),
                               reason=e.get("reason"), source=e.get("source", "human_review"),
                               event_id=e.get("event_id")) for e in review_events]
        results = apply_events(registry, events, evidence_root=evidence_root) if events else []
        review["decisions"] = results
        accepted = [r for r in results if r["status"] == "confirmed"]
        profile = attach_candidate(profile, hop["camera"], hop["track_id"],
                                   hop["observed_start"], hop["observed_end"], [])
        candidate_id = profile["candidates"][0]["candidate_id"]
        profile = advance_from_review(profile, candidate_id,
                                      "confirmed" if accepted else "candidate")
        review["profile_advanced"] = profile["version"] == "V2"

    next_round = None
    if hop:
        next_round = spatial(hop["observed_end"], hop["camera"], hop["map_camera_id"])
    return {"anchor": anchor, "steps": steps, "hop": hop, "review": review,
            "profile": {"version": profile["version"],
                        "observations": profile["observations"],
                        "candidates": profile["candidates"]},
            "next_round": next_round, "ledger": scheduler.ledger}
