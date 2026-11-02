"""P1-B-1B：人员与两轮车的时序关系候选生成。

四条证据缺一不可，且单帧不成立：
1. 空间：人框与车框的相对位置（骑手的人框底部停在车身中部以上，而不是站在车旁）；
2. 运动：车与人在移动时方向一致；
3. 时间：连续满足 ≥ N 帧；
4. 竞争：同一辆车同时有多人满足条件时不下结论。

人员轨迹与车辆轨迹始终独立，本模块只产出**候选关系**，带起止时间与核验状态。
不做跨镜头、不写库、不更新任何特征库。
"""
from __future__ import annotations

import math

DEFAULT_PARAMS = {
    # 人框与车框的交集占人框面积的比例
    "min_overlap_ratio": 0.25,
    # 骑手：人框底边必须高于"车框中线 + margin×车高"（站在车旁时脚会落到车框下方）
    "rider_bottom_margin": 0.10,
    # 连续多少帧才成立
    "riding_min_frames": 8,
    "approach_min_frames": 5,
    # 靠近：人车中心距离小于该值、整段确实在缩短，且不是"长时间站在车旁"
    "approach_distance_px": 140.0,
    "approach_min_delta_px": 25.0,
    "approach_max_seconds": 8.0,
    # 分离：连续多少帧不再重叠才算结束
    "separation_frames": 3,
    # 共动：用整段的位移方向（不是相邻两帧），并在 5 fps 下取 12 px 为最小可判位移
    "co_move_min_cos": 0.6,
    "co_move_min_px": 12.0,
}


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _distance(a, b):
    ca, cb = _center(a), _center(b)
    return math.hypot(cb[0] - ca[0], cb[1] - ca[1])


def overlap_ratio(person_box, vehicle_box):
    """交集 / 人框面积：人站在车里（骑车）时接近 1，站在旁边时接近 0。"""
    x1, y1 = max(person_box[0], vehicle_box[0]), max(person_box[1], vehicle_box[1])
    x2, y2 = min(person_box[2], vehicle_box[2]), min(person_box[3], vehicle_box[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    area = max(1.0, (person_box[2] - person_box[0]) * (person_box[3] - person_box[1]))
    return ((x2 - x1) * (y2 - y1)) / area


def rider_geometry(person_box, vehicle_box, params):
    """骑手位置判据：人框底边高于车身中线，且横向落在车身范围内。"""
    vehicle_h = vehicle_box[3] - vehicle_box[1]
    limit = vehicle_box[1] + vehicle_h * (0.5 + params["rider_bottom_margin"])
    vertical = person_box[3] <= limit
    horizontal = person_box[0] < vehicle_box[2] and person_box[2] > vehicle_box[0]
    return vertical and horizontal


def _co_move(segment, params):
    """整段位移方向是否同向。

    不能用相邻两帧：5 fps 下单帧位移只有几像素，永远够不到阈值，
    等于把共动检查关掉。用整段首尾位移才能真实反映"一起走"。
    """
    p0, p1 = segment[0]["person_bbox"], segment[-1]["person_bbox"]
    v0, v1 = segment[0]["vehicle_bbox"], segment[-1]["vehicle_bbox"]
    dp = (_center(p1)[0] - _center(p0)[0], _center(p1)[1] - _center(p0)[1])
    dv = (_center(v1)[0] - _center(v0)[0], _center(v1)[1] - _center(v0)[1])
    np_, nv = math.hypot(*dp), math.hypot(*dv)
    floor = params["co_move_min_px"]
    if np_ < floor and nv < floor:
        return None      # 都基本没动（停着骑/停着坐），不能据此判否
    if np_ < 1e-6 or nv < 1e-6:
        return 0.0       # 一个动了、一个没动，不同向
    return (dp[0] * dv[0] + dp[1] * dv[1]) / (np_ * nv)


def _frame_pairs(persons, vehicles, params):
    """同一帧内所有"可能有关系"的人车对。"""
    pairs = []
    for person in persons:
        for vehicle in vehicles:
            ratio = overlap_ratio(person["bbox"], vehicle["bbox"])
            distance = _distance(person["bbox"], vehicle["bbox"])
            if ratio < params["min_overlap_ratio"] and distance > params["approach_distance_px"]:
                continue
            pairs.append({"person": person, "vehicle": vehicle, "overlap": ratio,
                          "distance": distance,
                          "rider_geometry": rider_geometry(person["bbox"], vehicle["bbox"], params)})
    return pairs


def _runs(per_frame, predicate, min_frames):
    """把逐帧判定压成连续区间。"""
    segments, current = [], []
    for item in per_frame:
        if predicate(item):
            current.append(item)
        else:
            if len(current) >= min_frames:
                segments.append(current)
            current = []
    if len(current) >= min_frames:
        segments.append(current)
    return segments


def _observations(track):
    """轨迹里的观测既可能是 list，也可能是 {index: obs} 的 dict，也可能是计数（此时用 frames）。"""
    obs = track.get("observations")
    if isinstance(obs, list):
        return obs
    if isinstance(obs, dict):
        return list(obs.values())
    frames = track.get("frames")
    return frames if isinstance(frames, list) else []


def build_relations(person_tracks, vehicle_tracks, params=None, vehicle_types=None):
    params = dict(DEFAULT_PARAMS, **(params or {}))
    vehicle_types = vehicle_types or {}

    person_by_frame = {}
    for track in person_tracks:
        for obs in _observations(track):
            person_by_frame.setdefault(obs["frame_id"], []).append(
                {"track_id": track["track_id"], "bbox": obs["bbox"],
                 "timestamp_seconds": obs["timestamp_seconds"]})
    vehicle_by_frame = {}
    for track in vehicle_tracks:
        for obs in _observations(track):
            vehicle_by_frame.setdefault(obs["frame_id"], []).append(
                {"track_id": track["track_id"], "bbox": obs["bbox"],
                 "timestamp_seconds": obs["timestamp_seconds"]})

    frame_ids = sorted(set(person_by_frame) & set(vehicle_by_frame),
                       key=lambda fid: person_by_frame[fid][0]["timestamp_seconds"])
    pair_history = {}
    competition = {}
    for fid in frame_ids:
        pairs = _frame_pairs(person_by_frame[fid], vehicle_by_frame[fid], params)
        for pair in pairs:
            key = (pair["person"]["track_id"], pair["vehicle"]["track_id"])
            pair_history.setdefault(key, []).append({
                "frame_id": fid,
                "timestamp_seconds": pair["person"]["timestamp_seconds"],
                "overlap": pair["overlap"],
                "distance": pair["distance"],
                "rider_geometry": pair["rider_geometry"],
                "person_bbox": pair["person"]["bbox"],
                "vehicle_bbox": pair["vehicle"]["bbox"],
            })
        by_vehicle = {}
        for pair in pairs:
            if pair["overlap"] >= params["min_overlap_ratio"] and pair["rider_geometry"]:
                by_vehicle.setdefault(pair["vehicle"]["track_id"], []).append(pair["person"]["track_id"])
        for vehicle_id, people in by_vehicle.items():
            if len(people) > 1:
                competition.setdefault(vehicle_id, []).append({"frame_id": fid, "persons": people})

    candidates, events, errors = [], [], []
    for (person_id, vehicle_id), history in sorted(pair_history.items()):
        history.sort(key=lambda h: h["timestamp_seconds"])
        riding = _runs(history,
                       lambda h: h["overlap"] >= params["min_overlap_ratio"] and h["rider_geometry"],
                       params["riding_min_frames"])
        approaching = _runs(history,
                            lambda h: h["overlap"] < params["min_overlap_ratio"]
                            and h["distance"] <= params["approach_distance_px"],
                            params["approach_min_frames"])
        competed = any(person_id in c["persons"] for c in competition.get(vehicle_id, []))
        vehicle_type = vehicle_types.get(vehicle_id, {})
        for segment in riding:
            co_move = _co_move(segment, params)
            co_move = round(co_move, 3) if co_move is not None else None
            status = "unverified"
            relation = "riding_candidate"
            if competed:
                relation, status = "unknown", "ambiguous_multi_person"
                errors.append({"type": "multi_person_competition", "person_track_id": person_id,
                               "vehicle_track_id": vehicle_id,
                               "start_seconds": segment[0]["timestamp_seconds"],
                               "end_seconds": segment[-1]["timestamp_seconds"]})
            elif co_move is None:
                # 车没动（或人和车都没动）：单靠框的位置分不清"坐在车上"和"站在车后"。
                # 实测这类候选全是误判，一律降为 unknown。
                relation, status = "unknown", "stationary_vehicle_unverifiable"
                errors.append({"type": "stationary_vehicle_unverifiable", "person_track_id": person_id,
                               "vehicle_track_id": vehicle_id,
                               "start_seconds": segment[0]["timestamp_seconds"],
                               "end_seconds": segment[-1]["timestamp_seconds"]})
            elif co_move < params["co_move_min_cos"]:
                relation, status = "unknown", "motion_inconsistent"
                errors.append({"type": "motion_inconsistent", "person_track_id": person_id,
                               "vehicle_track_id": vehicle_id,
                               "start_seconds": segment[0]["timestamp_seconds"],
                               "end_seconds": segment[-1]["timestamp_seconds"]})
            candidates.append({
                "person_track_id": person_id,
                "vehicle_track_id": vehicle_id,
                "vehicle_type": vehicle_type.get("vehicle_type", "unknown"),
                "vehicle_type_status": vehicle_type.get("vehicle_type_status", "unverified"),
                "relation": relation,
                "start_seconds": round(segment[0]["timestamp_seconds"], 2),
                "end_seconds": round(segment[-1]["timestamp_seconds"], 2),
                "status": status,
                "evidence": {
                    "frames": len(segment),
                    "frame_ids": [s["frame_id"] for s in segment],
                    "mean_overlap": round(sum(s["overlap"] for s in segment) / len(segment), 3),
                    "co_move_cos": co_move,
                },
            })
            events.append({"type": "riding_started" if relation == "riding_candidate" else "relation_unknown",
                           "person_track_id": person_id, "vehicle_track_id": vehicle_id,
                           "timestamp_seconds": round(segment[0]["timestamp_seconds"], 2)})
            events.append({"type": "riding_ended",
                           "person_track_id": person_id, "vehicle_track_id": vehicle_id,
                           "timestamp_seconds": round(segment[-1]["timestamp_seconds"], 2)})
        for segment in approaching:
            # 只在"确实在靠近"时输出：整段距离明显缩短、且过程不拖长。
            # 否则只是人在车旁边走了很久，不能叫 approaching。
            if segment[0]["distance"] - segment[-1]["distance"] < params["approach_min_delta_px"]:
                continue
            if (segment[-1]["timestamp_seconds"] - segment[0]["timestamp_seconds"]) > params["approach_max_seconds"]:
                continue
            candidates.append({
                "person_track_id": person_id,
                "vehicle_track_id": vehicle_id,
                "vehicle_type": vehicle_type.get("vehicle_type", "unknown"),
                "vehicle_type_status": vehicle_type.get("vehicle_type_status", "unverified"),
                "relation": "approaching_candidate",
                "start_seconds": round(segment[0]["timestamp_seconds"], 2),
                "end_seconds": round(segment[-1]["timestamp_seconds"], 2),
                "status": "unverified",
                "evidence": {"frames": len(segment),
                             "mean_distance": round(sum(s["distance"] for s in segment) / len(segment), 1)},
            })
    return {"candidates": candidates, "events": events, "errors": errors}
