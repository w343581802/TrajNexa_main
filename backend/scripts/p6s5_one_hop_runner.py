"""S6-15E：单跳真实闭环 runner（2-2 #111 → 2-1 #37 → 下一轮规划）。

不注入真值：检测、跟踪、OSNet 全部真实运行，候选由 top1-top2 间距决定，
审核环节显式标记 simulated=True（回放中的模拟人工确认）。

用法（容器内）：
    M6_PERSON_REID_MODEL_PATH=models/reid/osnet_x0_25_msmt17_b1.onnx \
    python scripts/p6s5_one_hop_runner.py --frames /work/mk/s6b --out /work/mk/s6b/one_hop_chain.json
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
from app.m6_spatial_adapter import bind_channel, camera_spec, load_bindings  # noqa: E402
from app.m6_spatial_planner import plan_spatial_candidates, windows_for_candidates  # noqa: E402
from app.m6_target_candidate_adapter import candidates_from_scores  # noqa: E402
from app.m6_target_profile import attach_candidate, confirm_association, new_profile  # noqa: E402
from app.m6_target_spatial import target_spatial_state  # noqa: E402
from app.m6_window_analysis import analyze_window  # noqa: E402

FPS = 5.0
CANON = {"1-1": "9000000000000037", "2-1": "9000000000000058", "2-2": "9000000000000057"}


def _idx(t, anchor):
    return int(round((t - anchor) * FPS))


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
    parser.add_argument("--map", default="/work/mk/s6b/map_spatial.json")
    parser.add_argument("--bindings", default="/srv/app/config/m6_camera_bindings.json")
    parser.add_argument("--plan", default="/work/mk/s6b/map_only_plan.json")
    parser.add_argument("--anchor", type=float, default=53997.0)
    parser.add_argument("--frames-count", type=int, default=370)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    base = Path(args.frames)
    names = {"9000000000000037": "1-1", "9000000000000058": "2-1", "9000000000000057": "2-2"}
    coverage = (args.anchor, args.anchor + (args.frames_count - 1) / FPS)

    def index(camera, start, end):
        name = names.get(camera)
        if name is None or name == "2-2":
            return {"available": False, "reason": "no_recording"}
        if start < coverage[0] - 0.2 or end > coverage[1] + 0.2:
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
        name = names[camera]
        start, end = action["window"]
        window = analyze_window(str(base / name), start_index=_idx(start, args.anchor),
                                end_index=_idx(end, args.anchor), fps=FPS,
                                osd_anchor_seconds=args.anchor, camera=camera,
                                window_id="%s@%.1f" % (name, start))
        gated = candidates_from_scores(_score(window, str(base / name), reference, engine))
        window["target_candidates"] = gated["target_candidates"]
        state = scheduler.submit(window, appearance_score=(gated["target_candidates"][0]
                                                           ["retrieval_score"]
                                                           if gated["target_candidates"] else None))
        steps.append({"camera": name, "window": [round(start, 1), round(end, 1)],
                      "tracks": len(window.get("tracks") or []),
                      "candidates": gated["target_candidates"],
                      "state": state, "frames_decoded": window["cost"]["frames_decoded"]})
        if state == "needs_review":
            break

    hop = None
    if steps and steps[-1]["state"] == "needs_review":
        candidate = steps[-1]["candidates"][0]
        camera = steps[-1]["camera"]
        confirmed = scheduler.confirm_hop(
            evidence=["tracks:%s#%s" % (camera, candidate["track_id"]),
                      "window:%s" % steps[-1]["window"]], simulated=True)
        hop = {"camera": camera, "track": candidate["track_id"],
               "retrieval_score": candidate["retrieval_score"],
               "margin": candidate.get("margin_top1_top2"),
               "evidence": confirmed["evidence_refs"],
               "simulated_review": True}

    # 第二轮：以确认的 B 为新起点重新规划 C
    next_round = None
    if hop:
        snapshot = json.loads(Path(args.map).read_text(encoding="utf-8"))
        bindings = load_bindings(args.bindings)
        specs = [camera_spec(c, snapshot["spatial_revision"]) for c in snapshot["cameras"]]
        source = bind_channel(snapshot, bindings, hop["camera"][:3])["camera"]
        state = target_spatial_state(source, 54015.6, "electric_bicycle", track_id=hop["track"])
        observed = [CANON[hop["camera"]], CANON["2-2"]]
        candidates = plan_spatial_candidates(state, specs, observed_cameras=observed)
        plans = windows_for_candidates(state, candidates)
        next_round = {"from_camera": hop["camera"], "level": state["level"],
                      "top5": [{"camera": c["candidate_camera"], "relation": c["spatial_relation"],
                                "installation_distance_m": c["installation_distance_m"],
                                "fov_gap_m": c["fov_gap_m"],
                                "window": next(p["window"] for p in plans
                                               if p["camera"] == c["candidate_camera"])}
                               for c in candidates[:5]]}

    profile = new_profile("purple-rider", "2-2", "111", 54006.2, 54008.6)
    if hop:
        profile = attach_candidate(profile, hop["camera"], hop["track"], 54012.6, 54015.6, [])
        profile = confirm_association(profile, profile["candidates"][0]["candidate_id"])

    payload = {"schema": "m6-one-hop-chain/1",
               "exploratory": True,
               "binding_status": "pending_human_review",
               "clock_quality": "uncalibrated",
               "steps": steps, "hop": hop, "next_round": next_round,
               "profile_version": profile["version"],
               "ledger": scheduler.ledger,
               "elapsed_seconds": round(time.time() - started, 1)}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("hop", "profile_version", "ledger",
                                              "next_round", "elapsed_seconds")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
