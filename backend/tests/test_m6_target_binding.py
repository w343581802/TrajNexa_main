import pytest

from app import m6_target_binding as tb

CAM = {"width": 1920, "height": 1080, "hfov_deg": 60.0, "pitch_deg": 25.0,
       "yaw_deg": 123.0, "height_m": 4.0, "lon": 110.204471, "lat": 25.257477}


def _frames(t0, count, box=None, step=0.5):
    box = box or [800.0, 500.0, 900.0, 700.0]
    out = []
    for i in range(count):
        shifted = [box[0] + i * 2, box[1], box[2] + i * 2, box[3]]
        out.append({"t": t0 + i * step, "frame": 100 + i, "box": shifted})
    return out


def test_bind_picks_overlapping_tracklet():
    anchor = {"t": 10.0, "box": [800, 500, 900, 700]}
    tracklets = [{"track_id": "a", "frames": _frames(9.0, 6)},
                 {"track_id": "b", "frames": _frames(9.0, 6, box=[100, 100, 200, 200])}]
    result = tb.bind_tracklets(anchor, tracklets)
    assert result["bound"] == "a"
    assert result["identity_status"] == "candidate_unverified"
    assert result["bind_evidence"]["iou"] > 0.3


def test_ambiguous_binding_is_not_resolved_by_guess():
    anchor = {"t": 10.0, "box": [800, 500, 900, 700]}
    tracklets = [{"track_id": "a", "frames": _frames(10.0, 4)},
                 {"track_id": "b", "frames": _frames(10.0, 4, box=[802, 502, 902, 702])}]
    result = tb.bind_tracklets(anchor, tracklets)
    assert result["identity_status"] == "ambiguous"
    assert result["bind_evidence"]["ambiguous"] is True
    assert result["bind_evidence"]["runner_up"] == "b"


def test_no_match_returns_no_track():
    anchor = {"t": 10.0, "box": [10, 10, 60, 60]}
    result = tb.bind_tracklets(anchor, [{"track_id": "a", "frames": _frames(10.0, 3)}])
    assert result["bound"] is None
    assert result["reason"] == "no_tracklet_matches_anchor"


def test_gap_breaks_segment_without_filling():
    frames = _frames(0.0, 3) + _frames(10.0, 3)
    segments = tb.segment_frames(frames)
    assert len(segments) == 2
    assert segments[0][1] == "gap_exceeded"
    assert segments[1][1] == "end_of_track"
    assert len(segments[1][0]) == 3


def test_position_jump_breaks_segment():
    frames = _frames(0.0, 3) + [_frames(1.5, 1, box=[1600, 200, 1700, 400])[0] for _ in range(1)]
    frames.append({"t": 2.0, "frame": 999, "box": [1600.0, 200.0, 1700.0, 400.0]})
    segments = tb.segment_frames(frames)
    assert len(segments) >= 2
    assert segments[1][1] in ("position_jump", "gap_exceeded", "end_of_track")


def test_build_track_projects_and_keeps_identity_separate():
    anchor = {"t": 10.0, "box": [800, 500, 900, 700]}
    tracklets = [{"track_id": "a", "frames": _frames(9.0, 8)}]
    report = tb.build_track(anchor, tracklets, CAM, evidence={"clip": "c.mp4"})
    assert report["persisted"] is False
    assert report["identity_status"] == "candidate_unverified"
    assert report["summary"]["projected"] == 8
    first = report["segments"][0]["points"][0]
    assert first["evidence"]["track_id"] == "a"
    assert first["evidence"]["clip"] == "c.mp4"
    assert first["quality"] == "experimental_estimate"
