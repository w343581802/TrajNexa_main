"""M6-S4-3A 摄像头择优：按任务打分，而不是"第一个找到就停"。"""
from app.m6_camera_select import evaluate, expected_height_px, select_cameras

def _cam(cid, heading=0.0, dist=15.0, purpose="panorama", analyzed=False, focal=900):
    return {"id": cid, "heading_deg": heading, "near_m": 5.0, "far_m": 40.0,
            "focal_px": focal, "purpose": purpose, "already_analyzed": analyzed}


def test_expected_height_falls_with_distance():
    near = expected_height_px(_cam("a"), 8.0)
    far = expected_height_px(_cam("a"), 30.0)
    assert near > far
    assert abs(near - 900 * 1.7 / 8.0) < 0.5


def test_head_on_beats_side_view_at_same_distance():
    head_on = evaluate(_cam("head", heading=0.0), {"task": "face", "distance_m": 15.0,
                                                  "target_heading_deg": 0.0})
    side = evaluate(_cam("side", heading=90.0), {"task": "face", "distance_m": 15.0,
                                                "target_heading_deg": 0.0})
    assert head_on["score"] > side["score"]
    assert head_on["angle"] == 1.0


def test_face_camera_preferred_over_panorama():
    face = evaluate(_cam("face", purpose="face"), {"task": "face", "distance_m": 15.0})
    pano = evaluate(_cam("pano", purpose="panorama"), {"task": "face", "distance_m": 15.0})
    assert face["purpose_match"] and not pano["purpose_match"]
    assert face["score"] > pano["score"]


def test_too_small_camera_is_skipped_with_reason():
    tiny = _cam("tiny")
    result = select_cameras([tiny], {"task": "face", "distance_m": 38.0}, k=2)
    assert not result["selected"]
    assert "低于" in result["skipped"][0]["why"]
    assert result["skipped"][0]["expected_height_px"] < 80


def test_out_of_range_camera_is_skipped():
    result = select_cameras([_cam("far")], {"task": "track", "distance_m": 80.0}, k=2)
    assert not result["selected"]
    assert "不在覆盖范围" in result["skipped"][0]["why"]


def test_reanalyzed_camera_is_deprioritized():
    fresh = evaluate(_cam("fresh"), {"task": "track", "distance_m": 15.0})
    used = evaluate(_cam("used", analyzed=True), {"task": "track", "distance_m": 15.0})
    assert used["score"] < fresh["score"]
    assert any("已有同结论" in r for r in used["reasons"])


def test_selection_does_not_stop_at_first_hit():
    """三路都覆盖目标时，不能因为第一路找到就停：要按任务挑最有价值的 k 路。"""
    cameras = [_cam("close", heading=90.0), _cam("head", heading=0.0), _cam("far", dist=30.0)]
    result = select_cameras(cameras, {"task": "face", "distance_m": 15.0,
                                      "target_heading_deg": 0.0}, k=2)
    ids = [c["camera_id"] for c in result["selected"]]
    assert len(ids) == 2
    assert ids[0] == "head"
    assert result["skipped"] and "why" in result["skipped"][0]
