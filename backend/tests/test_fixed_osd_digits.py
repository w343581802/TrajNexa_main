"""Synthetic fixed-layout glyph tests. Never include surveillance screenshots in Git."""
from pathlib import Path
import cv2
import numpy as np
import pytest

from scripts.benchmark_fixed_osd_digits import (
    DATE_SLOTS_1920, validate_date, date_patches,
    templates_from_reference, classify, benchmark,
)


def frame(seed=0):
    image = np.full((1080, 1920, 3), 120, np.uint8)
    for char, x in zip("20261023", DATE_SLOTS_1920):
        block = np.random.default_rng(ord(char)).integers(0, 2, (20, 14), dtype=np.uint8)
        glyph = cv2.resize(block * 255, (28, 40), interpolation=cv2.INTER_NEAREST)
        image[4:44, x-14:x+14] = cv2.cvtColor(glyph, cv2.COLOR_GRAY2BGR)
    image[80:90, 50:60] = np.random.default_rng(seed).integers(
        0, 256, (10, 10, 3), dtype=np.uint8)
    return image


def test_valid_dates_and_rejection():
    assert validate_date("2026-10-23") == "2026-10-23"
    with pytest.raises(ValueError): validate_date("2026-02-30")
    with pytest.raises(ValueError): validate_date("2026-10-3")


def test_eight_positionally_segmented_digits():
    assert len(date_patches(frame())) == 8
    assert all(p.shape == (40, 28, 3) for p in date_patches(frame()))


def test_digits_reconstruct_from_independent_synthetic_reference():
    templates = templates_from_reference(frame(1), "2026-10-23", "normalized")
    prediction = [classify(p, templates) for p in date_patches(frame(2))]
    assert ''.join(v['accepted'] or '?' for v in prediction) == "20261023"


def test_obscured_digit_is_rejected():
    templates = templates_from_reference(frame(), "2026-10-23", "normalized")
    blank = np.full_like(date_patches(frame())[6], 125)
    assert classify(blank, templates)['accepted'] is None


def test_match_score_does_not_claim_clock_truth():
    models = templates_from_reference(frame(), "2026-10-23", "normalized")
    output = classify(date_patches(frame())[0], models)
    assert output['accepted'] == '2'
    assert 'confirmed' not in output


def test_identical_bytes_training_leakage_blocked(tmp_path):
    a, b = tmp_path/'reference.png', tmp_path/'query.png'
    cv2.imwrite(str(a), frame())
    cv2.imwrite(str(b), frame())
    with pytest.raises(ValueError, match="identical"):
        benchmark(a, "2026-10-23", b, expected_date="2026-10-23")


def test_expected_is_scoring_only(tmp_path):
    a, b = tmp_path/'reference.png', tmp_path/'query.png'
    cv2.imwrite(str(a), frame(1))
    cv2.imwrite(str(b), frame(2))
    good = benchmark(a, "2026-10-23", b, expected_date="2026-10-23")
    wrong = benchmark(a, "2026-10-23", b, expected_date="2026-10-24")
    assert good['status'] == wrong['status']
    assert good['date_candidate_unverified'] == wrong['date_candidate_unverified']
    assert good['exact_match_to_manual_truth'] is True
    assert wrong['exact_match_to_manual_truth'] is False
