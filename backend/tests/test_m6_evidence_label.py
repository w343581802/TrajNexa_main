"""M6-S4-4B 标注规范与评测指标。"""
from app.m6_evidence_label import (evidence_value, key_evidence_found,
                                   top_k_hit_rate, validate_labels)


def _row(fid, **kw):
    base = {"frame_id": fid, "video_relative_seconds": 1.0, "track_id": 41, "task": "face",
            "visibility": "visible", "sharpness": "sharp", "occlusion": "none",
            "truncated": False, "usable": "yes", "unusable_reason": None,
            "annotation_status": "verified", "face_visible": True, "face_usable": True}
    base.update(kw)
    return base


def test_valid_rows_pass():
    assert validate_labels([_row("1"), _row("2", usable="no",
                                           unusable_reason="过小")]) == []


def test_unusable_without_reason_is_rejected():
    errors = validate_labels([_row("1", usable="no")])
    assert any("不可用必须写原因" in e for e in errors)


def test_face_rows_need_visible_and_usable_separately():
    row = _row("1")
    row.pop("face_usable")
    errors = validate_labels([row])
    assert any("face_usable" in e for e in errors)


def test_task_mismatch_is_rejected():
    errors = validate_labels([_row("1")], task="plate")
    assert any("task 与评测目标不一致" in e for e in errors)


def test_duplicate_frame_is_rejected():
    assert any("重复" in e for e in validate_labels([_row("1"), _row("1")]))


def test_top_k_hit_rate_counts_usable_and_marginal():
    labels = [_row("1"), _row("2", usable="marginal"), _row("3", usable="no",
                                                           unusable_reason="糊")]
    stats = top_k_hit_rate(labels, ["1", "2", "3", "9"])
    assert stats["usable"] == 1
    assert stats["marginal"] == 1
    assert stats["unlabeled"] == 1
    assert stats["usable_rate"] == 0.25


def test_key_evidence_reports_any_and_all_hit():
    labels = [_row("1"), _row("2"), _row("3"), _row("4")]
    keys = ["1", "2", "3"]
    partial = key_evidence_found(labels, ["1", "3"], keys)
    assert partial["any_hit"] is True
    assert partial["all_hit"] is False
    assert partial["key_recall"] == 0.667
    assert partial["missed"] == ["2"]

    miss = key_evidence_found(labels, ["4"], keys)
    assert miss["any_hit"] is False and miss["all_hit"] is False and miss["key_recall"] == 0.0

    full = key_evidence_found(labels, ["1", "2", "3"], keys)
    assert full["all_hit"] is True and full["key_recall"] == 1.0


def test_association_and_quality_are_separate():
    assert evidence_value("confirmed", "usable")["verdict"] == "high_value_evidence_verified"
    assert evidence_value("candidate", "usable")["verdict"] == \
        "high_value_evidence_pending_review"
    assert evidence_value("candidate", "blurry")["verdict"] == "trace_ok_but_picture_unusable"
    assert evidence_value("ambiguous", "usable")["verdict"] == \
        "clear_picture_but_identity_unconfirmed"
    assert evidence_value("ambiguous", "blurry")["verdict"] == "not_evidence"
