"""P1-B-1B 规则测试：重点验证"不该判定骑乘的时候确实不判"。"""
from app.m6_person_vehicle_relation import build_relations


def _track(track_id, boxes):
    """boxes: [(frame_id, ts, bbox), ...]"""
    return {"track_id": track_id,
            "observations": [{"frame_id": fid, "timestamp_seconds": ts, "bbox": list(box)}
                             for fid, ts, box in boxes]}


def _seq(start_ts, count, step=0.2):
    return [("f%d" % i, round(start_ts + i * step, 2)) for i in range(count)]


def _person(track_id, frames, box_fn):
    return _track(track_id, [(fid, ts, box_fn(i)) for i, (fid, ts) in enumerate(frames)])


def test_moving_rider_is_riding_candidate():
    frames = _seq(100.0, 12)
    # 人和车一起向右走：位置关系保持 + 整段同向位移，才是骑乘证据
    person = _person(1, frames, lambda i: (620 + i * 3, 220, 660 + i * 3, 340))
    vehicle = _person(10, frames, lambda i: (600 + i * 3, 300, 700 + i * 3, 400))
    result = build_relations([person], [vehicle])
    riding = [c for c in result["candidates"] if c["relation"] == "riding_candidate"]
    assert len(riding) == 1
    assert riding[0]["status"] == "unverified"
    assert riding[0]["start_seconds"] < riding[0]["end_seconds"]
    assert len(riding[0]["evidence"]["frame_ids"]) >= 8
    assert riding[0]["evidence"]["co_move_cos"] > 0.6


def test_stationary_overlap_is_not_riding():
    """停在原地的车 + 位置像骑手的人：分不清坐着还是站在车后，只能 unknown。"""
    frames = _seq(100.0, 12)
    person = _person(1, frames, lambda i: (620, 220, 660, 340))
    vehicle = _person(10, frames, lambda i: (600, 300, 700, 400))
    result = build_relations([person], [vehicle])
    assert not [c for c in result["candidates"] if c["relation"] == "riding_candidate"]
    assert any(e["type"] == "stationary_vehicle_unverifiable" for e in result["errors"])


def test_pedestrian_beside_parked_vehicle_is_not_riding():
    frames = _seq(100.0, 12)
    # 人站在车旁：人框底边与车框底边齐平，落在车身中线以下
    person = _person(2, frames, lambda i: (610, 250, 660, 400))
    vehicle = _person(10, frames, lambda i: (600, 300, 700, 400))
    result = build_relations([person], [vehicle])
    assert not [c for c in result["candidates"] if c["relation"] == "riding_candidate"]


def test_single_frame_overlap_is_not_enough():
    frames = _seq(100.0, 1)
    person = _person(3, frames, lambda i: (620, 220, 660, 340))
    vehicle = _person(10, frames, lambda i: (600, 300, 700, 400))
    result = build_relations([person], [vehicle])
    assert not result["candidates"]


def test_two_people_on_one_vehicle_stays_unknown():
    frames = _seq(100.0, 12)
    a = _person(1, frames, lambda i: (620, 220, 660, 340))
    b = _person(2, frames, lambda i: (625, 225, 665, 342))
    vehicle = _person(10, frames, lambda i: (600, 300, 700, 400))
    result = build_relations([a, b], [vehicle])
    assert result["candidates"]
    assert all(c["relation"] == "unknown" for c in result["candidates"])
    assert any(e["type"] == "multi_person_competition" for e in result["errors"])


def test_opposite_motion_marks_unknown():
    frames = _seq(100.0, 12)
    person = _person(1, frames, lambda i: (620 + i * 2, 220, 660 + i * 2, 340))
    vehicle = _person(10, frames, lambda i: (600 - i * 1, 300, 700 - i * 1, 400))
    result = build_relations([person], [vehicle])
    riding = [c for c in result["candidates"] if c["relation"] == "riding_candidate"]
    assert not riding
    assert any(e["type"] == "motion_inconsistent" for e in result["errors"])


def test_approaching_is_candidate_not_riding():
    frames = _seq(100.0, 10)
    person = _person(1, frames, lambda i: (765 - i * 4, 300, 805 - i * 4, 420))
    vehicle = _person(10, frames, lambda i: (600, 300, 700, 400))
    result = build_relations([person], [vehicle])
    kinds = {c["relation"] for c in result["candidates"]}
    assert "approaching_candidate" in kinds
