import json

import pytest

from app import m6_candidate_visual_review as review


def _payload(start='consistent', car='unknown', quality='sufficient'):
    dimensions = {}
    for key, _label in review.DIMENSIONS:
        status = start
        if key == 'vehicle_appearance':
            status = car
        dimensions[key] = {'status': status, 'evidence': '可见浅色上衣' if start == 'consistent' else '',
                           'region': 'A,B'}
    return json.dumps({'image_quality': quality, 'dimensions': dimensions,
                       'summary': '衣着一致，车辆看不清'})


def test_parse_normalizes_unknown_and_requires_evidence():
    parsed = review.parse_review(_payload())
    assert parsed['dimensions']['garment_color']['status'] == 'consistent'
    assert parsed['dimensions']['vehicle_appearance']['status'] == 'unknown'
    assert parsed['image_quality'] == 'sufficient'


def test_missing_dimension_is_unknown_not_conflict():
    parsed = review.parse_review('{"image_quality":"limited","dimensions":{}}')
    assert all(item['status'] == 'unknown' for item in parsed['dimensions'].values())
    assert review.summarize(parsed['dimensions'], parsed['image_quality']) == 'insufficient_evidence'


def test_conflict_dominates_overall():
    parsed = review.parse_review(_payload(start='consistent', car='conflict'))
    assert review.summarize(parsed['dimensions'], parsed['image_quality']) == 'conflicts_present'


def test_supports_requires_three_consistent_and_no_conflict():
    parsed = review.parse_review(_payload(start='consistent'))
    assert review.summarize(parsed['dimensions'], parsed['image_quality']) == 'supports'


def test_invalid_json_raises_stable_code():
    with pytest.raises(review.ReviewError) as exc:
        review.parse_review('not json')
    assert str(exc.value) == 'candidate_review_invalid_json'
