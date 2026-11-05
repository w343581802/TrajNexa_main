import pytest

from app import m6_ground_projection as gp


def _cal(**overrides):
    calibration = {
        "calibration_id": "cal-1", "camera_uuid": "cam-1",
        "calibration_revision": "r1", "review_status": "approved",
        "reference_width": 200.0, "reference_height": 100.0,
        "world_crs": "local_enu", "homography_3x3": [[0.1, 0, 0], [0, 0.1, 0], [0, 0, 1]],
        "ground_valid_polygon": [[0, 0], [200, 0], [200, 100], [0, 100]],
        "holdout_residual_m": {"median": 0.4, "p90": 0.9, "max": 1.5},
    }
    calibration.update(overrides)
    return calibration


def test_projects_inside_polygon_with_measured_error():
    result = gp.project(_cal(), [100, 50])
    assert result["spatial_level"] == "L2"
    assert result["ground_point"]["x"] == 10.0
    assert result["ground_point"]["y"] == 5.0
    assert result["uncertainty_radius_m"] == 0.9


def test_unapproved_calibration_is_rejected():
    with pytest.raises(gp.GroundCalibrationError) as exc:
        gp.project(_cal(review_status="model_proposed"), [100, 50])
    assert str(exc.value) == "calibration_not_approved"


def test_outside_valid_polygon_is_rejected():
    polygon = [[100, 0], [200, 0], [200, 100], [100, 100]]
    with pytest.raises(gp.GroundCalibrationError) as exc:
        gp.project(_cal(ground_valid_polygon=polygon), [10, 10])
    assert str(exc.value) == "ground_point_outside_valid_polygon"


def test_non_uniform_frame_geometry_is_rejected():
    with pytest.raises(gp.GroundCalibrationError) as exc:
        gp.project(_cal(), [100, 50], frame_size=(400, 100))
    assert str(exc.value) == "frame_geometry_mismatch"


def test_uniform_frame_rescale_maps_back_to_reference():
    result = gp.project(_cal(), [200, 100], frame_size=(400, 200))
    assert result["ground_point"]["x"] == 10.0
    assert "frame_rescaled_to_reference" in result["uncertainties"]


def test_missing_residual_never_claims_metric_uncertainty():
    result = gp.project(_cal(holdout_residual_m={}), [100, 50])
    assert result["uncertainty_radius_m"] is None
    assert "residual_unquantified" in result["uncertainties"]
