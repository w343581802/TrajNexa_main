import math

import pytest

from app import m6_cv_self_calibration as cv


def _lines_through(vp, angles, length=400.0):
    lines = []
    for ang in angles:
        dx, dy = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        x1, y1 = vp[0] + dx * length, vp[1] + dy * length
        x2, y2 = vp[0] + dx * length * 2, vp[1] + dy * length * 2
        lines.append(cv.to_line([x1, y1, x2, y2]))
    return lines


def test_recovers_known_offscreen_vanishing_point():
    vp = (960.0, -176.0)
    lines = _lines_through(vp, [70, 80, 90, 100, 110, 75, 105, 95])
    result = cv.estimate_vanishing_point(lines, width=1920, height=1080)
    assert result["vp"][0] == pytest.approx(vp[0], abs=25)
    assert result["vp"][1] == pytest.approx(vp[1], abs=25)
    assert len(result["inliers"]) >= 6


def test_parallel_lines_are_rejected_not_forced():
    lines = [cv.to_line([x, 400, x, 900]) for x in range(200, 1800, 100)]
    with pytest.raises(cv.CvUnavailable):
        cv.estimate_vanishing_point(lines, width=1920, height=1080)


def test_regression_f130_is_rejected():
    # 首轮失败案例：VP y=479.4、俯角 25° 反推出 f=130 px，必须被合理性闸门拒绝
    with pytest.raises(cv.CvUnavailable) as caught:
        cv.plausible_focal(cv.implied_focal(479.4, 540.0, 25.0))
    assert str(caught.value) == "focal_out_of_range"


def test_pitch_from_vp_roundtrip():
    focal = 1536.0
    vp_y = 540.0 - focal * math.tan(math.radians(25.0))
    assert cv.pitch_from_vp(vp_y, 540.0, focal) == pytest.approx(25.0, abs=0.05)


def test_track_lines_use_ground_contacts():
    tracks = [[[900 + i, 800 - 30 * i] for i in range(6)],
              [[1000 - i, 780 - 28 * i] for i in range(6)]]
    lines = cv.tracks_to_lines(tracks)
    assert len(lines) == 2
    for line in lines:
        assert abs(line[0] * line[3] + line[1] * line[4] + line[2]) < 1e-6


def test_analyze_degrades_on_featureless_image():
    import numpy as np
    gray = np.full((540, 960), 128, dtype=np.uint8)
    report = cv.analyze(gray, width=960, height=540, pitch_deg=25.0)
    assert report["status"] == "failed"
    assert report["degrade_reason"] == "insufficient_lines"


def test_validate_flags_vanishing_point_off_expected_horizon():
    candidate = {"vanishing_point_px": [172.0, 5.5], "pitch_from_vp_deg": 19.18,
                 "implied_focal_px": 1146.2}
    result = cv.validate_against_pose(candidate, reported_pitch_deg=25.0,
                                      reported_focal_px=1536.3, cy=540.0, frame_height=1080)
    assert result["verdict"] == "conflict"
    assert "vanishing_point_not_on_horizon" in result["reasons"]
    assert result["expected_horizon_y"] == pytest.approx(-176.4, abs=0.5)


def test_validate_consistent_when_candidate_matches_horizon():
    focal, cy, pitch = 1536.0, 540.0, 25.0
    vp_y = cy - focal * math.tan(math.radians(pitch))
    candidate = {"vanishing_point_px": [960.0, vp_y],
                 "pitch_from_vp_deg": cv.pitch_from_vp(vp_y, cy, focal),
                 "implied_focal_px": focal}
    result = cv.validate_against_pose(candidate, reported_pitch_deg=pitch,
                                      reported_focal_px=focal, cy=cy, frame_height=1080)
    assert result["verdict"] == "consistent"
