"""Experimental fixed-layout CCTV date glyph matching. NOT production OCR.

Trains on a DIFFERENT manually checked reference image from the same camera
layout; expected date of query is used for scoring only, never inference.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import cv2
import numpy as np

DATE_SLOTS_1920 = [1254, 1285, 1317, 1347, 1417, 1451, 1508, 1540]
DATE_INDICES = [0, 1, 2, 3, 5, 6, 8, 9]
CANVAS_WIDTH, CANVAS_HEIGHT = 1920, 1080
PATCH_HALF_WIDTH, PATCH_Y1, PATCH_Y2 = 14, 4, 44
REQUIRED_MATCH, REQUIRED_MARGIN = 0.79, 0.075
DATE_TEXT = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def load_bgr(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or min(image.shape[:2]) < 300:
        raise ValueError(f'Unable to decode a full CCTV image: {path}')
    return image


def validate_date(raw: str) -> str:
    if not DATE_TEXT.fullmatch(raw):
        raise ValueError('Require a manually verified YYYY-MM-DD date')
    return datetime.strptime(raw, '%Y-%m-%d').strftime('%Y-%m-%d')


def date_patches(image: np.ndarray, *, centers=DATE_SLOTS_1920) -> list[np.ndarray]:
    h, w = image.shape[:2]
    fx, fy = w / CANVAS_WIDTH, h / CANVAS_HEIGHT
    y1, y2 = round(PATCH_Y1 * fy), round(PATCH_Y2 * fy)
    patches = []
    for center in centers:
        x1 = round((center - PATCH_HALF_WIDTH) * fx)
        x2 = round((center + PATCH_HALF_WIDTH) * fx)
        if x1 < 0 or x2 > w or y1 < 0 or y2 > h:
            raise ValueError('OSD character slot outside image bounds')
        patch = image[y1:y2, x1:x2]
        if patch.size == 0 or patch.shape[1] < 10 or patch.shape[0] < 14:
            raise ValueError('OSD character slot too small')
        patches.append(patch.copy())
    return patches


def feature(patch: np.ndarray, mode: str) -> np.ndarray:
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (28, 40), interpolation=cv2.INTER_AREA)
    if mode == 'normalized':
        a = gray.astype(np.float32)
    elif mode == 'edges':
        a = cv2.Canny(gray, 45, 115).astype(np.float32)
    elif mode == 'bright':
        a = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)[1].astype(np.float32)
    else:
        raise ValueError(f'Unsupported feature mode: {mode}')
    return (a - a.mean()) / (a.std() + 1e-6)


def compare(template: np.ndarray, patch: np.ndarray) -> float:
    best = -1.0
    for dx in (-2, 0, 2):
        for dy in (-2, 0, 2):
            moved = np.roll(patch, (dy, dx), axis=(0, 1))
            if dy > 0:
                moved[:dy] = 0
            if dy < 0:
                moved[dy:] = 0
            if dx > 0:
                moved[:, :dx] = 0
            if dx < 0:
                moved[:, dx:] = 0
            denominator = float(np.linalg.norm(template) * np.linalg.norm(moved)) + 1e-6
            best = max(best, float(np.sum(template * moved)) / denominator)
    return best


def templates_from_reference(reference: np.ndarray, reference_date: str, mode: str):
    validate_date(reference_date)
    models = defaultdict(list)
    for index, patch in zip(DATE_INDICES, date_patches(reference)):
        models[reference_date[index]].append(feature(patch, mode))
    return models


def classify(patch, models, *, mode='normalized', threshold=REQUIRED_MATCH,
             min_margin=REQUIRED_MARGIN):
    probe = feature(patch, mode)
    scores = {label: max(compare(item, probe) for item in samples)
              for label, samples in models.items()}
    order = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if not order:
        raise ValueError('No templates available')
    top, score = order[0]
    runner_up = order[1][1] if len(order) > 1 else -1.0
    margin = float(score - runner_up)
    accepted = top if score >= threshold and margin >= min_margin else None
    return {
        'candidate': top, 'accepted': accepted,
        'similarity': round(float(score), 4),
        'runner_up_margin': round(margin, 4),
        'reason': 'accepted' if accepted is not None else 'weak_or_ambiguous',
        'top_scores': [{'digit': key, 'similarity': round(float(value), 4)}
                       for key, value in order[:3]]
    }


def benchmark(reference_path: Path, reference_date: str, query_path: Path, *,
              expected_date: str | None = None, mode='normalized',
              threshold=REQUIRED_MATCH, margin=REQUIRED_MARGIN,
              previews: Path | None = None):
    validate_date(reference_date)
    if expected_date is not None:
        validate_date(expected_date)
    if reference_path.resolve() == query_path.resolve():
        raise ValueError('Reference and query must be different image paths')
    reference_hash = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    query_hash = hashlib.sha256(query_path.read_bytes()).hexdigest()
    if reference_hash == query_hash:
        raise ValueError('Reference and query bytes are identical; this is data leakage')
    ref = load_bgr(reference_path)
    query = load_bgr(query_path)
    if abs(ref.shape[1]/ref.shape[0] - query.shape[1]/query.shape[0]) > 0.06:
        raise ValueError('Images have incompatible aspect ratios')
    templates = templates_from_reference(ref, reference_date, mode)
    segments = date_patches(query)
    predictions = [classify(p, templates, mode=mode, threshold=threshold,
                            min_margin=margin) for p in segments]
    for index, row in zip(DATE_INDICES, predictions):
        row['date_character_index'] = index
        row['field'] = ('year' if index <= 3 else 'month' if index <= 6 else 'day')
    digits = [row['accepted'] or '?' for row in predictions]
    assembled = ''.join(digits[:4]) + '-' + ''.join(digits[4:6]) + '-' + ''.join(digits[6:])
    valid = assembled if '?' not in assembled else None
    if valid:
        try:
            validate_date(valid)
        except ValueError:
            valid = None
    if previews:
        previews.mkdir(parents=True, exist_ok=True)
        for i, (idx, patch) in enumerate(zip(DATE_INDICES, segments)):
            cv2.imwrite(str(previews / f'query_{i}_{idx}.png'), patch)
    return {
        'module': 'fixed_layout_osd_digit_reference_match_experiment',
        'production_pipeline_modified': False,
        'reference_sha256': reference_hash, 'query_sha256': query_hash,
        'reference_date_training_label': reference_date,
        'reference_layout': 'manual_calibrated_1920x1080_top_clock_v0',
        'feature_mode': mode, 'template_classes': sorted(templates.keys()),
        'threshold': threshold, 'minimum_margin': margin,
        'per_digit': predictions, 'raw_digit_result': assembled,
        'date_candidate_unverified': valid,
        'status': 'complete_candidate_unverified' if valid else 'indeterminate',
        'test_only_manual_truth': expected_date,
        'exact_match_to_manual_truth': (valid == expected_date) if expected_date is not None else None,
        'fully_verified_capture_time': None
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference_image', type=Path)
    parser.add_argument('query_image', type=Path)
    parser.add_argument('--reference-date', required=True)
    parser.add_argument('--expected')
    parser.add_argument('--mode', choices=('normalized','edges','bright'), default='normalized')
    parser.add_argument('--threshold', type=float, default=REQUIRED_MATCH)
    parser.add_argument('--margin', type=float, default=REQUIRED_MARGIN)
    parser.add_argument('--preview-dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = benchmark(args.reference_image, args.reference_date, args.query_image,
                       expected_date=args.expected, mode=args.mode,
                       threshold=args.threshold, margin=args.margin,
                       previews=args.preview_dir)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)

if __name__ == '__main__':
    main()
