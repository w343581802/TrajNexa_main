import math

import pytest

from app import m6_camera_ray_projection as ray
from app import m6_ground_contact as contact

CAM = {"width": 1920, "height": 1080, "hfov_deg": 70.0, "pitch_deg": 25.0,
       "yaw_deg": 0.0, "height_m": 4.0, "lon": 110.2114, "lat": 25.2567}


def test_center_pixel_projects_expected_distance():
    result = ray.project(CAM, [960, 540])
    assert result["enu_offset_m"]["east"] == pytest.approx(0, abs=0.05)
    assert result["enu_offset_m"]["north"] == pytest.approx(4 / math.tan(math.radians(25)), abs=0.1)
    assert result["quality"] == "experimental_estimate"
    assert result["identity_status"] == "candidate_unverified"


def test_yaw_rotates_east_north():
    straight = ray.project(CAM, [960, 540])
    cam90 = dict(CAM, yaw_deg=90.0)
    turned = ray.project(cam90, [960, 540])
    assert turned["enu_offset_m"]["east"] == pytest.approx(straight["enu_offset_m"]["north"], abs=0.1)
    assert turned["enu_offset_m"]["north"] == pytest.approx(0, abs=0.1)


def test_missing_fov_degrades_instead_of_guessing():
    cam = dict(CAM)
    cam["hfov_deg"] = None
    with pytest.raises(ray.ProjectionUnavailable) as caught:
        ray.project(cam, [960, 540])
    assert str(caught.value) == "hfov_deg_missing"


def test_rejects_beyond_max_range():
    with pytest.raises(ray.ProjectionUnavailable) as caught:
        ray.project(CAM, [960, 0], max_range_m=50.0)
    assert str(caught.value) == "ground_point_too_far"


def test_rejects_pixel_outside_frame():
    with pytest.raises(ray.ProjectionUnavailable) as caught:
        ray.project(CAM, [960, -1])
    assert str(caught.value) == "pixel_outside_frame"


def test_uncertainty_is_reported_and_missing_params_return_none():
    radius = ray.uncertainty_radius(CAM, [960, 700])
    assert radius is not None and radius > 0
    cam = dict(CAM)
    cam["pitch_deg"] = None
    assert ray.uncertainty_radius(cam, [960, 700]) is None


def test_contact_from_box_marks_cut_off():
    result = contact.from_box([0, 400, 120, 1079], "person", frame_width=1920, frame_height=1080)
    assert result["quality"] == "cut_off"
    assert result["contact_pixel"] == [60.0, 1079.0]
