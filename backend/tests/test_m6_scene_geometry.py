import json

import pytest

from app import m6_scene_geometry as sg


def test_parse_normalized_region_and_unknown_direction():
    text = json.dumps({"road_region": [0.1, 0.5, 0.9, 1.0], "road_direction": "diagonal",
                       "structures": ["curb", "parking line"], "occlusion_regions": [[0, 0, 0.2, 0.2]]})
    scene = sg.parse_scene_json(text, width=1000, height=500)
    assert scene["road_region"] == [100.0, 250.0, 900.0, 500.0]
    assert scene["road_direction"] == "unknown"
    assert scene["usable"] is True
    assert len(scene["structures"]) == 2


def test_parse_rejects_non_json():
    with pytest.raises(sg.SceneGeometryError):
        sg.parse_scene_json("not json", width=100, height=100)


def test_restrict_keeps_only_segments_inside_road_and_outside_occlusion():
    scene = {"usable": True, "road_region": [100, 200, 900, 500],
             "occlusion_regions": [[0, 0, 300, 300]], "road_direction": "center"}
    segments = [[150, 250, 170, 290], [850, 250, 880, 300], [50, 100, 80, 140]]
    result = sg.restrict_segments(segments, scene)
    assert result["mode"] == "qwen_region"
    assert result["segments"] == [[850, 250, 880, 300]]


def test_restrict_falls_back_when_scene_unusable():
    segments = [[0, 0, 10, 10]]
    result = sg.restrict_segments(segments, {"usable": False})
    assert result["mode"] == "baseline_all"
    assert result["segments"] == segments


def test_compare_reports_flags_divergent_vps():
    base = {"frame": {"width": 1920}, "status": "estimated",
            "candidates": [{"vanishing_point_px": [100, 100]}]}
    other = {"frame": {"width": 1920}, "status": "estimated",
             "candidates": [{"vanishing_point_px": [600, 100]}]}
    result = sg.compare_reports(base, other)
    assert result["agreement"] == "divergent"
    assert result["gap_px"] == 500.0
