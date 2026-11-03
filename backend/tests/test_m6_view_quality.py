import numpy as np
import pytest

from app.m6_observation_group import group_observers
from app.m6_view_quality import (DEFAULT_PARAMS, candidate_quality,
                                 confirmed_target_quality, evaluate_target,
                                 rank_cameras, sharpness, tracklet_quality)


def _sharp_image(size=200):
    image = np.zeros((size, size, 3), dtype="uint8")
    image[::2, :, :] = 255
    return image


def _flat_image(size=200):
    return np.full((size, size, 3), 128, dtype="uint8")


def _samples(image, height):
    return [{"image": image, "bbox": [50, 50, 50 + height, 50 + height]}]


def test_sharp_large_target_scores_high_and_blurry_small_scores_low():
    good = evaluate_target(_samples(_sharp_image(), 90))
    poor = evaluate_target(_samples(_flat_image(), 20))
    assert good["band"] == "high"
    assert poor["band"] == "low"
    assert good["basis"] == "measured"


def test_module_can_be_switched_off():
    result = evaluate_target(_samples(_sharp_image(), 90), {"enabled": False})
    assert result == {"enabled": False, "band": "unknown", "score": None,
                      "basis": "disabled"}
    members = [{"camera": "58", "coverage": {"region_in_fov": 0.9}, "static_prior": None}]
    ranked = rank_cameras(members, {"58": {"band": "low", "basis": "measured"}},
                          {"enabled": False})
    assert ranked[0]["quality"]["basis"] == "disabled"


def test_dynamic_measurement_beats_static_prior():
    members = [{"camera": "58", "coverage": {"region_in_fov": 0.9}, "static_prior": 0.9},
               {"camera": "37", "coverage": {"region_in_fov": 0.9}, "static_prior": 0.2}]
    ranked = rank_cameras(members, {"37": {"band": "high", "basis": "measured"},
                                    "58": {"band": "low", "basis": "measured"}},
                          {"mode": "prioritize"})
    assert ranked[0]["camera"] == "37"


def test_observe_only_reports_without_reordering():
    members = [{"camera": "58", "coverage": {"region_in_fov": 0.9}, "static_prior": None},
               {"camera": "37", "coverage": {"region_in_fov": 0.9}, "static_prior": None}]
    ranked = rank_cameras(members, {"37": {"band": "high", "basis": "measured"}},
                          {"mode": "observe_only"})
    assert [m["camera"] for m in ranked] == ["58", "37"]
    assert ranked[1]["quality"]["band"] == "high"


def _square(lon, lat, half):
    return {"type": "Polygon", "coordinates": [[
        [lon - half, lat - half], [lon + half, lat - half],
        [lon + half, lat + half], [lon - half, lat + half], [lon - half, lat - half]]]}


REGION = _square(110.2100, 25.2570, 0.0002)


def _cam(cid, lon, lat, half=0.0006):
    return {"map_camera_id": cid, "camera_uuid": "u-%s" % cid,
            "fov": {"observation": _square(lon, lat, half)}}


def test_verify_all_keeps_every_covering_camera_even_over_limit():
    cameras = [_cam("58", 110.2100, 25.2570), _cam("37", 110.2101, 25.2570),
               _cam("39", 110.2099, 25.2570)]
    group = group_observers({"geometry": REGION, "source": "possible_region"}, cameras,
                            max_observers=1, quality_by_camera={},
                            quality_params={"mode": "verify_all"})
    assert len(group["members"]) == 3
    assert all(m["selected"] for m in group["members"])


def test_blurry_camera_is_deprioritized_but_never_dropped():
    cameras = [_cam("58", 110.2100, 25.2570), _cam("37", 110.2101, 25.2570)]
    quality = {"58": {"band": "low", "basis": "measured"},
               "37": {"band": "high", "basis": "measured"}}
    group = group_observers({"geometry": REGION, "source": "possible_region"}, cameras,
                            max_observers=3, quality_by_camera=quality,
                            quality_params={"mode": "prioritize"})
    assert [m["camera"] for m in group["members"]] == ["37", "58"]
    assert len(group["members"]) == 2          # 低分镜头仍在列表里，只是排后


def test_no_sample_falls_back_to_unverified_prior():
    members = [{"camera": "58", "coverage": {"region_in_fov": 0.9}, "static_prior": 0.7}]
    ranked = rank_cameras(members, {}, {"mode": "prioritize"})
    assert ranked[0]["quality"]["basis"] == "static_prior_unverified"


def test_sharpness_detects_flat_versus_textured():
    assert sharpness(_flat_image()) < 1.0
    assert sharpness(_sharp_image()) > 100.0


def test_tracklet_quality_never_counts_as_target_evidence():
    result = tracklet_quality(_samples(_sharp_image(), 90))
    assert result["attribution"] == "tracklet"
    assert result["usable_as_target_evidence"] is False


def test_candidate_quality_keeps_identity_uncertainty():
    result = candidate_quality(_samples(_sharp_image(), 90), "2-2/2-2@54008.6:403")
    assert result["attribution"] == "candidate"
    assert result["identity_confirmed"] is False
    assert result["usable_as_target_evidence"] is False
    assert result["uncertainty"] == "candidate_identity_unconfirmed"


def test_wrong_target_high_quality_cannot_become_confirmed_evidence():
    other = candidate_quality(_samples(_sharp_image(), 120), "2-1/x:999")
    refused = confirmed_target_quality(_samples(_sharp_image(), 120), "B1", [])
    assert other["band"] == "high"
    assert refused["refused"] is True
    assert refused["usable_as_target_evidence"] is False
    assert refused["reason"] == "missing_human_review_evidence"


def test_confirmed_target_quality_requires_and_records_evidence():
    result = confirmed_target_quality(_samples(_sharp_image(), 90), "B1",
                                      ["review-event:evt-1"])
    assert result["attribution"] == "confirmed_target"
    assert result["usable_as_target_evidence"] is True
    assert result["evidence_refs"] == ["review-event:evt-1"]


def test_tiny_but_sharp_target_is_low_not_medium():
    result = evaluate_target(_samples(_sharp_image(), 18))
    assert result["band"] == "low"
    assert result["too_small_for_identity"] is True
