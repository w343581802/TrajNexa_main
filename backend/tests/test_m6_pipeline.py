"""M6-S5 离线研判流程：编排与证据汇总。"""
import numpy as np

from app.m6_pipeline import attach_quality, run_pipeline


def _track(track_id, start):
    return {"track_id": track_id, "observations": [
        {"frame_id": str(start + i), "timestamp_seconds": (start + i) * 0.2,
         "bbox": [100, 100, 160, 260], "confidence": 0.8} for i in range(5)]}


def _vecs(start, vector):
    return {str(start + i): np.asarray(vector, dtype=np.float32) for i in range(5)}


def _data():
    tracks = [_track(1, 0), _track(2, 100)]
    vectors = {1: _vecs(0, [1.0, 0.0]), 2: _vecs(100, [0.8, 0.6])}
    return tracks, vectors


def test_pipeline_runs_end_to_end():
    tracks, vectors = _data()
    report = run_pipeline(tracks=tracks, frame_vectors=vectors, reference_track=1)
    assert report["ok"] is True
    stages = [s["stage"] for s in report["stages"]]
    assert stages == ["reference_archive", "grouping", "reid_match", "trackback"]
    assert report["archive"]["frame_count"] == 5
    assert len(report["candidates"]) == 2
    assert report["candidates"][0]["status"] == "reference"
    assert report["candidates"][0]["score"] == 1.0
    assert abs(report["candidates"][1]["score"] - 0.80) < 0.01


def test_pipeline_reports_association_not_identity():
    tracks, vectors = _data()
    report = run_pipeline(tracks=tracks, frame_vectors=vectors, reference_track=1)
    assert report["association_status"] in ("candidate", "ambiguous")


def test_quality_attaches_to_association_separately():
    tracks, vectors = _data()
    report = run_pipeline(tracks=tracks, frame_vectors=vectors, reference_track=1)
    assert "evidence" not in report          # 没有画质信息时不预先给结论
    attach_quality(report, keyframes=[], quality_status="usable")
    assert report["evidence"]["verdict"] == "trace_ok_but_picture_unusable"
    attach_quality(report, keyframes=[{"frame_id": "3"}], quality_status="usable")
    assert report["evidence"]["verdict"] == "high_value_evidence_pending_review"


def test_missing_reference_vectors_fail_explicitly():
    tracks, vectors = _data()
    report = run_pipeline(tracks=tracks, frame_vectors={2: vectors[2]}, reference_track=1)
    assert report["ok"] is False
    assert report["reason"] == "reference_has_no_frames"
