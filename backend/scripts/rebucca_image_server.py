"""M5 local image-only model service, running inside an installed Rebucca environment.

Reuses Rebucca EngineFactory and model pre/post-processing; no Rebucca ORM,
alarm rules or TrajNexa case DB are imported. One HTTPServer serializes
inference so a small Windows GPU is not loaded with duplicate model instances.

Start (PowerShell): python backend/scripts/rebucca_image_server.py
  --rebucca-root D:/Rebucca --config D:/test/m5-probe.json --port 8766
Default bind is loopback only; for Docker-to-Windows access explicitly bind to a
reachable host interface AND set M5_REBUCCA_TOKEN (restrict firewall as well).
"""
from __future__ import annotations

import argparse
import base64
import binascii
from http.server import HTTPServer, BaseHTTPRequestHandler
import hmac
import json
import os
from pathlib import Path
import sys
import time


MAX_HTTP_BYTES = 24 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


class ImageServiceError(ValueError):
    pass


def _load_models(rebucca_root: Path, config_path: Path):
    if not (rebucca_root / "app" / "analysis" / "engines" / "factory.py").is_file():
        raise ImageServiceError("invalid_rebucca_root")
    # sibling probe imports the normalization module by filename; its "app" is
    # not imported until the Rebucca root has been inserted.
    import rebucca_chain_probe as probe
    normalizer = probe._load_normalizer()
    sys.path.insert(0, str(rebucca_root))
    import cv2
    import numpy as np
    from app.analysis.engines.factory import EngineFactory

    config = json.loads(config_path.read_text(encoding="utf-8"))
    models = probe._load_detectors(config, rebucca_root, EngineFactory)
    return models, normalizer, cv2, np


class ImageService:
    def __init__(self, models, normalize, cv2, np):
        self.models = {m["id"]: m for m in models}
        self.normalize = normalize
        self.cv2 = cv2
        self.np = np

    def list_models(self):
        return [{"id": m["id"], "name": m["name"],
                 "engine": m["engine_kind"], "model_sha256": m["model_sha256"],
                 "algorithm_type": m.get("algorithm_type", "detect"),
                 "task_type": m.get("task_type", "detect"),
                 "device": m.get("device", "cpu"),
                 "input_size": m.get("input_size", [640, 640]),
                 "conf_threshold": m.get("conf_threshold"),
                 "iou_threshold": m.get("iou_threshold")}
                for m in self.models.values()]

    def infer(self, body: dict):
        if not isinstance(body, dict):
            raise ImageServiceError("invalid_request")
        selected = body.get("model_ids")
        if not isinstance(selected, list) or not selected or len(selected) > 5 or (
                not all(isinstance(m, str) and m in self.models for m in selected)
                or len(set(selected)) != len(selected)):
            raise ImageServiceError("invalid_model_selection")
        threshold = body.get("min_confidence", .35)
        if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not .2 <= threshold <= 1:
            raise ImageServiceError("invalid_threshold")
        encoded = body.get("image_base64")
        if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 8):
            raise ImageServiceError("image_too_large")
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ImageServiceError("invalid_image") from None
        if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
            raise ImageServiceError("image_too_large")
        image = self.cv2.imdecode(self.np.frombuffer(image_bytes, self.np.uint8), self.cv2.IMREAD_COLOR)
        if image is None:
            raise ImageServiceError("invalid_image")
        h, w = image.shape[:2]
        if not w or not h or w * h > MAX_IMAGE_PIXELS:
            raise ImageServiceError("image_too_large")
        results, candidates = [], []
        for mid in selected:
            m = self.models[mid]
            started = time.monotonic()
            try:
                raw = m["engine"].detect(image)
                proposals = self.normalize(
                    raw, width=w, height=h, model_id=mid,
                    model_name=m["name"], class_map=m["class_map"])
                proposals = [p for p in proposals if p["confidence"] >= threshold]
                candidates.extend(proposals)
                status = "returned_unverified"  # Engine implementations may suppress internal errors.
            except Exception:
                status, proposals = "inference_error", []
            results.append({"model_id": mid, "status": status,
                            "candidate_count": len(proposals),
                            "elapsed_seconds": round(time.monotonic() - started, 3)})
        return {"status": "candidates_unverified", "image_size": [w, h],
                "model_runs": results, "candidates": candidates,
                "candidate_count": len(candidates)}


def make_handler(service: ImageService, token: str = ""):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # Never log image bytes, request bodies, tokens or file names.
            return

        def _authorized(self):
            if not token:
                return True
            received = self.headers.get("X-TrajNexa-Token", "")
            return hmac.compare_digest(received, token)

        def _json(self, code: int, value: dict):
            payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if not self._authorized():
                self._json(403, {"error": "forbidden"})
            elif self.path == "/health":
                self._json(200, {"status": "ready", "model_count": len(service.models)})
            elif self.path == "/models":
                self._json(200, {"models": service.list_models()})
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self):
            if not self._authorized():
                self._json(403, {"error": "forbidden"})
                return
            if self.path != "/detect":
                self._json(404, {"error": "not_found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                size = -1
            if size <= 0 or size > MAX_HTTP_BYTES:
                self._json(413, {"error": "request_too_large"})
                return
            try:
                data = json.loads(self.rfile.read(size))
                response = service.infer(data)
            except (ImageServiceError, ValueError, UnicodeDecodeError):
                self._json(422, {"error": "invalid_image_request"})
                return
            except Exception:
                self._json(500, {"error": "inference_unavailable"})
                return
            self._json(200, response)
    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rebucca-root", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)
    args = p.parse_args()
    token = os.environ.get("M5_REBUCCA_TOKEN", "")
    if args.bind not in ("127.0.0.1", "localhost", "::1") and len(token) < 16:
        p.error("Non-loopback bind requires M5_REBUCCA_TOKEN >= 16 chars + firewall isolation")
    if not 1024 <= args.port <= 65535:
        p.error("Invalid port")
    models, normalizer, cv2, np = _load_models(
        Path(args.rebucca_root).resolve(), Path(args.config).resolve())
    service = ImageService(models, normalizer, cv2, np)
    server = HTTPServer((args.bind, args.port), make_handler(service, token))
    print("Rebucca image service ready: {} model(s) on {}:{}".format(
        len(models), args.bind, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
