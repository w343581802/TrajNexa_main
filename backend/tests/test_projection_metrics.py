"""/cases/{case}/tracking-runs/{run}/track-segments 报告口径的单测（B8-S §6.2）。"""
from __future__ import annotations

import math

import pytest

from app import m6_projection_metrics as metrics

LON = 110.21
LAT = 25.25


def offset(lat: float, metres: float) -> float:
    return lat + metres / 111_320.0


def point(*, level="L2-candidate", t="2026-10-23T15:00:05", metres=None, radius=5.0, reason=None, range_m=12.0):
    row = {"t": t, "frame": 10, "spatial_level": level, "quality": "experimental_estimate", "range_m": range_m}
    if metres is not None:
        row["ground_point"] = {"x": LON, "y": offset(LAT, metres), "crs": "EPSG:4326", "lon": LON, "lat": offset(LAT, metres)}
        row["uncertainty_radius_m"] = radius
    if reason is not None:
        row["degrade_reason"] = reason
    return row


def envelope(points, *, tracks=1, samples=None):
    segment = {"track_id": "t1", "break_reason": "end_of_track", "start_seconds": 0.0, "end_seconds": 6.0, "points": list(points)}
    track = {"track_id": "t1", "camera_label": "1-LT-测试镜头", "segments": [segment], "camera_ready": True}
    return {
        "case_id": "case-1",
        "run_id": "run-1",
        "layer": "experimental_estimate",
        "persisted": False,
        "spatial_revision": "mapdb-spatial:abc",
        "spatial_error": None,
        "tracks": [track],
        "summary": {"tracks": tracks, "samples": samples if samples is not None else len(points), "with_coordinates": 1},
    }


def truth_set(*rows, camera_uuid="cam-1"):
    return metrics.load_truth({"dataset_id": "pilot-truth", "camera_uuid": camera_uuid, "points": list(rows)})


def truth_row(point_id, metres, *, at="2026-10-23T15:00:05", source="surveyed"):
    return {"id": point_id, "lon": LON, "lat": offset(LAT, metres), "at": at, "source": source}


def test_camera_only_points_are_not_experimental():
    report = metrics.build_report(envelope([point(level="L0", metres=None), point(level="L0", metres=None)]))
    assert report["overall"]["points"] == {"total": 2, "experimental": 0, "formal": 0, "camera_only": 2, "degraded": 0}
    assert report["overall"]["rates"]["experimental"] == 0.0
    assert report["truth"] is None
    assert report["thresholds"] is None


def test_degraded_points_keep_reason_histogram():
    report = metrics.build_report(
        envelope(
            [
                point(metres=None, reason="ground_point_too_far", range_m=70.0),
                point(metres=None, reason="ground_point_too_far", range_m=75.0),
                point(metres=None, reason="ray_does_not_hit_ground", range_m=30.0),
            ]
        )
    )
    assert report["overall"]["points"]["degraded"] == 3
    assert report["overall"]["degrade_reasons"] == {"ground_point_too_far": 2, "ray_does_not_hit_ground": 1}
    assert report["overall"]["buckets"]["far"]["degraded"] == 2


def test_formal_level_is_reported_as_mis_publish():
    report = metrics.build_report(envelope([point(level="L2", metres=4.0), point(metres=6.0)]))
    assert report["gate"]["mis_published"] == 1
    assert report["gate"]["mis_publish_rate"] == 0.5


def test_points_without_truth_only_report_coverage():
    report = metrics.build_report(envelope([point(metres=4.0), point(metres=None, reason="ground_point_too_far")]))
    assert report["overall"]["points"]["experimental"] == 1
    assert report["overall"]["rates"]["degraded"] == 0.5
    assert report["overall"]["uncertainty_radius_m"]["median"] == 5.0
    assert report["truth"] is None


def test_truth_matching_respects_time_tolerance():
    truth = truth_set(truth_row("T1", 3.0), truth_row("T2", 30.0, at="2026-10-23T16:00:00"))
    report = metrics.build_report(envelope([point(metres=3.4)]), truth=truth)
    assert report["truth"]["matched"] == 1
    assert report["truth"]["unmatched"] == 1
    assert report["truth"]["unmatched_ids"] == ["T2"]
    assert report["truth"]["error_m"]["median"] == pytest.approx(0.4, abs=0.2)


def test_declared_region_coverage_uses_projection_radius():
    truth = truth_set(truth_row("T1", 3.0), truth_row("T2", 12.0, at="2026-10-23T15:00:20"))
    rows = [
        point(metres=3.5, radius=5.0, t="2026-10-23T15:00:05"),
        point(metres=30.0, radius=5.0, t="2026-10-23T15:00:20"),
    ]
    report = metrics.build_report(envelope(rows), truth=truth)
    assert report["truth"]["declared_region"]["checked"] == 2
    assert report["truth"]["declared_region"]["inside"] == 1
    assert report["truth"]["declared_region"]["rate"] == 0.5
    assert report["truth"]["error_m"]["max"] == pytest.approx(18.0, abs=0.3)
    assert report["truth"]["error_m"]["median"] == pytest.approx(9.25, abs=0.3)


def test_numeric_clip_offset_is_not_treated_as_absolute_clock():
    """真实 run 的 `t` 是片段内秒数，没有绝对时刻时只能空间配，并标 time_checked=false。"""
    truth = truth_set(truth_row("T1", 3.0))
    rows = [{**point(metres=3.4), "t": 0.2}]
    report = metrics.build_report(envelope(rows), truth=truth)
    assert report["truth"]["matched"] == 1
    assert report["truth"]["time_checked"] == 0
    assert report["truth"]["unmatched"] == 0


def test_truth_without_time_match_is_reported_as_such():
    truth = truth_set(truth_row("T1", 3.0, at="2026-10-23T15:00:05"))
    rows = [{**point(metres=3.4), "t": "2026-10-23T16:30:00"}]
    report = metrics.build_report(envelope(rows), truth=truth)
    assert report["truth"]["matched"] == 0
    assert report["truth"]["unmatched_detail"][0]["reason"] == "no_time_match"
    assert report["truth"]["unmatched_detail"][0]["nearest_m"] == pytest.approx(0.4, abs=0.2)


def test_truth_errors_are_grouped_by_range_class():
    truth = truth_set(truth_row("T1", 2.0))
    report = metrics.build_report(envelope([point(metres=2.2, range_m=10.0)]), truth=truth)
    assert list(report["truth"]["by_range_class"]) == ["near"]
    assert report["truth"]["by_range_class"]["near"]["count"] == 1


def test_algorithm_output_cannot_be_used_as_truth():
    with pytest.raises(metrics.TruthSetError) as excinfo:
        truth_set(truth_row("T1", 2.0, source="ray_projection"))
    assert str(excinfo.value) == "truth_source_is_algorithm_output:T1"


def test_truth_rejects_unknown_source_and_bad_coordinates():
    with pytest.raises(metrics.TruthSetError) as excinfo:
        truth_set(truth_row("T1", 2.0, source="guess"))
    assert str(excinfo.value) == "truth_source_not_allowed:T1"

    with pytest.raises(metrics.TruthSetError) as excinfo:
        metrics.load_truth({"points": [{"id": "T1", "lon": "abc", "lat": 25.25, "source": "surveyed"}]})
    assert str(excinfo.value) == "truth_point_missing_coordinates:T1"

    with pytest.raises(metrics.TruthSetError) as excinfo:
        metrics.load_truth({"points": []})
    assert str(excinfo.value) == "truth_points_missing"


def test_truth_rejects_duplicate_ids():
    with pytest.raises(metrics.TruthSetError) as excinfo:
        truth_set(truth_row("T1", 2.0), truth_row("T1", 4.0))
    assert str(excinfo.value) == "truth_point_duplicate_id:T1"


def test_percentile_is_none_for_empty_and_ignores_non_numeric():
    assert metrics.percentile([], 0.9) is None
    assert metrics.percentile([None, "x"], 0.9) is None
    assert metrics.percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert metrics.percentile([1, 2, 3, 4], 0.9) == pytest.approx(3.7)


def test_haversine_matches_known_offset():
    metres = metrics.haversine_m((LON, LAT), (LON, offset(LAT, 100.0)))
    assert metres == pytest.approx(100.0, rel=0.01)
    assert math.isclose(metrics.haversine_m((LON, LAT), (LON, LAT)), 0.0, abs_tol=1e-9)