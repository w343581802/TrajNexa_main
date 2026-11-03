from app.m6_truth_registry import (add_observation, chains_by_target, confirm,
                                   observation, validate_package)


def _reg():
    return {"case_id": "purple", "observations": []}


def test_chains_are_separated_by_target():
    reg = _reg()
    purple = observation("purple", "purple", "u-2-2", "57", "111", 54006.2,
                         recording_id="rec-2-2", evidence=["frame:a.jpg"])
    cyan = observation("purple", "cyan", "u-1-1", "37", "225", 54063.0,
                       recording_id="rec-1-1", evidence=["frame:b.jpg"])
    add_observation(reg, purple)
    add_observation(reg, cyan)
    confirm(reg, purple["observation_id"], "case-owner", ["frame:a.jpg"])
    confirm(reg, cyan["observation_id"], "case-owner", ["frame:b.jpg"])
    chains = chains_by_target(reg)
    assert len(chains) == 2
    assert chains[("purple", "purple")][0]["tracklet_id"] == "111"
    assert chains[("purple", "cyan")][0]["tracklet_id"] == "225"


def test_cross_target_dependency_is_flagged():
    reg = _reg()
    purple = observation("purple", "purple", "u-2-2", "57", "111", 54006.2,
                         recording_id="rec-2-2", evidence=["frame:a.jpg"])
    add_observation(reg, purple)
    confirm(reg, purple["observation_id"], "case-owner", ["frame:a.jpg"])
    cyan = observation("purple", "cyan", "u-1-1", "37", "225", 54063.0,
                       recording_id="rec-1-1", evidence=["frame:b.jpg"])
    cyan["depends_on"] = purple["observation_id"]
    add_observation(reg, cyan)
    codes = [p["code"] for p in validate_package(reg)]
    assert "cross_target_dependency" in codes


def test_missing_evidence_file_is_flagged(tmp_path):
    reg = _reg()
    record = observation("purple", "purple", "u-2-1", "58", "37", 54012.6,
                         recording_id="rec-2-1", evidence=["frame:not-there.jpg"])
    add_observation(reg, record)
    confirm(reg, record["observation_id"], "case-owner", ["frame:not-there.jpg"])
    codes = [p["code"] for p in validate_package(reg, evidence_root=str(tmp_path))]
    assert "evidence_unresolvable" in codes
