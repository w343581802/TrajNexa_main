"""D2 去重与碎片归属的确定性测试（不依赖真实录像）。"""
from app.m6_fragments import (
    Detection,
    attribute_fragments,
    dedupe_same_frame,
    to_detections,
)


def _det(index, box, conf=0.6):
    return Detection(index=index, bbox=tuple(float(v) for v in box), confidence=conf)


def test_dedupe_removes_near_identical_boxes():
    kept, dropped = dedupe_same_frame([
        _det(0, [600, 300, 650, 400], 0.9),
        _det(1, [602, 302, 651, 401], 0.5),
    ])
    assert [d.index for d in kept] == [0]
    assert dropped and dropped[0]["duplicate_of"] == 0
    assert dropped[0]["index"] == 1


def test_dedupe_keeps_fragment_and_body_that_only_partially_overlap():
    # 头肩碎片与人体框 IoU≈0.3，本来就不该被 0.5 去重吃掉
    kept, dropped = dedupe_same_frame([
        _det(0, [611, 294, 649, 381], 0.73),
        _det(1, [609, 291, 638, 333], 0.33),
    ])
    assert len(kept) == 2 and not dropped


def test_fragment_inside_body_is_merged_with_provenance():
    body = _det(0, [600, 290, 650, 390])
    head = _det(1, [610, 291, 640, 335])
    kept, merged, ambiguous = attribute_fragments([body, head])
    assert [d.index for d in kept] == [0]
    assert kept[0].merged_from and kept[0].merged_from[0]["index"] == 1
    assert merged and merged[0]["into"] == 0
    assert not ambiguous


def test_two_people_side_by_side_are_not_merged():
    person_a = _det(0, [600, 290, 660, 400])
    person_b = _det(1, [665, 330, 700, 400])      # 小个子，只在边界附近接触
    kept, merged, _ = attribute_fragments([person_a, person_b])
    assert len(kept) == 2, "并排的两个人不能被当成碎片并掉"
    assert not merged


def test_low_height_detection_is_not_swallowed_by_tall_box():
    # 远处一个矮的人站在高个子后面：高度比超过上限，保留两个
    tall = _det(0, [600, 200, 660, 480], 0.8)
    short = _det(1, [605, 380, 650, 470], 0.6)
    kept, merged, _ = attribute_fragments([tall, short])
    assert len(kept) == 2 and not merged


def test_ambiguous_fragment_is_kept_when_two_bodies_claim_it():
    body_a = _det(0, [600, 280, 660, 400], 0.7)
    body_b = _det(1, [604, 285, 662, 402], 0.6)
    head = _det(2, [610, 290, 640, 330], 0.5)
    kept, merged, ambiguous = attribute_fragments([body_a, body_b, head])
    assert not merged, "两个候选都不能确认时不许合并"
    assert ambiguous and ambiguous[0]["index"] == 2
    assert any(d.index == 2 for d in kept)


def test_history_breaks_ambiguity():
    body_a = _det(0, [600, 280, 660, 400], 0.7)
    body_b = _det(1, [604, 285, 662, 402], 0.6)
    head = _det(2, [610, 290, 640, 330], 0.5)
    kept, merged, ambiguous = attribute_fragments(
        [body_a, body_b, head], previous_boxes=[(600, 280, 660, 400)])
    assert merged and merged[0]["into"] == 0
    assert not ambiguous


def test_to_detections_keeps_original_index():
    dets = to_detections([{"bbox": [1, 2, 3, 4], "confidence": 0.5},
                          {"bbox": [5, 6, 7, 8], "confidence": 0.4, "index": 17}])
    assert dets[0].index == 0 and dets[1].index == 17
