"""P1-B-1A-2 聚合规则的确定性测试：只验证"什么时候必须 unknown"。"""
from scripts.p1b_vehicle_type import aggregate


def _item(crop, track_id=1, box=(0, 0, 200, 120)):
    return {"track_id": track_id, "frame_id": "f1", "timestamp_seconds": 1.0,
            "bbox": list(box), "score": 1.0, "detector_label": "摩托车", "crop": crop}


def test_small_crop_is_forced_unknown():
    manifest = [_item("a.jpg", box=(0, 0, 50, 50))]
    votes = {"a.jpg": {"vehicle_type": "motorcycle", "confidence": 0.9, "evidence": "排气筒"}}
    result = aggregate(manifest, votes)
    assert result[0]["vehicle_type"] == "unknown"
    assert result[0]["votes_forced_unknown_by_size"] == 1


def test_large_crop_keeps_model_type():
    manifest = [_item("b.jpg")]
    votes = {"b.jpg": {"vehicle_type": "electric_two_wheeler", "confidence": 0.95, "evidence": "踏板式"}}
    result = aggregate(manifest, votes)
    assert result[0]["vehicle_type"] == "electric_two_wheeler"
    assert result[0]["vehicle_type_status"] == "model_inferred"


def test_majority_vote_across_keyframes():
    manifest = [_item("c1.jpg"), _item("c2.jpg"), _item("c3.jpg")]
    votes = {"c1.jpg": {"vehicle_type": "electric_two_wheeler", "confidence": 0.9},
             "c2.jpg": {"vehicle_type": "electric_two_wheeler", "confidence": 0.9},
             "c3.jpg": {"vehicle_type": "motorcycle", "confidence": 0.9}}
    result = aggregate(manifest, votes)
    assert result[0]["vehicle_type"] == "electric_two_wheeler"


def test_human_override_wins():
    manifest = [_item("d.jpg")]
    votes = {"d.jpg": {"vehicle_type": "bicycle", "confidence": 0.9}}
    result = aggregate(manifest, votes, overrides={"1": "motorcycle"})
    assert result[0]["vehicle_type"] == "motorcycle"
    assert result[0]["vehicle_type_status"] == "human_confirmed"
