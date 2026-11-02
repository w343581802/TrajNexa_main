"""P1-B-1C-1 轨迹质量约束的单元测试。"""
import pytest

from app.m6_track_quality import (DEFAULT_PARAMS, assess_tracks, audit_track, quality_of,
                                  refine_tracks, split_track)


def _obs(index, bbox, step=0.2, start=100.0):
    return {"frame_id": "f%03d" % index, "timestamp_seconds": round(start + index * step, 3),
            "bbox": list(bbox), "confidence": 0.8}


def _track(track_id, boxes, step=0.2):
    return {"track_id": track_id, "frames": [_obs(i, b, step) for i, b in enumerate(boxes)]}


def test_clean_track_has_no_violation():
    """正常行驶的车辆：位置连续、尺寸稳定 → 不切段。"""
    boxes = [(600 + i * 4, 300, 680 + i * 4, 380) for i in range(12)]
    result = refine_tracks([_track(1, boxes)])
    assert len(result["tracks"]) == 1
    assert result["splits"] == []
    assert audit_track(result["tracks"][0]["frames"])["violations"] == 0


def test_area_jump_splits_track():
    """框面积突然放大 4 倍：必须断开，不能带着错误框继续跑。"""
    boxes = [(600, 300, 680, 380)] * 6 + [(560, 260, 800, 460)] * 6
    segments, breaks = split_track([_obs(i, b) for i, b in enumerate(boxes)])
    assert len(segments) == 2
    assert "area_jump" in breaks[0]["reasons"]


def test_center_jump_splits_track():
    """框尺寸没变但位置突然跳到另一辆车上：也要断开。"""
    boxes = [(600, 300, 680, 380)] * 6 + [(900, 300, 980, 380)] * 6
    segments, breaks = split_track([_obs(i, b) for i, b in enumerate(boxes)])
    assert len(segments) == 2
    assert "center_jump" in breaks[0]["reasons"]


def test_aspect_out_of_range_is_flagged():
    """扁长框（把一排车框在一起）宽高比异常 → 断开。"""
    boxes = [(600, 300, 680, 380)] * 6 + [(500, 300, 900, 360)] * 6
    segments, breaks = split_track([_obs(i, b) for i, b in enumerate(boxes)])
    assert len(segments) == 2
    assert "aspect_out_of_range" in breaks[0]["reasons"]


def test_short_fragment_is_marked_not_deleted():
    """切出来的碎片要保留并标记，不能靠删轨迹来让指标好看。"""
    boxes = [(600, 300, 680, 380)] * 10 + [(560, 260, 800, 460)] * 2
    result = refine_tracks([_track(7, boxes)])
    assert len(result["tracks"]) == 2
    fragment = [t for t in result["tracks"] if t["fragment"]]
    assert len(fragment) == 1
    assert fragment[0]["parent_track_id"] == 7
    assert result["fragments"] == 1


def test_audit_reports_ratios():
    boxes = [(600, 300, 680, 380)] * 6 + [(560, 260, 800, 460)] * 6
    metrics = audit_track([_obs(i, b) for i, b in enumerate(boxes)])
    assert metrics["area_max_ratio"] > 2.0
    assert metrics["violations"] >= 1


def test_track_without_frames_is_ignored():
    result = refine_tracks([{"track_id": 3, "frames": []}])
    assert result["tracks"] == []


def test_persistent_wide_box_splits_once():
    """连续出现同一个扁长框：只切一次，不能切出一堆单帧碎片。"""
    boxes = [(600, 300, 680, 380)] * 6 + [(500, 300, 900, 360)] * 8
    segments, breaks = split_track([_obs(i, b) for i, b in enumerate(boxes)])
    assert len(segments) == 2
    assert len(breaks) == 1




def test_quality_labels_are_marking_only():
    """质量标签只标注，不改轨迹、不删轨迹。"""
    clean = _track(1, [(600 + i * 4, 300, 680 + i * 4, 380) for i in range(12)])
    jitter = _track(2, [(600, 300, 680, 380)] * 6 + [(560, 260, 800, 460)] * 6)
    enriched, counts = assess_tracks([clean, jitter])
    assert counts["reliable"] == 1
    assert counts["suspicious"] == 1
    assert len(enriched) == len([clean, jitter])
    assert enriched[0]["quality_status"] == "reliable"
    assert "area_jump" in enriched[1]["quality_reasons"]
    assert enriched[1]["evidence_frames"] == 12



def test_short_track_is_unknown_quality():
    short = _track(5, [(600 + i * 2, 300, 640 + i * 2, 340) for i in range(2)])
    result = quality_of(short["frames"])
    assert result["quality_status"] == "unknown"
    assert "too_few_frames" in result["quality_reasons"]
