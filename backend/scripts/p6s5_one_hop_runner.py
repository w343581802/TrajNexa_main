"""S6-15E：单跳真实闭环 runner（B4.1 修正版）。

与上一版的区别：
  1. 每路录像用自己的 frame_time_map（2-1 锚点 53998，1-1/2-2 为 53997），不再共用 --anchor；
  2. B 的起止时间来自模型候选 Tracklet 的实际观测帧，不再写 54012.6/54015.6 常量；
  3. 审核以 review_event 表达，simulated 只写模拟结果，不推进正式 TargetProfile。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.m6_camera_scheduler import CameraScheduler  # noqa: E402
from app.m6_person_reid import _get_engine  # noqa: E402
from app.m6_review_events import apply_events, review_event  # noqa: E402
from app.m6_spatial_adapter import bind_channel, camera_spec, load_bindings  # noqa: E402
from app.m6_spatial_planner import plan_spatial_candidates, windows_for_candidates  # noqa: E402
from app.m6_target_candidate_adapter import candidates_from_scores  # noqa: E402
from app.m6_target_profile import attach_candidate, new_profile  # noqa: E402
from app.m6_target_spatial import target_spatial_state  # noqa: E402
from app.m6_time_map import frame_time_map, span_for_window, time_at  # noqa: E402
from app.m6_truth_registry import add_observation, observation  # noqa: E402
from app.m6_window_analysis import analyze_window  # noqa: E402

CANON = {"1-1": "9000000000000037", "2-1": "9000000000000058", "2-2": "9000000000000057"}


def _load_maps(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    maps = {}
    for item in payload["maps"]:
        maps[item["label"]] = frame_time_map(
            item["recording_id"], item["camera_uuid"], item["anchor_seconds"], item["fps"],
            start_index=item.get("start_index", 0), frame_count=item.get("frame_count"),
            clock_quality=item.get("clock_quality", "uncalibrated"),
            offset_seconds=item.get("offset_seconds", 0.0), revision=item.get("revision", 1))
    return maps


def _score(window, frames_dir, reference, engine, max_tracks=25):
    scored = []
    for track in (window.get("tracks") or [])[:max_tracks]:
        best = None
        for obs in track["observations"][:2]:
            image = cv2.imread("%s/%04d.jpg" % (frames_dir, int(obs["frame_id"]) + 1))
            if image is None:
                continue
            try:
                _, vectors = engine.extract_embeddings(image, [[int(v) for v in obs["bbox"]]])
            except Exception:
                continue
            vector = vectors[0] / np.linalg.norm(vectors[0])
            score = float(np.dot(reference, vector))
            best = score if best is None else max(best, score)
        scored.append({"track_id": track["track_id"],
                       "retrieval_score": None if best is None else round(best, 3)})
    return scored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", required=True)
    parser.add_argument("--time-maps", required=True)
    parser.add_argument("--map", default="/work/mk/s6b/map_spatial.json")
    parser.add_argument("--bindings", default="/srv/app/config/m6_camera_bindings.json")
    parser.add_argument("--plan", default="/work/mk/s6b/map_only_plan.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    base = Path(args.frames)
    maps = _load_maps(args.time_maps)
    labels = {v: k for k, v in CANON.items()}

    def index(camera, start, end):
        name = labels.get(camera)
        if name is None or name == "2-2":
            return {"available": False, "reason": "no_recording"}
        time_map = maps[name]
        first = time_at(time_map, time_map["start_index"])
        last = time_at(time_map, time_map["start_index"] + time_map["frame_count"] - 1)
        if start < first - 0.2 or end > last + 0.2:
            return {"available": False, "reason": "outside_frozen_frames"}
        return {"available": True}

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    forward = {w["camera"]: w for w in plan["windows_forward"]}
    predictions = []
    for camera in ["9000000000000037", "9000000000000058", "9000000000000039",
                   "9000000000000038"]:
        window = forward[camera]
        predictions.append({"camera": camera, "priority": window["priority"],
                            "estimated_window": window["estimated_window"],
                            "search_window": window["window"], "exploratory": True,
                            "uncertainties": window["uncertainties"]})

    reference = np.load(str(base / "ref_purple.npy"))
    engine, _ = _get_engine()
    scheduler = CameraScheduler(predictions, recording_index=index,
                                params={"segment_seconds": 6.0, "max_decoded_frames": 100000},
                                mode="segmented")
    steps = []
    started = time.time()
    for _ in range(80):
        action = scheduler.next_action()
        if action is None:
            break
        camera = action["action"]["camera"]
        name = labels[camera]
        start, end = action["window"]
        span = span_for_window(maps[name], start, end)
        window = analyze_window(str(base / name), start_index=span["start_index"],
                                end_index=span["end_index"], fps=maps[name]["fps"],
                                osd_anchor_seconds=maps[name]["anchor_seconds"],
                                camera=camera, window_id="%s@%.1f" % (name, start))
        gated = candidates_from_scores(_score(window, str(base / name), reference, engine))
        window["target_candidates"] = gated["target_candidates"]
        state = scheduler.submit(window, appearance_score=(gated["target_candidates"][0]
                                                           ["retrieval_score"]
                                                           if gated["target_candidates"] else None))
        steps.append({"camera": name, "scheduled_window": [round(start, 1), round(end, 1)],
                      "actual_frame_span": [span["start_index"], span["end_index"]],
                      "actual_seconds": [span["start_seconds"], span["end_seconds"]],
                      "tracks": len(window.get("tracks") or []),
                      "candidates": gated["target_candidates"], "state": state,
                      "frames_decoded": window["cost"]["frames_decoded"]})
        if state == "needs_review":
            break

    hop = None
    review = None
    profile = new_profile("purple-rider", "2-2", "111", 54006.2, 54008.6)
    registry = {"case_id": "purple-rider", "observations": []}
    if steps and steps[-1]["state"] == "needs_review":
        step = steps[-1]
        candidate = step["candidates"][0]
        name = step["camera"]
        time_map = maps[name]
        track = next(t for t in window["tracks"] if t["track_id"] == candidate["track_id"])
        frames = [o["frame_id"] for o in track["observations"]]
        hop = {"camera": name, "map_camera_id": CANON[name], "track_id": candidate["track_id"],
               "retrieval_score": candidate["retrieval_score"],
               "margin_top1_top2": candidate.get("margin_top1_top2"),
               "first_frame": min(frames), "last_frame": max(frames),
               "observed_start": time_at(time_map, min(frames)),
               "observed_end": time_at(time_map, max(frames)),
               "clock_quality": time_map["clock_quality"],
               "evidence_refs": ["frame:%s/%04d.jpg" % (name, int(min(frames)) + 1),
                                 "frame:%s/%04d.jpg" % (name, int(max(frames)) + 1)]}
        snapshot = json.loads(Path(args.map).read_text(encoding="utf-8"))
        bindings = load_bindings(args.bindings)
        specs = [camera_spec(c, snapshot["spatial_revision"]) for c in snapshot["cameras"]]
        specs_by_id = {c["map_camera_id"]: c for c in specs}
        spec = specs_by_id[hop["map_camera_id"]]
        record = observation("purple-rider", "purple-rider", spec["camera_uuid"],
                             hop["map_camera_id"], candidate["track_id"],
                             hop["observed_start"], hop["track_id"],
                             osd_uncertainty_seconds=0.2, evidence=hop["evidence_refs"],
                             note="模型候选，等待人工审核")
        try:
            add_observation(registry, record)
            event = review_event("confirm", "purple-rider", "purple-rider",
                                 record["observation_id"], "simulation-operator",
                                 "2026-11-03T00:00:00Z", evidence=hop["evidence_refs"],
                                 reason="offline_replay_simulation", source="simulated")
            results = apply_events(registry, [event])
            review = {"event_id": event["event_id"], "source": "simulated",
                      "status": results[0]["status"],
                      "note": "模拟审核只写模拟结果，正式 V2 仍需真实审核事件"}
        except Exception as exc:
            review = {"status": "error", "code": str(exc)}
        profile = attach_candidate(profile, hop["camera"], hop["track_id"],
                                   hop["observed_start"], hop["observed_end"], [])

    next_round = None
    if hop:
        snapshot = json.loads(Path(args.map).read_text(encoding="utf-8"))
        bindings = load_bindings(args.bindings)
        specs = [camera_spec(c, snapshot["spatial_revision"]) for c in snapshot["cameras"]]
        source = bind_channel(snapshot, bindings, hop["camera"])["camera"]
        state = target_spatial_state(source, hop["observed_end"], "electric_bicycle",
                                     track_id=hop["track_id"])
        observed = [CANON[hop["camera"]], CANON["2-2"]]
        candidates = plan_spatial_candidates(state, specs, observed_cameras=observed)
        plans = windows_for_candidates(state, candidates)
        next_round = {"from_camera": hop["camera"], "level": state["level"],
                      "source_time": hop["observed_end"],
                      "top5": [{"camera": c["candidate_camera"], "relation": c["spatial_relation"],
                                "installation_distance_m": c["installation_distance_m"],
                                "fov_gap_m": c["fov_gap_m"],
                                "window": next(p["window"] for p in plans
                                               if p["camera"] == c["candidate_camera"])}
                               for c in candidates[:5]]}

    payload = {"schema": "m6-one-hop-chain/2", "exploratory": True,
               "binding_status": "pending_human_review",
               "clock_quality": "uncalibrated", "steps": steps, "hop": hop,
               "review": review, "profile": {"version": profile["version"],
                                             "candidates": profile["candidates"]},
               "next_round": next_round, "ledger": scheduler.ledger,
               "elapsed_seconds": round(time.time() - started, 1)}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("hop", "review", "next_round", "ledger",
                                              "elapsed_seconds")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
