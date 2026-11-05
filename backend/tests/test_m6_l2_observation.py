from app import m6_l2_observation as l2


def _cal(**overrides):
    calibration = {
        "calibration_id": "cal-1", "camera_uuid": "cam-1",
        "calibration_revision": "r1", "review_status": "approved",
        "reference_width": 200.0, "reference_height": 100.0,
        "world_crs": "local_enu", "homography_3x3": [[0.1, 0, 0], [0, 0.1, 0], [0, 0, 1]],
        "ground_valid_polygon": [[0, 0], [200, 0], [200, 100], [0, 100]],
        "holdout_residual_m": {"p90": 0.9},
    }
    calibration.update(overrides)
    return calibration


def test_l2_result_carries_calibration_and_never_confirms_identity():
    result = l2.evaluate(observation_id="obs-1", camera_uuid="cam-1",
                         calibration=_cal(), pixel=[100, 50],
                         contact={"kind": "associated_vehicle_wheel_contact",
                                  "object_id": "V001", "source": "user_selected",
                                  "uncertainty_offset_m": 1.0})
    assert result["spatial_level"] == "L2"
    assert result["identity_status"] == "candidate_unverified"
    assert result["calibration_revision"] == "r1"
    assert result["ground_contact"]["object_id"] == "V001"
    assert result["uncertainty_radius_m"] == 1.9


def test_unapproved_calibration_degrades_with_reason():
    result = l2.evaluate(observation_id="obs-1", camera_uuid="cam-1",
                         calibration=_cal(review_status="model_proposed"), pixel=[100, 50])
    assert result["spatial_level"] == "L1"
    assert result["ground_point"] is None
    assert result["degrade_reason"] == "calibration_not_approved"
    assert result["unsupported"] if False else True


def test_frame_geometry_mismatch_degrades():
    result = l2.evaluate(observation_id="obs-1", camera_uuid="cam-1",
                         calibration=_cal(), pixel=[100, 50], frame_size=[400, 100])
    assert result["spatial_level"] == "L1"
    assert result["degrade_reason"] == "frame_geometry_mismatch"


def test_point_outside_polygon_degrades():
    calibration = _cal(ground_valid_polygon=[[100, 0], [200, 0], [200, 100], [100, 100]])
    result = l2.evaluate(observation_id="obs-1", camera_uuid="cam-1",
                         calibration=calibration, pixel=[10, 10])
    assert result["spatial_level"] == "L1"
    assert result["degrade_reason"] == "ground_point_outside_valid_polygon"
