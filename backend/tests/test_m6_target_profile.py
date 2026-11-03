import pytest

from app.m6_target_profile import (attach_candidate, attribute, compare_attributes,
                                   confirm_association, confirmed_attributes,
                                   judge_attribute, new_profile, revoke_association,
                                   validate_attributes)


def _v1():
    return new_profile(
        "purple_rider_001", "2-2", 111, 54006.2, 54008.6,
        attributes=[
            attribute("upper_clothing_color", "紫色", state="human_verified",
                      visibility="clear", evidence_frame=52, model="qwen3-vl:8b-instruct"),
            attribute("vehicle_color", "黑色", state="human_verified",
                      visibility="clear", evidence_frame=52),
        ])


def _candidate_attrs():
    return [
        attribute("upper_clothing_color", "紫色", visibility="clear", evidence_frame=80),
        attribute("vehicle_color", "红色", visibility="partial", evidence_frame=80,
                  evidence_region=[0, 0, 60, 120]),
        attribute("lower_clothing", "深色裤子", visibility="partial", evidence_frame=80),
        attribute("helmet", "unknown", visibility="unknown", evidence_frame=80),
    ]


def test_schema_rejects_merged_attribute_names():
    merged = attribute("vehicle_color|vehicle_type", "黑色电动车")
    try:
        validate_attributes([merged])
    except ValueError as exc:
        assert "merged_or_illegal_name" in str(exc)
    else:
        raise AssertionError("合并属性应被拒绝")


def test_schema_rejects_absent_claim_without_clear_visibility():
    hidden = attribute("helmet", "无头盔", observation_status="not_observed", visibility="partial")
    try:
        validate_attributes([hidden])
    except ValueError as exc:
        assert "not_observed_needs_clear_visibility" in str(exc)
    else:
        raise AssertionError("看不清却写没有，应被拒绝")


def test_schema_requires_unknown_value_for_unknown_status():
    item = attribute("carried_items", "雨伞", observation_status="unknown", visibility="unknown")
    try:
        validate_attributes([item])
    except ValueError as exc:
        assert "unknown_must_not_carry_value" in str(exc)
    else:
        raise AssertionError("unknown 不应携带具体值")


def test_schema_accepts_well_formed_attributes():
    ok = [attribute("vehicle_color", "黑色", state="human_verified", visibility="clear",
                    subject_ref="target_vehicle_01", evidence_region=[0.3, 0.4, 0.7, 0.9]),
          attribute("helmet", "unknown", visibility="unknown", observation_status="unknown")]
    assert validate_attributes(ok) is True


def test_v1_keeps_only_human_verified_attributes():
    assert {a["name"] for a in confirmed_attributes(_v1())} == {"upper_clothing_color", "vehicle_color"}


def test_candidate_does_not_pollute_confirmed_profile():
    profile = attach_candidate(_v1(), "2-1", 37, 54012.6, 54015.6, _candidate_attrs())
    assert profile["version"] == "V1"
    assert len(profile["observations"]) == 1
    assert {a["name"] for a in confirmed_attributes(profile)} == {"upper_clothing_color", "vehicle_color"}
    assert all(a["state"] == "model_proposed" for a in profile["candidates"][0]["attributes"]
               if a["name"] != "helmet")


def test_comparison_reports_support_conflict_unknown_and_new():
    findings = compare_attributes(_v1()["attributes"], _candidate_attrs())
    verdicts = {f["name"]: f["verdict"] for f in findings}
    assert verdicts["upper_clothing_color"] == "support"
    assert verdicts["vehicle_color"] == "conflict"
    assert verdicts["lower_clothing"] == "new_in_candidate"
    assert verdicts["helmet"] == "unknown_in_candidate"


def test_attribute_region_is_kept_for_conflict_evidence():
    findings = compare_attributes(_v1()["attributes"], _candidate_attrs())
    conflict = next(f for f in findings if f["verdict"] == "conflict")
    assert conflict["b_region"] == [0, 0, 60, 120]


def test_rejected_attribute_stops_influencing_comparison_but_is_kept():
    profile = attach_candidate(_v1(), "2-1", 37, 54012.6, 54015.6, _candidate_attrs())
    cand = judge_attribute(profile["candidates"][0], "vehicle_color", "human_rejected",
                           "红色来自背景隔离墩")
    rejected = [a for a in cand["attributes"] if a["name"] == "vehicle_color"][0]
    assert rejected["state"] == "human_rejected" and "隔离墩" in rejected["note"]
    findings = compare_attributes(profile["attributes"], cand["attributes"])
    conflict = [f for f in findings if f["name"] == "vehicle_color"][0]
    assert conflict["verdict"] == "rejected_by_human"   # 不再产生冲突，但记录被否决
    assert conflict["b"] == "红色"                       # 原始模型输出仍然保留


def test_unknown_attribute_is_never_promoted_to_feature():
    profile = attach_candidate(_v1(), "2-1", 37, 54012.6, 54015.6, _candidate_attrs())
    cand = judge_attribute(profile["candidates"][0], "lower_clothing", "human_verified")
    updated = confirm_association({**profile, "candidates": [cand]}, "2-1#37")
    names = {a["name"] for a in updated["attributes"]}
    assert "lower_clothing" in names        # 人工核实的可采纳
    assert "helmet" not in names            # unknown 不补全


def test_confirm_association_keeps_v1_attributes_and_adds_verified_only():
    profile = attach_candidate(_v1(), "2-1", 37, 54012.6, 54015.6, _candidate_attrs())
    cand = judge_attribute(profile["candidates"][0], "upper_clothing_color", "human_verified")
    updated = confirm_association({**profile, "candidates": [cand]}, "2-1#37")
    assert updated["version"] == "V2"
    assert updated["observations"][-1]["camera"] == "2-1"
    by_name = {a["name"]: a for a in updated["attributes"]}
    assert by_name["vehicle_color"]["value"] == "黑色"      # V1 原始特征未被覆盖
    assert by_name["vehicle_color"]["state"] == "human_verified"


def test_one_wrong_attribute_does_not_reject_the_association():
    profile = attach_candidate(_v1(), "2-1", 37, 54012.6, 54015.6, _candidate_attrs())
    cand = judge_attribute(profile["candidates"][0], "vehicle_color", "human_rejected", "背景干扰")
    updated = confirm_association({**profile, "candidates": [cand]}, "2-1#37")
    assert updated["version"] == "V2"                        # 关联仍成立
    assert updated["observations"][-1]["track_id"] == 37
    assert "vehicle_color" not in {a["name"] for a in updated["attributes"][2:]}


def test_revoke_restores_previous_confirmed_state():
    profile = attach_candidate(_v1(), "2-1", 37, 54012.6, 54015.6, _candidate_attrs())
    cand = judge_attribute(profile["candidates"][0], "upper_clothing_color", "human_verified")
    v2 = confirm_association({**profile, "candidates": [cand]}, "2-1#37")
    back = revoke_association(v2, "2-1#37")
    assert back["version"] == "V1"
    assert [o["camera"] for o in back["observations"]] == ["2-2"]
    assert back["history"][-1]["action"] == "revoke_association"
