"""Compare wide/tight CCTV clock crops with the existing PP-OCRv6 benchmark.

No changes to production OCR, original media or existing inspection reports.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from scripts.benchmark_ppocrv6_clock import (run_experiment, normalize_expected,
                                             parse_roi, crop_roi, VALID_MODELS)

PRESETS = {
    "wide": (0.55, 0, 0.99, 0.095),
    "tight": (0.62, 0, 0.99, 0.060),
    "compact": (0.635, 0, 0.965, 0.052),
}


def summarize(entries: list[dict], expected=None):
    candidates: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        timestamp = entry.get("parsed_clock_unverified")
        if timestamp:
            candidates[timestamp].add(entry["roi_preset"])
    if not candidates:
        status = "unreadable"
    elif len(candidates) == 1:
        status = "single_candidate_unverified"
    else:
        status = "conflicting_candidates_require_review"
    answer = {
        "status": status,
        "candidates": [{"time_unverified": key, "roi_presets": sorted(value)}
                       for key, value in sorted(candidates.items())],
        "confirmed_capture_time": None,
    }
    if expected is not None:
        answer["manual_truth_match_count"] = sum(x.get("exact_match_to_manual_truth") is True
                                                   for x in entries)
    return answer


def run_matrix(path: Path, *, models=("PP-OCRv6_medium_rec",),
               presets=("wide", "tight", "compact"), custom_roi=None,
               expected=None, preview_dir=None, predictor_factory=None):
    if predictor_factory is None:
        from paddleocr import TextRecognition
        predictor_factory = lambda name: TextRecognition(model_name=name, device="cpu")
    model_cache = {}
    def get_model(name):
        if name not in model_cache:
            model_cache[name] = predictor_factory(name)
        return model_cache[name]
    bounds = {"custom": custom_roi} if custom_roi is not None else {name: PRESETS[name] for name in presets}
    rows = []
    rois = {}
    for name, roi in bounds.items():
        report = run_experiment(path, roi=roi, models=models, expected=expected,
                                predictor_factory=get_model)
        rois[name] = report["roi_pixels_xyxy"]
        if preview_dir is not None:
            import cv2
            import numpy as np
            preview_dir.mkdir(parents=True, exist_ok=True)
            img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
            patch, _ = crop_roi(img, roi)
            if not cv2.imwrite(str(preview_dir / f"{name}.png"), patch):
                raise OSError("Failed to save ROI preview")
        for row in report["results"]:
            rows.append({**row, "roi_preset": name})
    return {
        "experiment": "PP-OCRv6_multi_roi_clock_benchmark",
        "source_media_not_modified": True,
        "production_ocr_not_modified": True,
        "roi_pixels": rois,
        "results": rows,
        "summary": summarize(rows, expected),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--models", nargs="+", choices=VALID_MODELS,
                        default=["PP-OCRv6_medium_rec"])
    parser.add_argument("--presets", nargs="+", choices=list(PRESETS),
                        default=["wide", "tight", "compact"])
    parser.add_argument("--roi", type=parse_roi, default=None)
    parser.add_argument("--expected", type=normalize_expected)
    parser.add_argument("--preview-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = run_matrix(args.image, models=args.models, presets=args.presets,
                            custom_roi=args.roi, expected=args.expected,
                            preview_dir=args.preview_dir)
    except (FileNotFoundError, ValueError, ImportError, RuntimeError) as exc:
        parser.exit(2, f"Benchmark error: {exc}\n")
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
