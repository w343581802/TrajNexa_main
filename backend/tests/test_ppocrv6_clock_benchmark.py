"""PP-OCRv6 ROI/adapter tests with fake predictor, NOT real model accuracy."""
from pathlib import Path
import cv2
import numpy as np
import pytest

from scripts.benchmark_ppocrv6_clock import (parse_roi, crop_roi, make_variants,
    extract_result, normalize_expected, evaluate, run_experiment)


def test_roi_contains_upper_right_clock_without_upscaling_source():
    sample = np.zeros((1088, 1920, 3), dtype=np.uint8)
    roi, coords = crop_roi(sample, parse_roi('0.55,0,0.99,0.095'))
    assert coords == (1056, 0, 1901, 104)
    assert roi.shape == (104, 845, 3)
    assert set(make_variants(roi)) == {'original', 'upscaled_2x', 'clahe_2x', 'light_text_2x'}


def test_invalid_roi_rejected():
    for value in ['0.5,0,0.4,0.2', '-1,0,1,1', 'bad,roi', '0.1,0.2']:
        with pytest.raises(Exception):
            parse_roi(value)


def test_paddleocr_text_recognition_json_adapter():
    class Result:
        @property
        def json(self):
            return {'res': {'rec_text': '2026-10-23 14:59:59', 'rec_score': 0.996}}
    assert extract_result([Result()]) == ('2026-10-23 14:59:59', 0.996)
    assert extract_result([{'res': {'rec_text': '2026-10-3/14:59:59', 'rec_score': .9999}}])[0] == '2026-10-3/14:59:59'
    assert extract_result([]) == (None, None)


def test_expected_is_scoring_only_not_repair():
    expected = normalize_expected('2026-10-23 14:59:59')
    entry = evaluate('2026-10-3/14:59:59', expected)
    assert entry['parsed_clock_unverified'] == '2026-10-03T14:59:59'
    assert entry['exact_match_to_manual_truth'] is False
    assert entry['requires_review'] is True


def test_benchmark_with_fake_model_does_not_need_paddleocr(tmp_path: Path):
    frame = np.ones((1088, 1920, 3), dtype=np.uint8) * 175
    image_path = tmp_path / 'private-input.png'
    assert cv2.imwrite(str(image_path), frame)
    initialized = []
    class Model:
        def predict(self, input, batch_size):
            assert input.ndim == 3 and batch_size == 1
            return [{'res': {'rec_text': '2026-10-23 14:59:59', 'rec_score': .88}}]
    def factory(name):
        initialized.append(name)
        return Model()
    r = run_experiment(image_path, models=('PP-OCRv6_medium_rec','PP-OCRv6_small_rec'),
                       expected='2026-10-23T14:59:59', predictor_factory=factory)
    assert initialized == ['PP-OCRv6_medium_rec', 'PP-OCRv6_small_rec']
    assert len(r['results']) == 8
    assert all(x['exact_match_to_manual_truth'] for x in r['results'])
    assert r['raw_source_preserved'] and not r['baseline_pipeline_modified']
