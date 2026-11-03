import pytest

from app.m6_truth_registry import (TruthPackageError, add_observation, confirm,
                                   confirmed_chain, export_package, is_exploratory,
                                   observation, revoke, validate_package)


def _reg():
    return {"case_id": "purple-rider", "observations": []}


def _obs(tracklet="111", camera="u-2-2", map_id="9000000000000057", anchor=54006.2,
         status="candidate", reviewer=None, evidence=None, binding="pending_human_review"):
    return observation("purple-rider", "purple", camera, map_id, tracklet, anchor,
                       recording_id="rec-2-2", status=status, reviewer=reviewer,
                       evidence=evidence if evidence is not None else ["frame:2-2/0046.jpg"],
                       binding_status=binding)


def test_candidate_needs_recording_and_evidence():
    reg = _reg()
    bad = _obs()
    bad["evidence"] = []
    with pytest.raises(TruthPackageError):
        add_observation(reg, bad)
    ok = add_observation(reg, _obs())
    assert ok["status"] == "candidate"


def test_confirmation_requires_reviewer_and_evidence():
    reg = _reg()
    record = add_observation(reg, _obs())
    with pytest.raises(TruthPackageError):
        confirm(reg, record["observation_id"], None, ["frame:x"])
    with pytest.raises(TruthPackageError):
        confirm(reg, record["observation_id"], "case-owner", [])
    confirmed = confirm(reg, record["observation_id"], "case-owner", ["frame:2-2/0046.jpg"])
    assert confirmed["status"] == "human_confirmed"


def test_synthetic_observation_cannot_be_confirmed():
    reg = _reg()
    record = _obs()
    record["synthetic"] = True
    add_observation(reg, record)
    with pytest.raises(TruthPackageError):
        confirm(reg, record["observation_id"], "case-owner", ["frame:x"])


def test_duplicate_observation_rejected():
    reg = _reg()
    add_observation(reg, _obs())
    with pytest.raises(TruthPackageError):
        add_observation(reg, _obs())


def test_chain_is_time_ordered_and_valid():
    reg = _reg()
    a = add_observation(reg, _obs(tracklet="111", anchor=54006.2))
    b = add_observation(reg, _obs(tracklet="37", camera="u-2-1",
                                  map_id="9000000000000058", anchor=54012.6))
    confirm(reg, a["observation_id"], "case-owner", ["frame:a"])
    confirm(reg, b["observation_id"], "case-owner", ["frame:b"])
    chain = confirmed_chain(reg)
    assert [c["tracklet_id"] for c in chain] == ["111", "37"]
    assert validate_package(reg) == []


def test_third_hop_without_independent_truth_is_flagged():
    reg = _reg()
    for tracklet, camera, anchor in (("111", "u-2-2", 54006.2), ("37", "u-2-1", 54012.6),
                                     ("225", "u-1-1", 54003.8)):
        record = add_observation(reg, _obs(tracklet=tracklet, camera=camera, anchor=anchor))
        confirm(reg, record["observation_id"], "case-owner", ["frame:%s" % tracklet])
    # 人为抹掉第三条证据，模拟"用推导代替真值"
    for record in reg["observations"]:
        if record["tracklet_id"] == "225":
            record["evidence"] = []
    codes = [p["code"] for p in validate_package(reg)]
    assert "third_hop_without_independent_truth" in codes


def test_revoke_cascades_to_dependents():
    reg = _reg()
    a = add_observation(reg, _obs())
    b = _obs(tracklet="225", camera="u-1-1", map_id="9000000000000037", anchor=54063.0)
    b["depends_on"] = a["observation_id"]
    add_observation(reg, b)
    revoke(reg, a["observation_id"], "wrong_person")
    assert all(r["status"] == "revoked" for r in reg["observations"])


def test_pending_binding_marks_package_exploratory():
    reg = _reg()
    add_observation(reg, _obs(binding="pending_human_review"))
    assert is_exploratory(reg) is True
    package = export_package(reg)
    assert package["exploratory"] is True
    assert package["problems"] == []
