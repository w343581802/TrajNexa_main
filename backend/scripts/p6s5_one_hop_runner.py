"""B4.2：由真值包里的已确认起点自动执行 A→B 检索。

脚本不知道 B 是哪台镜头、什么时间、哪条轨迹；这些只用于运行后的评估。
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

from app.m6_one_hop import run_one_hop  # noqa: E402
from app.m6_person_reid import _get_engine  # noqa: E402
from app.m6_spatial_adapter import bind_channel, camera_spec, load_bindings  # noqa: E402
from app.m6_spatial_planner import plan_spatial_candidates, windows_for_candidates  # noqa: E402
from app.m6_target_reference import extract_reference  # noqa: E402
from app.m6_target_spatial import target_spatial_state  # noqa: E402
from app.m6_time_map import frame_time_map, time_at  # noqa: E402
from app.m6_truth_registry import starting_observation  # noqa: E402
from app.m6_observer_batch import select_batch  # noqa: E402
from app.m6_window_analysis import analyze_window  # noqa: E402
from app.m6_view_quality import DEFAULT_PARAMS as QUALITY_DEFAULTS  # noqa: E402
from app.m6_view_quality import evaluate_target  # noqa: E402


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


def _unwrap_tracks(payload):
    """兼容 {results:{bytetrack:{tracks:[...]}}} 与裸列表两种形态。"""
    node = payload
    for _ in range(6):
        if isinstance(node, list):
            return node
        if not isinstance(node, dict):
            return []
        if isinstance(node.get("tracks"), list):
            return node["tracks"]
        for key in ("results", "bytetrack", "bytetracker"):
            if key in node:
                node = node[key]
                break
        else:
            node = next(iter(node.values()), [])
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth-package", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--anchor-tracks", required=True, help="A 录像的跟踪输出，用于参考特征")
    parser.add_argument("--time-maps", required=True)
    parser.add_argument("--map", default="/work/mk/s6b/map_spatial.json")
    parser.add_argument("--bindings", default="/srv/app/config/m6_camera_bindings.json")
    parser.add_argument("--review-events", default=None)
    parser.add_argument("--segment-seconds", type=float, default=6.0)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--batch-mode", action="store_true")
    parser.add_argument("--quality-mode", default="off",
                        choices=["off", "observe_only", "prioritize", "verify_all"])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    started = time.time()
    package = json.loads(Path(args.truth_package).read_text(encoding="utf-8"))
    anchor = starting_observation(package, args.target)
    label = anchor["camera_label"]
    if not label:
        raise SystemExit("anchor_camera_label_missing")
    maps = _load_maps(args.time_maps)
    snapshot = json.loads(Path(args.map).read_text(encoding="utf-8"))
    bindings = load_bindings(args.bindings)
    specs = [camera_spec(c, snapshot["spatial_revision"]) for c in snapshot["cameras"]]
    labels = {}
    for channel, binding in bindings["channels"].items():
        labels[binding["camera_uuid"]] = channel
    camera_ids = {c["map_camera_id"]: labels.get(c["camera_uuid"], c.get("label", ""))
                  for c in specs}
    anchor["camera_ids"] = camera_ids
    anchor["camera_uuids"] = {name: uuid for uuid, name in labels.items()}
    anchor["camera_label"] = label

    engine, model_id = _get_engine()
    tracks = _unwrap_tracks(json.loads(Path(args.anchor_tracks).read_text(encoding="utf-8")))
    source_track = next((t for t in tracks if str(t["track_id"]) == str(anchor["tracklet_id"])),
                        None)
    if source_track is None:
        raise SystemExit("anchor_tracklet_not_found")
    if anchor.get("observed_end") is None:
        frames = [int(o["frame_id"]) for o in source_track["observations"]]
        anchor["observed_end"] = time_at(maps[label], max(frames))
        anchor["observed_end_source"] = "tracklet_last_frame"

    def read_image(frame_id):
        return cv2.imread("%s/%s/%04d.jpg" % (args.frames, label, int(frame_id) + 1))

    reference = extract_reference(
        read_image,
        lambda image, bbox: engine.extract_embeddings(image, [[int(v) for v in bbox]])[1][0],
        source_track["observations"], model_id=Path(str(model_id)).name)

    def plan_from(camera_label, seconds, exclude_labels):
        bound = bind_channel(snapshot, bindings, camera_label)
        if bound["camera"] is None:
            raise SystemExit("camera_binding_unresolved:%s" % camera_label)
        state = target_spatial_state(bound["camera"], seconds, "electric_bicycle")
        observed = [cid for cid, name in camera_ids.items() if name in exclude_labels]
        candidates = plan_spatial_candidates(state, specs, observed_cameras=observed)
        plans = windows_for_candidates(state, candidates)
        return state, candidates, plans

    stage = {}

    def spatial(observed_end, camera_label, map_camera_id):
        _, candidates, plans = plan_from(camera_label, observed_end, [camera_label, label])
        stage["candidates"] = candidates
        return {"source_time": observed_end, "from_camera": camera_label,
                "top5": [{"camera": c["candidate_camera"], "relation": c["spatial_relation"],
                          "installation_distance_m": c["installation_distance_m"],
                          "fov_gap_m": c["fov_gap_m"],
                          "window": next(p["window"] for p in plans
                                         if p["camera"] == c["candidate_camera"])}
                         for c in candidates[:5]]}


    _, _, initial_plans = plan_from(label, anchor["observed_end"], [label])
    predictions = [{"camera": p["camera"], "priority": p["priority"],
                    "estimated_window": p["estimated_window"], "search_window": p["window"],
                    "exploratory": True, "uncertainties": p["uncertainties"]}
                   for p in initial_plans[:6] if camera_ids.get(p["camera"])]

    def analyze(name, span, window_id):
        time_map = maps[name]
        return analyze_window("%s/%s" % (args.frames, name), start_index=span["start_index"],
                              end_index=span["end_index"], fps=time_map["fps"],
                              osd_anchor_seconds=time_map["anchor_seconds"],
                              camera=name, window_id=window_id)

    quality_by_camera = {}
    batches = []
    quality_state = {"ms": 0.0, "mode": args.quality_mode}

    def observe_quality(sched, name, result, window):
        candidates = result.get("target_candidates") or []
        if not candidates or args.quality_mode == "off":
            return
        track = next((t for t in (result.get("tracks") or [])
                      if t["track_id"] == candidates[0]["track_id"]), None)
        if track is None:
            return
        started_q = time.time()
        samples = []
        for obs in track["observations"][:5]:
            image = cv2.imread("%s/%s/%04d.jpg" % (args.frames, name, int(obs["frame_id"]) + 1))
            if image is not None:
                samples.append({"image": image, "bbox": obs["bbox"]})
        quality = evaluate_target(samples, {"mode": args.quality_mode,
                                            "enabled": args.quality_mode != "off"})
        quality["cost_ms"] = round((time.time() - started_q) * 1000.0, 1)
        quality_state["ms"] += quality["cost_ms"]
        quality_by_camera[name] = quality
        def _win(action):
            return action.get("estimated_window") or action.get("search_window") or (0, 0)
        peers = [{"camera": camera_ids.get(a["camera"], a["camera"]), "window": _win(a),
                  "selected": True}
                 for a in sched.actions
                 if a["camera"] != name and a["state"] in ("planned", "segmented")
                 and camera_ids.get(a["camera"], a["camera"])
                 and _win(a)[0] <= window[1] and _win(a)[1] >= window[0]]
        batch = select_batch([{"camera": name, "window": window, "selected": True}] + peers,
                             batch_size=args.batch_size)
        batches.append({"batch_id": batch["batch_id"], "lead": name,
                        "members": [m["camera"] for m in batch["members"]],
                        "windows": [m["window"] for m in batch["members"]]})
        if args.quality_mode in ("prioritize", "verify_all") and quality["band"] == "low":
            others = [a["camera"] for a in sched.actions
                      if a["camera"] != name and a["state"] in ("planned", "segmented")]
            sched.boost_cameras(others)
            quality["boosted"] = others

    def score(name, window):
        scored = []
        for track in (window.get("tracks") or [])[:25]:
            best = None
            for obs in track["observations"][:2]:
                image = cv2.imread("%s/%s/%04d.jpg" % (args.frames, name, int(obs["frame_id"]) + 1))
                if image is None:
                    continue
                try:
                    vector = engine.extract_embeddings(image, [[int(v) for v in obs["bbox"]]])[1][0]
                except Exception:
                    continue
                vector = vector / np.linalg.norm(vector)
                value = float(np.dot(reference["vector"], vector))
                best = value if best is None else max(best, value)
            scored.append({"track_id": track["track_id"],
                           "retrieval_score": None if best is None else round(best, 3)})
        return scored

    review_events = []
    if args.review_events:
        review_events = json.loads(Path(args.review_events).read_text(encoding="utf-8"))

    result = run_one_hop(anchor=anchor, time_maps=maps, predictions=predictions,
                         analyze=analyze, score=score, spatial=spatial,
                         review_events=review_events, max_actions=80,
                         segment_seconds=args.segment_seconds, on_window=observe_quality,
                         batch_size=args.batch_size if args.batch_mode else 1)
    payload = {"schema": "m6-one-hop-chain/3", "exploratory": True,
               "binding_status": anchor["binding_status"],
               "clock_quality": "uncalibrated",
               "reference": {"model_id": reference["model_id"],
                             "sources": reference["sources"],
                             "failures": reference["failures"]},
               "candidates_plan": [{"camera": c["candidate_camera"],
                                    "priority": c["priority"], "reasons": c["reasons"]}
                                   for c in stage.get("candidates", [])],
               "anchor": anchor, "steps": result["steps"], "hop": result["hop"],
               "candidate_pool": result["candidate_pool"],
               "session": result["session"],
               "review": result["review"], "profile": result["profile"],
               "next_round": result["next_round"], "ledger": result["ledger"],
               "quality": {"mode": args.quality_mode,
                           "cameras": quality_by_camera,
                           "cost_ms": round(quality_state["ms"], 1)},
               "batches": {"count": len(batches), "items": batches,
                           "cross_camera_identity_merged": False},
               "elapsed_seconds": round(time.time() - started, 1)}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("anchor", "reference", "hop", "review",
                                              "profile", "next_round", "ledger",
                                              "elapsed_seconds")},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
