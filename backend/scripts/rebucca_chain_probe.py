"""Experimental M5 video -> Rebucca small models -> optional Rebucca VLM.

Run in a Python environment with Rebucca dependencies installed. Reads local test
media and writes JSONL; does not touch TrajNexa's case DB, Rebucca alarms or Map.
This is an OFFLINE integration probe, not the production media ingestion path.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for piece in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


def _load_normalizer():
    # Both applications name their Python package "app"; load TrajNexa's pure
    # contract module by filename to avoid shadowing Rebucca's app namespace.
    path = Path(__file__).resolve().parents[1] / "app" / "m5_rebucca_contract.py"
    spec = importlib.util.spec_from_file_location("traj_m5_rebucca_contract", path)
    if not spec or not spec.loader:
        raise RuntimeError("m5_contract_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize_detections


def _local_llm_endpoint(url: str) -> str:
    try:
        parts = urlsplit(url)
        if (parts.scheme != "http" or
                parts.hostname not in {"localhost", "127.0.0.1", "::1"} or
                not parts.port or parts.username or parts.password or
                parts.query or parts.fragment or parts.path.rstrip("/") != "/v1"):
            raise ValueError
    except ValueError:
        raise ValueError("m5_vlm_must_use_local_loopback_v1") from None
    return url.rstrip("/")


def _crop_jpeg(frame, box, cv2):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    pad_x, pad_y = .06 * (x2 - x1), .06 * (y2 - y1)
    left = max(0, int((x1 - pad_x) * w))
    top = max(0, int((y1 - pad_y) * h))
    right = min(w, math.ceil((x2 + pad_x) * w))
    bottom = min(h, math.ceil((y2 + pad_y) * h))
    if right - left < 16 or bottom - top < 16:
        return None
    ok, encoded = cv2.imencode(".jpg", frame[top:bottom, left:right],
                               [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return encoded.tobytes() if ok else None


def _resolve_model_file(spec: dict, root: Path) -> Path:
    path = Path(spec["model_file"])
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError("m5_model_file_missing")
    return path


def _model_labels(spec: dict, root: Path) -> list[str]:
    labels = spec.get("labels")
    if labels is None and spec.get("labels_source") == "rebucca_coco80_cn":
        # Load Rebucca's OWN D-FINE label order, not a hand-maintained copy.
        source = root / "app" / "utils" / "seed_defaults.py"
        meta = importlib.util.spec_from_file_location("_rebucca_seed_defaults_for_m5", source)
        if not meta or not meta.loader:
            raise ValueError("m5_rebucca_coco_labels_missing")
        mod = importlib.util.module_from_spec(meta)
        meta.loader.exec_module(mod)
        labels = list(mod.COCO_80_CN)
        if len(labels) != 80:
            raise ValueError("m5_invalid_rebucca_coco_labels")
    if labels is None and spec.get("labels_file"):
        path = Path(spec["labels_file"])
        if not path.is_absolute():
            path = root / path
        labels = path.read_text(encoding="utf-8").splitlines()
    if labels is None:
        return []
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise ValueError("m5_invalid_model_labels")
    return [x.strip() for x in labels]


def _load_detectors(config, root, EngineFactory):
    models = config.get("detectors")
    if not isinstance(models, list) or not 1 <= len(models) <= 5:
        raise ValueError("m5_detectors_required")
    loaded = []
    seen_ids = set()
    for spec in models:
        if not isinstance(spec, dict) or not isinstance(spec.get("id"), str):
            raise ValueError("m5_invalid_detector")
        model_id = spec["id"]
        if not model_id or model_id in seen_ids:
            raise ValueError("m5_duplicate_detector_id")
        seen_ids.add(model_id)
        path = _resolve_model_file(spec, root)
        labels = _model_labels(spec, root)
        algo_type = spec.get("algorithm_type", "yolo8")
        if algo_type == "dfine" and not labels:
            raise ValueError("m5_dfine_requires_ordered_class_labels")
        if spec.get("task_type", "detect") not in ("detect", "segment"):
            raise ValueError("m5_only_detect_or_segment_for_probe")
        engine = EngineFactory.create(
            spec.get("inference_engine", "onnxruntime"),
            model_file=str(path),
            labels=labels,
            input_size=(int(spec.get("input_width", 640)),
                        int(spec.get("input_height", 640))),
            conf_threshold=float(spec.get("conf_threshold", .35)),
            iou_threshold=float(spec.get("iou_threshold", .5)),
            algorithm_type=algo_type,
            task_type=spec.get("task_type", "detect"),
            device=spec.get("device", "cpu"),
        )
        if not engine.load():
            raise RuntimeError("m5_model_load_failed:" + model_id)
        class_map = spec.get("class_map", {})
        if not isinstance(class_map, dict):
            raise ValueError("m5_invalid_class_map")
        loaded.append({
            "id": model_id, "name": str(spec.get("name", model_id)),
            "engine": engine, "class_map": class_map,
            "model_sha256": _hash_file(path),
            "engine_kind": str(spec.get("inference_engine", "onnxruntime")),
            "algorithm_type": str(algo_type),
            "task_type": str(spec.get("task_type", "detect")),
            "device": str(spec.get("device", "cpu")),
            "input_size": [int(spec.get("input_width", 640)),
                           int(spec.get("input_height", 640))],
            "conf_threshold": float(spec.get("conf_threshold", .35)),
            "iou_threshold": float(spec.get("iou_threshold", .5)),
        })
    return loaded


def _load_vlms(config):
    items = config.get("vlms", [])
    if not isinstance(items, list) or len(items) > 3:
        raise ValueError("m5_invalid_vlm_config")
    for item in items:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(k), str) and item[k]
            for k in ("id", "model", "prompt")
        ):
            raise ValueError("m5_invalid_vlm_config")
        _local_llm_endpoint(item.get("base_url", "http://127.0.0.1:11434/v1"))
    return items


def _review_frame(frame, detections, vlms, client_class, cv2, budget: int,
                  max_roi_per_frame: int) -> list[dict]:
    if budget <= 0:
        return []
    reviews = []
    for vlm in vlms:
        selected = [d for d in detections
                    if (not vlm.get("detector_ids") or
                        d["model_id"] in vlm["detector_ids"])
                    and (not vlm.get("categories") or
                         d["object_class"] in vlm["categories"])]
        selected.sort(key=lambda d: -d["confidence"])
        for item in selected[:max_roi_per_frame]:
            if len(reviews) >= budget:
                return reviews
            jpeg = _crop_jpeg(frame, item["bbox_normalized"], cv2)
            if jpeg is None:
                continue
            prompt = vlm["prompt"]
            output = {
                "vlm_id": vlm["id"], "vlm_model": vlm["model"],
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "detector_model_id": item["model_id"],
                "candidate_index": item["candidate_index"],
                "roi_sha256": hashlib.sha256(jpeg).hexdigest(),
                "status": "unverified_vlm_candidate",
            }
            try:
                api_key = os.environ.get(vlm.get("api_key_env", ""), "ollama")
                client = client_class(_local_llm_endpoint(
                    vlm.get("base_url", "http://127.0.0.1:11434/v1")),
                    api_key, min(120, float(vlm.get("timeout_seconds", 60))),
                    "OpenAI", vlm["model"])
                result = client.infer(prompt, jpeg)
                output["raw_response"] = str(result or "")[:8192]
                if not result:
                    output["status"] = "vlm_empty_response"
            except Exception:
                output["status"] = "vlm_unavailable"
            reviews.append(output)
    return reviews


def run(args):
    if args.sample_fps <= 0 or args.sample_fps > 30 or not 1 <= args.max_frames <= 200:
        raise ValueError("m5_invalid_sampling_limits")
    if not 0 <= args.max_vlm_calls <= 100 or not 1 <= args.max_roi_per_frame <= 10:
        raise ValueError("m5_invalid_vlm_budget")
    root = Path(args.rebucca_root).resolve()
    if not (root / "app" / "analysis" / "engines" / "factory.py").is_file():
        raise ValueError("m5_invalid_rebucca_root")
    media = Path(args.media).resolve()
    if not media.is_file() or media.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError("m5_invalid_test_media")
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("m5_invalid_config")
    normalizer = _load_normalizer()
    # Import Rebucca only after loading the TrajNexa normalizer to avoid app
    # package collisions. This probe must run in Rebucca's Python environment.
    sys.path.insert(0, str(root))
    import cv2
    from app.analysis.engines.factory import EngineFactory
    from app.utils.LLMUtils import LLMUtils

    models = _load_detectors(config, root, EngineFactory)
    vlms = _load_vlms(config) if args.with_vlm else []
    source_sha = _hash_file(media)
    ext = media.suffix.lower()
    if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        source_kind = "image"
    elif ext in (".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"):
        source_kind = "video"
    else:
        raise ValueError("m5_unsupported_test_media_type")
    if source_kind == "image":
        frame = cv2.imread(str(media))
        if frame is None:
            raise ValueError("m5_image_decode_failed")
        frames = [(0, None, frame)]
    else:
        cap = cv2.VideoCapture(str(media))
        if not cap.isOpened():
            raise ValueError("m5_video_decode_failed")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25)
        if not math.isfinite(fps) or not 0 < fps <= 240:
            fps = 25
        step = max(1, round(fps / args.sample_fps))

        def sampled_frames():
            index = 0
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        return
                    if index % step == 0:
                        pts = cap.get(cv2.CAP_PROP_POS_MSEC)
                        pts = round(pts) if math.isfinite(pts) and pts >= 0 else None
                        yield index, pts, frame
                    index += 1
            finally:
                cap.release()
        frames = sampled_frames()

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    remaining_vlm = args.max_vlm_calls if args.with_vlm else 0
    completed = 0
    with output.open("x", encoding="utf-8") as writer:
        header = {
            "type": "source", "status": "test_only_not_evidence",
            "source_name": media.name, "source_sha256": source_sha,
            "source_kind": source_kind,
            "rebucca_engine_models": [
                {"id": m["id"], "model_sha256": m["model_sha256"],
                 "inference_engine": m["engine_kind"]} for m in models
            ],
            "frame_time_status": "opencv_pos_msec_unverified",
        }
        writer.write(json.dumps(header, ensure_ascii=False, allow_nan=False) + "\n")
        for index, pts, frame in frames:
            if completed >= args.max_frames:
                break
            h, w = frame.shape[:2]
            all_candidates, model_runs = [], []
            for m in models:
                try:
                    raw = m["engine"].detect(frame)
                    proposals = normalizer(
                        raw, width=w, height=h, model_id=m["id"],
                        model_name=m["name"], class_map=m["class_map"])
                    all_candidates.extend(proposals)
                    model_runs.append({"model_id": m["id"], "status": "returned",
                                       "candidate_count": len(proposals)})
                except Exception:
                    model_runs.append({"model_id": m["id"], "status": "inference_error"})
            reviews = _review_frame(
                frame, all_candidates, vlms, LLMUtils, cv2, remaining_vlm,
                args.max_roi_per_frame)
            remaining_vlm -= len(reviews)
            row = {
                "type": "frame", "status": "unverified_candidates",
                "source_sha256": source_sha, "frame_index": index,
                "requested_offset_ms_unverified": pts,
                "image_size": [w, h], "model_runs": model_runs,
                "detections": all_candidates, "vlm_reviews": reviews,
                "identity_established": False, "trajectory_written": False,
            }
            writer.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
            completed += 1
    if source_kind == "video":
        frames.close()
    print("Rebucca test-only probe finished: {} frames -> {}".format(completed, output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebucca-root", required=True)
    parser.add_argument("--media", required=True, help="Local test image or video")
    parser.add_argument("--config", required=True, help="Local JSON model configuration")
    parser.add_argument("--output", required=True, help="New JSONL path (will not overwrite)")
    parser.add_argument("--sample-fps", type=float, default=1.0)
    parser.add_argument("--max-frames", type=int, default=20)
    parser.add_argument("--with-vlm", action="store_true",
                        help="Explicit opt-in to send cropped ROIs to loopback VLM")
    parser.add_argument("--max-vlm-calls", type=int, default=8)
    parser.add_argument("--max-roi-per-frame", type=int, default=2)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
