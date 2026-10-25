"""Opt-in PP-OCRv6 text-line recognition benchmark for a single CCTV clock.

No uploads, API changes, model training or production inference changes.
Run in the existing optional PaddleOCR worker container or local PaddleOCR 3.7 env.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np

from app.overlay_ocr import _parse_time

VALID_MODELS = ('PP-OCRv6_medium_rec', 'PP-OCRv6_small_rec')
DEFAULT_ROI = (0.55, 0.00, 0.99, 0.095)


def parse_roi(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(n.strip()) for n in value.split(','))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('ROI must be x1,y1,x2,y2 as ratios') from exc
    if len(values) != 4 or not (0 <= values[0] < values[2] <= 1 and
                                    0 <= values[1] < values[3] <= 1):
        raise argparse.ArgumentTypeError('ROI: 0 <= x1 < x2 <= 1, 0 <= y1 < y2 <= 1')
    return values  # type: ignore[return-value]


def crop_roi(image: np.ndarray, roi: tuple[float, float, float, float]):
    if image is None or image.ndim != 3 or image.shape[0] < 20 or image.shape[1] < 20:
        raise ValueError('Unable to decode a sufficiently large color image')
    height, width = image.shape[:2]
    x1, y1, x2, y2 = roi
    coords = (int(x1 * width), int(y1 * height),
              min(width, int(np.ceil(x2 * width))), min(height, int(np.ceil(y2 * height))))
    left, top, right, bottom = coords
    cropped = image[top:bottom, left:right]
    if cropped.size == 0 or cropped.shape[0] < 12 or cropped.shape[1] < 40:
        raise ValueError('Selected clock ROI is empty or too small')
    return cropped.copy(), coords


def make_variants(crop: np.ndarray) -> dict[str, np.ndarray]:
    # TextRecognition already expects one text line. Do not run the detection model.
    larger = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(larger, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, binary = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)
    return {
        'original': crop,
        'upscaled_2x': larger,
        'clahe_2x': cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR),
        'light_text_2x': cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR),
    }


def extract_result(prediction: Any) -> tuple[str | None, float | None]:
    """Official TextRecognition result uses res.rec_text and res.rec_score."""
    if isinstance(prediction, (list, tuple)):
        if len(prediction) != 1:
            return None, None
        prediction = prediction[0]
    if not isinstance(prediction, dict):
        body = getattr(prediction, 'json', None)
        prediction = body() if callable(body) else body
    if not isinstance(prediction, dict):
        return None, None
    data = prediction.get('res', prediction)
    if not isinstance(data, dict):
        return None, None
    raw = data.get('rec_text')
    score = data.get('rec_score')
    if not isinstance(raw, str):
        return None, None
    try:
        numeric_score = round(float(score), 5) if score is not None else None
    except (TypeError, ValueError):
        numeric_score = None
    return raw, numeric_score


def normalize_expected(expected: str | None) -> str | None:
    if expected is None:
        return None
    try:
        # Only ground truth provided by the tester. Never use it to change predictions.
        return datetime.fromisoformat(expected.replace(' ', 'T')).strftime('%Y-%m-%dT%H:%M:%S')
    except ValueError as exc:
        raise argparse.ArgumentTypeError('--expected requires YYYY-MM-DD HH:MM:SS') from exc


def evaluate(raw: str | None, expected: str | None) -> dict:
    parsed, _ = _parse_time([{'text': raw or '', 'region': 'top_right'}])
    return {
        'parsed_clock_unverified': parsed,
        'exact_match_to_manual_truth': None if expected is None else (parsed == expected),
        'requires_review': True,
    }


def run_experiment(path: Path, *, roi=DEFAULT_ROI,
                   models=('PP-OCRv6_medium_rec',), expected=None,
                   predictor_factory=None) -> dict:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    decoded = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    cropped, coords = crop_roi(decoded, roi)
    variants = make_variants(cropped)
    output = {
        'experiment': 'PP-OCRv6_CCTV_clock_line_recognition',
        'source_sha256': source_hash,
        'roi_pixels_xyxy': coords,
        'roi_ratio_xyxy': list(roi),
        'raw_source_preserved': True,
        'baseline_pipeline_modified': False,
        'manual_truth_for_scoring_only': expected,
        'results': [],
    }
    if predictor_factory is None:
        from paddleocr import TextRecognition  # optional dependency, only here
        predictor_factory = lambda name: TextRecognition(model_name=name, device='cpu')
    for name in models:
        if name not in VALID_MODELS:
            raise ValueError(f'Unsupported model: {name}')
        init_start = time.perf_counter()
        model = predictor_factory(name)
        init_seconds = round(time.perf_counter() - init_start, 3)
        for variant_name, image in variants.items():
            t0 = time.perf_counter()
            results = model.predict(input=image, batch_size=1)
            took = round(time.perf_counter() - t0, 3)
            raw, confidence = extract_result(results)
            output['results'].append({
                'model': name,
                'variant': variant_name,
                'raw_text': raw,
                'model_score_not_truth_confidence': confidence,
                'model_init_seconds': init_seconds,
                'inference_seconds': took,
                **evaluate(raw, expected),
            })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, help='local CCTV screenshot (not uploaded)')
    parser.add_argument('--roi', type=parse_roi, default=DEFAULT_ROI,
                        help='normalized x1,y1,x2,y2; default upper-right strip')
    parser.add_argument('--models', nargs='+', choices=VALID_MODELS,
                        default=['PP-OCRv6_medium_rec'],
                        help='use medium by default; optionally also try small')
    parser.add_argument('--expected', help='human-read exact timestamp for scoring only')
    parser.add_argument('--output', type=Path, help='optional path to save JSON results')
    parser.add_argument('--preview', type=Path, help='optional cropped local preview image')
    args = parser.parse_args()
    expected = normalize_expected(args.expected)
    if args.preview is not None:
        image = cv2.imdecode(np.fromfile(str(args.image), dtype=np.uint8), cv2.IMREAD_COLOR)
        crop, _ = crop_roi(image, args.roi)
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.preview), crop):
            parser.error('cannot write preview image')
    try:
        report = run_experiment(args.image, roi=args.roi, models=args.models, expected=expected)
    except (ValueError, FileNotFoundError, ImportError, RuntimeError) as exc:
        parser.exit(2, f'PP-OCRv6 benchmark failed: {exc}\n')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
