"""M6-S1-1C：外观重关联层（opt-in，默认关闭）。

为什么需要单独一层：`trackers` 2.6.1 的 `BoTSORTTracker` 没有 ReID 入口，
外观关联只能自己做。这一层不改跟踪器，只在**跟踪结束后**把"同一目标被切成两段"
的轨迹按外观 + 时空合理性接回去，并留下完整证据。

边界（与验收口径一致）：
1. 默认关闭——轨迹接回不进入默认链路；
2. 只在"轨迹已结束 + 新轨迹已开始 + 时间缺口在阈值内 + 空间位置说得通"时尝试；
3. 相似度不足、或被多个候选竞争时不接——宁可断，不可接错；
4. 每次接回都记 cosine、缺口、位移，供人工核验。
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np

DEFAULT_PARAMS = {
    "max_gap_seconds": 3.0,
    "min_cosine": 0.72,
    "max_center_distance_ratio": 3.0,
    "samples_per_track": 5,
    "min_crop_height_px": 40,
    "competition_margin": 0.05,
}


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _diag(box):
    return math.hypot(box[2] - box[0], box[3] - box[1])


def _normalise(vec):
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def crop_for_embedding(frame, box, expand=0.1):
    """裁出人物区域，宽高比 1:2，供 OSNet（256×128）使用。"""
    h, w = frame.shape[:2]
    bw, bh = box[2] - box[0], box[3] - box[1]
    cx, cy = _center(box)
    half_h = bh * (0.5 + expand)
    half_w = half_h / 2.0
    x1 = max(0, int(cx - half_w))
    x2 = min(w, int(cx + half_w))
    y1 = max(0, int(cy - half_h))
    y2 = min(h, int(cy + half_h))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return frame[y1:y2, x1:x2]


def embed_tracks(tracks, frame_loader, session, params=None):
    """给每条轨迹算一个平均外观向量；样本太少或太小的轨迹不参与关联。"""
    params = dict(DEFAULT_PARAMS, **(params or {}))
    input_name = session.get_inputs()[0].name
    gallery = {}
    for track in tracks:
        obs = track["observations"]
        if not obs:
            continue
        usable = [o for o in obs if (o["bbox"][3] - o["bbox"][1]) >= params["min_crop_height_px"]]
        if len(usable) < 2:
            continue
        step = max(1, len(usable) // params["samples_per_track"])
        samples = usable[::step][: params["samples_per_track"]]
        vectors = []
        for o in samples:
            frame = frame_loader(int(o["frame_id"]))
            if frame is None:
                continue
            crop = crop_for_embedding(frame, o["bbox"])
            if crop is None:
                continue
            blob = _to_blob(crop)
            out = session.run(None, {input_name: blob})[0][0]
            vectors.append(_normalise(np.asarray(out, dtype=np.float32)))
        if len(vectors) < 2:
            continue
        gallery[track["track_id"]] = _normalise(np.mean(vectors, axis=0))
    return gallery


def _to_blob(crop):
    import cv2
    resized = cv2.resize(crop, (128, 256))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    rgb = (rgb - mean) / std
    return np.transpose(rgb, (2, 0, 1))[None, ...]


def candidate_links(tracks, gallery, params=None):
    """列出所有"时间上前后相接 + 空间上说得通"的候选对及其 cosine。"""
    params = dict(DEFAULT_PARAMS, **(params or {}))
    by_id = {t["track_id"]: t for t in tracks}
    candidates = []
    for a in tracks:
        if a["track_id"] not in gallery or not a["observations"]:
            continue
        last = a["observations"][-1]
        for b in tracks:
            if b["track_id"] == a["track_id"] or b["track_id"] not in gallery:
                continue
            if not b["observations"]:
                continue
            first = b["observations"][0]
            gap = b["start_seconds"] - a["end_seconds"]
            if gap < 0 or gap > params["max_gap_seconds"]:
                continue
            dist = math.hypot(*(np.subtract(_center(first["bbox"]), _center(last["bbox"]))))
            limit = params["max_center_distance_ratio"] * max(_diag(last["bbox"]), _diag(first["bbox"]))
            if dist > limit:
                continue
            cosine = float(np.dot(gallery[a["track_id"]], gallery[b["track_id"]]))
            candidates.append({"from": a["track_id"], "to": b["track_id"],
                               "cosine": round(cosine, 3), "gap_seconds": round(gap, 2),
                               "center_distance": round(dist, 1)})
    return candidates


def link_tracks(tracks, gallery, params=None):
    """贪心接回：按 cosine 从高到低，只接受"唯一最优"的配对。"""
    params = dict(DEFAULT_PARAMS, **(params or {}))
    by_id = {t["track_id"]: t for t in tracks}
    candidates = [c for c in candidate_links(tracks, gallery, params) if c["cosine"] >= params["min_cosine"]]
    accepted, used_from, used_to = [], set(), set()
    for cand in sorted(candidates, key=lambda c: -c["cosine"]):
        if cand["from"] in used_to or cand["to"] in used_from:
            continue
        if cand["from"] in used_from or cand["to"] in used_to:
            continue
        # 竞争：同一段轨迹还有别的候选目标，或同一目标还有别的候选来源，且分数接近。
        # 分不清谁是谁时宁可断，不可接错。
        rivals = [c for c in candidates
                  if c is not cand
                  and c["cosine"] > cand["cosine"] - params["competition_margin"]
                  and (c["from"] == cand["from"] or c["to"] == cand["to"])]
        if rivals:
            continue
        accepted.append(cand)
        used_from.add(cand["from"])
        used_to.add(cand["to"])

    merged_map = {}
    for cand in accepted:
        merged_map[cand["to"]] = cand["from"]
    return {"links": accepted,
            "merge_map": merged_map,
            "merged_tracks": merge_track_objects(tracks, merged_map)}


def merge_track_objects(tracks, merge_map):
    """把 to → from 的轨迹合并成一条，保留被接回目标的新 ID（ID 不变即"接回原目标"）。"""
    by_id = {t["track_id"]: t for t in tracks}
    children = {}
    for to_id, from_id in merge_map.items():
        children.setdefault(from_id, []).append(to_id)
    result = []
    for track in tracks:
        if track["track_id"] in merge_map:
            continue
        merged = dict(track)
        obs = list(track["observations"])
        merged_from = []
        for child_id in children.get(track["track_id"], []):
            child = by_id[child_id]
            obs.extend(child["observations"])
            merged_from.append(child_id)
        obs.sort(key=lambda o: o["timestamp_seconds"])
        merged["observations"] = obs
        if obs:
            merged["start_seconds"] = obs[0]["timestamp_seconds"]
            merged["end_seconds"] = obs[-1]["timestamp_seconds"]
            merged["duration_seconds"] = round(merged["end_seconds"] - merged["start_seconds"], 3)
        if merged_from:
            merged["merged_from"] = merged_from
        result.append(merged)
    return result
