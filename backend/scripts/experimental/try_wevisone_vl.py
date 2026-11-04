#!/usr/bin/env python3
"""B7-C optional local WeVisOne-VL referring-grounding experiment.

Requires a separate environment with Tencent's official WeVisOne-VL package
and downloaded 2B/4B weights. No CCTV frame is sent to a remote API.
This is not an M6 identity decision and does not modify any truth package.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.wevisone_grounding import (
    WeVisOneParseError,
    parse_referring_response,
    referring_prompt,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="A local PNG/JPEG surveillance frame")
    parser.add_argument("--query", required=True, help="Referring expression for visible objects")
    parser.add_argument("--model", default="tencent/WeVisOne-VL-2B",
                        choices=("tencent/WeVisOne-VL-2B", "tencent/WeVisOne-VL-4B"))
    parser.add_argument("--model-path", default=None,
                        help="Local directory with downloaded weights (offline use)")
    parser.add_argument("--backend", default="transformers", choices=("transformers", "vllm"))
    parser.add_argument("--fixture-response", help="Parse saved raw response WITHOUT loading a model")
    parser.add_argument("--out", required=True, help="Write JSON result to a local path")
    parser.add_argument("--annotated-out", help="Optionally draw candidate boxes on a local image")
    args = parser.parse_args()

    from PIL import Image, ImageDraw

    with Image.open(args.image) as input_image:
        image = input_image.convert("RGB")
    prompt = referring_prompt(args.query)
    if args.fixture_response:
        response = Path(args.fixture_response).read_text(encoding="utf-8")
        latency_s = None
        evidence_mode = "parser_fixture_only"
    else:
        try:
            from wevisone_vl.engine import WeVisOneVL
        except ImportError as exc:
            parser.error("Official WeVisOne-VL is not installed. Follow Tencent/WeVisOne-VL README.")
        # decode_matting=False avoids producing dense masks; pure referring-box experiment.
        model = WeVisOneVL(args.model_path or args.model, backend=args.backend)
        generated = model.predict(image, task="referring", query=args.query,
                                  decode_matting=False, max_new_tokens=512)
        response = generated.response
        latency_s = round(generated.latency_s, 3)
        evidence_mode = "local_model_inference"

    raw = (response or "")[:4000]
    try:
        parsed = parse_referring_response(response, image.width, image.height)
    except WeVisOneParseError as exc:
        # 解析失败必须可见：不当作"没找到"，也不崩掉实验记录。
        parsed = {"status": "parse_failed", "reason": str(exc), "boxes": [],
                  "score": None, "identity_established": False, "review_required": True}
    output = {
        "schema": "trajnexa-wevisone-grounding-probe/1",
        "mode": evidence_mode, "model_id": args.model_path or args.model,
        "backend": args.backend,
        "source_image": args.image, "width": image.width, "height": image.height,
        "query": args.query, "prompt": prompt, "latency_s": latency_s,
        "inference_result": parsed, "raw_response": raw,
        "identity_established": False,
        "affects_tracking": False, "requires_human_review": True,
        "note": "Grounding only; score is unavailable and absence is model-reported, not ground truth.",
    }
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.annotated_out:
        draw = ImageDraw.Draw(image)
        for item in parsed["boxes"]:
            draw.rectangle(item["box_px_xyxy"], outline=(255, 100, 0), width=3)
        annotated = Path(args.annotated_out)
        annotated.parent.mkdir(parents=True, exist_ok=True)
        image.save(annotated)
    print(json.dumps({"status": parsed["status"], "boxes": len(parsed["boxes"]),
                      "mode": evidence_mode, "out": str(destination)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
