"""M6-S4-1A 归组证据：包含度、冲突检测、外观一致性。"""
import numpy as np

from app.m6_track_grouping import group_tracks, pair_evidence


def _track(track_id, boxes, conf=0.8, start=0):
    return {"track_id": track_id, "observations": [
        {"frame_id": str(start + i), "timestamp_seconds": float(start + i) * 0.2,
         "bbox": list(box), "confidence": conf}
        for i, box in enumerate(boxes)
    ]}


def _vecs(track_id, vector, frames):
    return (track_id, {str(f): np.asarray(vector, dtype=np.float32) for f in frames})


def _frame_vectors(*items):
    return dict(items)


def test_identical_boxes_are_same_object():
    a = _track(1, [[100, 100, 160, 260]] * 5)
    b = _track(2, [[100, 100, 160, 260]] * 5)
    vecs = _frame_vectors(_vecs(1, [1.0, 0.0], range(5)), _vecs(2, [1.0, 0.0], range(5)))
    ev = pair_evidence(a, b, vecs)
    assert ev["verdict"] == "same_object"
    assert ev["median_containment"] == 1.0


def test_nested_box_merges_despite_size_ratio():
    outer = _track(1, [[100, 100, 160, 260]] * 5)
    inner = _track(2, [[115, 110, 145, 170]] * 5)  # 头部/上身框，高度只有 60
    vecs = _frame_vectors(_vecs(1, [1.0, 0.0], range(5)), _vecs(2, [0.99, 0.01], range(5)))
    ev = pair_evidence(outer, inner, vecs)
    assert ev["max_size_ratio"] > 2.2
    assert ev["median_containment"] >= 0.7
    assert ev["verdict"] == "same_object"


def test_same_frame_far_apart_is_conflict():
    a = _track(1, [[0, 100, 60, 260]] * 5)
    b = _track(2, [[600, 100, 660, 260]] * 5)
    vecs = _frame_vectors(_vecs(1, [1.0, 0.0], range(5)), _vecs(2, [1.0, 0.0], range(5)))
    ev = pair_evidence(a, b, vecs)
    assert ev["verdict"] == "conflict_coexist"


def test_different_appearance_needs_review():
    a = _track(1, [[100, 100, 160, 260]] * 5)
    b = _track(2, [[102, 102, 158, 258]] * 5)
    vecs = _frame_vectors(_vecs(1, [1.0, 0.0], range(5)), _vecs(2, [0.0, 1.0], range(5)))
    ev = pair_evidence(a, b, vecs)
    assert ev["verdict"] == "needs_review"


def test_no_overlap_is_not_grouped():
    a = _track(1, [[100, 100, 160, 260]] * 5, start=0)
    b = _track(2, [[100, 100, 160, 260]] * 5, start=50)
    ev = pair_evidence(a, b, None)
    assert ev["verdict"] == "not_overlapping"


def test_group_tracks_keeps_original_ids():
    a = _track(1, [[100, 100, 160, 260]] * 5)
    b = _track(2, [[100, 100, 160, 260]] * 5)
    c = _track(3, [[600, 100, 660, 260]] * 5)
    vecs = _frame_vectors(
        _vecs(1, [1.0, 0.0], range(5)),
        _vecs(2, [1.0, 0.0], range(5)),
        _vecs(3, [1.0, 0.0], range(5)),
    )
    groups, _ = group_tracks([a, b, c], vecs)
    tags = sorted(tuple(g["track_ids"]) for g in groups)
    assert tags == [(1, 2), (3,)]
    assert groups[0]["candidate_id"].startswith("PhysicalObjectCandidate_")


def test_coverage_gaps_expose_false_extension():
    from app.m6_track_grouping import coverage_gaps

    a = _track(1, [[100, 100, 160, 260]] * 5, start=0)
    b = _track(2, [[100, 100, 160, 260]] * 5, start=9)  # 中间空了 0.8 秒
    report = coverage_gaps([a, b], max_gap_seconds=0.45)
    assert report["gaps"], "归组后出现的空档必须被记录"
    assert report["max_gap_seconds"] > 0.45


def _frames(track_id, entries, vec=(1.0, 0.0)):
    """entries: [(frame_index, x, y)] -> (track, frame_vectors 片段)"""
    track = {"track_id": track_id, "observations": [
        {"frame_id": str(i), "timestamp_seconds": i * 0.2,
         "bbox": [x, y, x + 60, y + 160], "confidence": 0.8}
        for i, x, y in entries]}
    vectors = {track_id: {str(i): np.asarray(vec, dtype=np.float32) for i, _, _ in entries}}
    return track, vectors


def test_transitive_merge_is_rejected_by_group_conflict():
    """A~B 与 B~C 成立，但 A 与 C 同帧相距很远（不可能是同一人）→ 拒绝自动归组。"""
    from app.m6_track_grouping import group_tracks

    a, va = _frames(1, [(i, 0, 100) for i in range(10)])
    b, vb = _frames(2, [(0, 0, 100), (1, 0, 100), (2, 0, 100)]
                    + [(i, 600, 100) for i in range(10, 14)])
    c, vc = _frames(3, [(i, 600, 100) for i in range(6, 14)])
    groups, evidence = group_tracks([a, b, c], {**va, **vb, **vc})

    verdicts = {(e["track_a"], e["track_b"]): e["verdict"] for e in evidence}
    assert verdicts[(1, 2)] == "same_object"
    assert verdicts[(2, 3)] == "same_object"
    assert verdicts[(1, 3)] == "conflict_coexist"

    # 三条各自成组，且都带上冲突标记，不能输出 A-B-C 一个大组
    assert sorted(tuple(g["track_ids"]) for g in groups) == [(1,), (2,), (3,)]
    assert all(g["group_conflict"] for g in groups)


def test_low_confidence_same_object_is_not_auto_merged():
    """证据只到"像"（余弦 0.80）时不允许自动并档，宁可留给人工核验。"""
    from app.m6_track_grouping import group_tracks

    a = _track(1, [[100, 100, 160, 260]] * 5)
    b = _track(2, [[100, 100, 160, 260]] * 5)
    vecs = {**_vecs(1, [1.0, 0.0], range(5))[1], **_vecs(2, [0.80, 0.60], range(5))[1]}
    groups, evidence = group_tracks([a, b], vecs)
    assert evidence[0]["verdict"] == "same_object"
    assert sorted(tuple(g["track_ids"]) for g in groups) == [(1,), (2,)]
