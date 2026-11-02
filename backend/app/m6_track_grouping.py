"""M6-S4-1A：重复轨迹归组（PhysicalObjectCandidate）。

问题：同一目标被检测出多个框（整体框、上身框等），ByteTrack 会开出多条**并行轨迹**；
S4-1 首轮里同一名骑电动车女性的 41/50/53/64/67 五条轨迹同时存在。
如果直接把它们当成五个候选，候选数、覆盖率、召回都会虚高。

本模块只**归组**，不改原始 Track ID：
    PhysicalObjectCandidate_001 = {41, 50, 53, 64, 67}
五条轨迹各自保留观测与证据，归组记录也保留每一条的判定依据，便于人工推翻。

归组判据（**不预设单一 cosine 阈值**，按证据组合判断）：
1. 时间重叠：两条轨迹在同一时段存在观测；
2. 空间一致：重叠帧的框 IoU / 中心距 / 尺寸比；
3. 外观一致：**逐帧两两余弦的中位数**与达成比例的帧数，而不是只看最大值；
4. 运动一致：重叠时段内中心位移方向的一致性；
5. 冲突检查：同一帧里两条框相距很远 = "同时出现的不同目标"，禁止归组。

阈值来自人工核验过的样本（design/44 §D.19），可调用时覆盖。
"""
from __future__ import annotations

import math

import numpy as np

DEFAULT_GROUP_RULES = {
    "min_overlap_frames": 3,
    "min_overlap_ratio": 0.30,
    "min_iou": 0.30,
    "min_containment": 0.70,
    "max_center_ratio": 0.60,
    "max_size_ratio": 3.00,
    "min_cosine_median": 0.78,
    "min_cosine_hit_ratio": 0.60,
    "cosine_hit_threshold": 0.75,
    "min_motion_agreement": 0.50,
    "conflict_center_ratio": 1.20,
}


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _height(box):
    return max(1.0, box[3] - box[1])


def _containment(a, b):
    """交集 / 较小框面积：一个框套在另一个框里时（整体框 vs 上身框）仍能给出高值。"""
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / smaller if smaller > 0 else 0.0


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _obs_by_frame(track):
    return {str(obs["frame_id"]): obs for obs in track["observations"]}


def pair_evidence(track_a, track_b, frame_vectors=None, rules=None):
    """计算两条轨迹"是否为同一物理目标"的全部证据；只给证据，不做最终裁决。"""
    rules = dict(DEFAULT_GROUP_RULES, **(rules or {}))
    frames_a, frames_b = _obs_by_frame(track_a), _obs_by_frame(track_b)
    shared = sorted(set(frames_a) & set(frames_b), key=lambda f: float(f))

    ious, containments, center_ratios, size_ratios = [], [], [], []
    conflicts = 0
    for frame in shared:
        box_a, box_b = frames_a[frame]["bbox"], frames_b[frame]["bbox"]
        ious.append(_iou(box_a, box_b))
        containments.append(_containment(box_a, box_b))
        ref = max(_height(box_a), _height(box_b))
        dist = math.hypot(_center(box_a)[0] - _center(box_b)[0],
                          _center(box_a)[1] - _center(box_b)[1])
        ratio = dist / ref
        center_ratios.append(ratio)
        size_ratios.append(max(_height(box_a), _height(box_b)) / min(_height(box_a), _height(box_b)))
        if ratio > rules["conflict_center_ratio"]:
            conflicts += 1

    motion_agreement = None
    if len(shared) >= 3:
        mid = shared[len(shared) // 2]

        def velocity(obs_map):
            start, end = obs_map[shared[0]]["bbox"], obs_map[mid]["bbox"]
            return (_center(end)[0] - _center(start)[0], _center(end)[1] - _center(start)[1])

        va, vb = velocity(frames_a), velocity(frames_b)
        # 位移太小（接近静止目标）时方向没有意义，记为"未知"而不是 0
        scale = max(_height(frames_a[shared[0]]["bbox"]), 1.0)
        norm = math.hypot(*va) * math.hypot(*vb)
        if norm > 0 and math.hypot(*va) >= 0.15 * scale and math.hypot(*vb) >= 0.15 * scale:
            motion_agreement = max(0.0, (va[0] * vb[0] + va[1] * vb[1]) / norm)

    cosine_median = cosine_hit_ratio = None
    cosine_frames = 0
    if frame_vectors:
        vec_a = frame_vectors.get(track_a["track_id"], {})
        vec_b = frame_vectors.get(track_b["track_id"], {})
        per_frame = [float(vec_a[f] @ vec_b[f]) for f in shared if f in vec_a and f in vec_b]
        if per_frame:
            cosine_frames = len(per_frame)
            cosine_median = float(np.median(per_frame))
            hit = sum(1 for c in per_frame if c >= rules["cosine_hit_threshold"])
            cosine_hit_ratio = hit / float(len(per_frame))

    shorter = min(len(frames_a), len(frames_b))
    evidence = {
        "track_a": track_a["track_id"],
        "track_b": track_b["track_id"],
        "overlap_frames": len(shared),
        "overlap_ratio": round(len(shared) / shorter, 3) if shorter else 0.0,
        "mean_iou": round(sum(ious) / len(ious), 3) if ious else 0.0,
        "median_containment": round(sorted(containments)[len(containments) // 2], 3) if containments else 0.0,
        "median_center_ratio": round(sorted(center_ratios)[len(center_ratios) // 2], 3) if center_ratios else None,
        "max_size_ratio": round(max(size_ratios), 2) if size_ratios else None,
        "motion_agreement": None if motion_agreement is None else round(motion_agreement, 3),
        "cosine_frames": cosine_frames,
        "cosine_median": None if cosine_median is None else round(cosine_median, 3),
        "cosine_hit_ratio": None if cosine_hit_ratio is None else round(cosine_hit_ratio, 3),
        "conflict_frames": conflicts,
    }
    evidence["verdict"] = verdict_for(evidence, rules)
    return evidence


def verdict_for(ev, rules=None):
    """把证据映射成 same_object / needs_review / conflict_coexist / not_overlapping。"""
    rules = dict(DEFAULT_GROUP_RULES, **(rules or {}))
    if ev["overlap_frames"] < rules["min_overlap_frames"] or ev["overlap_ratio"] < rules["min_overlap_ratio"]:
        return "not_overlapping"
    if ev["conflict_frames"] > 0:
        return "conflict_coexist"
    # 一个框整体套在另一个框里（整体框 vs 上身/头部框）时，尺寸比必然很大，
    # 这时用"包含度"代替尺寸比，否则会把同一个目标判成两个。
    nested = ev["median_containment"] >= rules["min_containment"]
    checks = [
        ev["mean_iou"] >= rules["min_iou"] or nested,
        ev["median_center_ratio"] is not None and ev["median_center_ratio"] <= rules["max_center_ratio"],
        nested or ev["max_size_ratio"] is None or ev["max_size_ratio"] <= rules["max_size_ratio"],
        ev["cosine_median"] is None or ev["cosine_median"] >= rules["min_cosine_median"],
        ev["cosine_hit_ratio"] is None or ev["cosine_hit_ratio"] >= rules["min_cosine_hit_ratio"],
        ev["motion_agreement"] is None or ev["motion_agreement"] >= rules["min_motion_agreement"],
    ]
    return "same_object" if all(checks) else "needs_review"


def group_tracks(tracks, frame_vectors=None, rules=None):
    """把轨迹并成 PhysicalObjectCandidate；原始 Track ID 全部保留。"""
    rules = dict(DEFAULT_GROUP_RULES, **(rules or {}))
    parent = {t["track_id"]: t["track_id"] for t in tracks}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    evidence = []
    for i, a in enumerate(tracks):
        for b in tracks[i + 1:]:
            ev = pair_evidence(a, b, frame_vectors, rules)
            evidence.append(ev)
            if ev["verdict"] == "same_object":
                parent[find(a["track_id"])] = find(b["track_id"])

    members = {}
    for track in tracks:
        members.setdefault(find(track["track_id"]), []).append(track["track_id"])

    groups = []
    ordered = sorted(members.values(), key=lambda ids: (-len(ids), min(ids)))
    for index, ids in enumerate(ordered, start=1):
        ids = sorted(ids)
        groups.append({
            "candidate_id": "PhysicalObjectCandidate_%03d" % index,
            "track_ids": ids,
            "grouped": len(ids) > 1,
            "evidence": [e for e in evidence
                         if e["track_a"] in ids and e["track_b"] in ids and e["verdict"] == "same_object"],
        })
    return groups, evidence
