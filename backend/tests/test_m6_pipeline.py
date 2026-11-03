"""M6-S5 离线研判流程：锚点、重复复核、时间戳与证据状态。"""
import numpy as np

from app.m6_pipeline import attach_quality, build_manifest, run_pipeline


def _track(track_id, frames, bbox=(100, 100, 160, 260), sec=None):
    return {"track_id": track_id, "observations": [
        {"frame_id": str(f), "timestamp_seconds": float(sec(f) if sec else f * 0.2),
         "bbox": list(bbox), "confidence": 0.8} for f in frames]}


def _vec(frames, vector):
    return {str(f): np.asarray(vector, dtype=np.float32) for f in frames}


def test_anchor_is_not_ranked_and_duplicate_is_separated():
    tracks = [_track(1, range(0, 5)), _track(2, range(100, 105)), _track(3, range(200, 205))]
    vectors = {1: _vec(range(0, 5), [1.0, 0.0]),
               2: _vec(range(100, 105), [0.98, 0.02]),
               3: _vec(range(200, 205), [0.10, 0.99])}
    report = run_pipeline(tracks=tracks, frame_vectors=vectors, reference_track=1)
    assert report["ok"] is True
    assert report["anchor"]["group"] == [1]
    names = [d["candidate"] for d in report["suspected_duplicates_of_reference"]]
    assert names == ["2"]
    independent = [c["candidate"] for c in report["independent_candidates"]]
    assert "1" not in independent and "2" not in independent and "3" in independent
    assert report["suspected_duplicates_of_reference"][0]["score"] > 0.95
    assert report["suspected_duplicates_of_reference"][0]["review_reason"]


def test_reference_itself_never_counts_as_association_success():
    tracks = [_track(1, range(0, 5)), _track(3, range(200, 205))]
    vectors = {1: _vec(range(0, 5), [1.0, 0.0]), 3: _vec(range(200, 205), [0.10, 0.99])}
    report = run_pipeline(tracks=tracks, frame_vectors=vectors, reference_track=1)
    assert report["association_status"] == "unconfirmed"


def test_timestamps_come_from_observations_not_frame_ids():
    """两帧编号差很远但时间相同 → 必须被时间间隔过滤掉；用帧号反推会算成"隔了很久"。"""
    tracks = [_track(1, [100], sec=lambda f: 1.0),
              _track(3, [500], sec=lambda f: 1.0)]
    vectors = {1: {"100": np.asarray([1.0, 0.0], dtype=np.float32)},
               3: {"500": np.asarray([1.0, 0.0], dtype=np.float32)}}
    report = run_pipeline(tracks=tracks, frame_vectors=vectors, reference_track=1,
                          params={"match_mode": "per_frame"})
    assert report["ok"] is True
    assert report["independent_candidates"][0]["score"] == 0.0


def test_keyframes_alone_do_not_mean_usable_picture():
    tracks = [_track(1, range(0, 5))]
    vectors = {1: _vec(range(0, 5), [1.0, 0.0])}
    report = run_pipeline(tracks=tracks, frame_vectors=vectors, reference_track=1)
    attach_quality(report, [{"frame_id": "1"}])
    assert report["quality_status"] == "unverified"
    assert report["evidence"]["verdict"] == "not_evidence"
    attach_quality(report, [{"frame_id": "1"}], quality_status="usable")
    assert report["evidence"]["verdict"] == "clear_picture_but_identity_unconfirmed"


def test_manifest_records_every_factor():
    manifest = build_manifest(sampling_strategy="time_stratified", max_samples=16,
                              min_bbox_height=40.0, quality_gate={"confidence": 0.25},
                              model_version="sha256:abc", source_video="cam29",
                              pipeline_version="abc1234")
    assert manifest["max_samples"] == 16
    assert manifest["model_version"] == "sha256:abc"
    assert "不证明归组结果正确" in manifest["note"]
