from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app import m6_l2_trajectory as traj

T0 = datetime(2026, 11, 5, 7, 0, tzinfo=timezone.utc)


def _row(lon, lat, minute, level="L2", crs="EPSG:4326"):
    return SimpleNamespace(spatial_level=level, ground_point={"x": lon, "y": lat, "crs": crs},
                           observation_id=f"obs-{minute}", uncertainty_radius_m=1.2,
                           created_at=T0 + timedelta(minutes=minute))


def test_single_point_has_no_direction():
    result = traj.build_segments([_row(110.0, 25.0, 0)])
    segment = result["segments"][0]
    assert segment["quality"] == "single_point"
    assert segment["direction_deg"] is None
    assert "single_point_no_direction" in segment["uncertainties"]
    assert segment["identity_status"] == "candidate_unverified"


def test_gap_splits_segments_and_length_is_meters():
    rows = [_row(110.0, 25.0, 0), _row(110.0001, 25.0, 1), _row(110.0002, 25.0, 40)]
    result = traj.build_segments(rows, gap_seconds=900)
    assert [s["point_count"] for s in result["segments"]] == [2, 1]
    first = result["segments"][0]
    assert first["length_m"] > 5
    assert 80 < first["direction_deg"] < 100  # 向东

def test_non_l2_or_non_wgs84_rows_are_skipped():
    result = traj.build_segments([_row(110, 25, 0), _row(110, 25, 1, level="L1"),
                                  _row(110, 25, 2, crs="local_enu")])
    assert result["skipped_rows"] == 2
    assert len(result["segments"][0]["points"]) == 1


def test_spatial_priority_is_soft_and_bounded():
    near = traj.spatial_priority((110.0, 25.0), 110.0, 25.0)
    far = traj.spatial_priority((110.0, 25.0), 110.01, 25.0)
    assert near == 1.0
    assert 0.0 <= far < near
    assert traj.spatial_priority(None, 0, 0) == 0.0
