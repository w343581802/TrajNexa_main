"""B8-A workbench contract: historical M6 results remain candidates, never identity proof."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.m6_workbench_projection import TrackingProjectionError, project_tracking_result


ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "design" / "assets" / "m6_s6_18a_cyan_replay_b7b.json"


def _sample():
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def test_existing_three_camera_experiment_projection_preserves_weak_candidates():
    source = _sample()
    before = deepcopy(source)
    result = project_tracking_result(source, case_id=source["session"]["case_id"])

    assert result["session_state"] == "awaiting_review"
    assert result["review_required"] is True
    assert result["identity_established_by_projection"] is False
    assert len(result["candidate_pool"]) == 10
    assert len(result["windows"]) == 10
    assert all(c["state"] == "weak_candidate" for c in result["candidate_pool"])
    assert all(c["identity_status"] == "candidate_unverified"
               for c in result["candidate_pool"])
    assert source == before


def test_case_boundary_is_enforced():
    with pytest.raises(TrackingProjectionError, match="case_scope_mismatch"):
        project_tracking_result(_sample(), case_id="different-case")


def test_anchor_case_boundary_is_enforced():
    source = _sample()
    source["anchor"]["case_id"] = "different-case"
    with pytest.raises(TrackingProjectionError, match="anchor_case_mismatch"):
        project_tracking_result(source, case_id=source["session"]["case_id"])


def test_target_boundary_is_enforced():
    source = _sample()
    source["anchor"]["target_id"] = "someone-else"
    with pytest.raises(TrackingProjectionError, match="target_scope_mismatch"):
        project_tracking_result(source, case_id=source["session"]["case_id"])


def test_bad_score_fails_closed():
    source = _sample()
    source["candidate_pool"][0]["retrieval_score"] = float("nan")
    with pytest.raises(TrackingProjectionError, match="invalid_candidate_score"):
        project_tracking_result(source, case_id=source["session"]["case_id"])


def test_duplicate_candidate_id_fails_closed():
    source = _sample()
    source["candidate_pool"].append(deepcopy(source["candidate_pool"][0]))
    with pytest.raises(TrackingProjectionError, match="duplicate_candidate_id"):
        project_tracking_result(source, case_id=source["session"]["case_id"])


def test_default_qwen4b_and_grounding_opt_in_only():
    config = json.loads((ROOT / "backend" / "config" / "model_capabilities.json")
                        .read_text(encoding="utf-8"))
    assert config["default"]["attribute_extraction"] == "qwen3vl4b"
    assert config["tasks"]["m6.one_hop"]["attribute_extraction"] == "qwen3vl4b"
    assert config["strategies"]["attribute_extraction"] == "large_only"
    assert config["default"]["visual_grounding"] == "none"
    assert config["tasks"]["m6.one_hop"]["visual_grounding"] == "none"
    assert "wevisone_vl_2b" in config["models"]
