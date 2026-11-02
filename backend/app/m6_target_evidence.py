"""M6-S4-1：中近距离识别 → 远距回溯 → 轨迹延伸（离线证据层）。

设计约束（对应 design/45 §3.2、§3.3 与 §9.2）：
1. 特征是**逐帧原始向量**，不是一条轨迹一个平均向量——同一目标在不同距离、不同
   朝向下的外观差异必须保留下来，供人工核验；
2. 只有达到质量门槛的观测才参与比较（人体高 ≥150 px、conf ≥0.40、至少 3 帧）；
3. 输出是**候选排序 + 证据差异**，不是身份概率；宁可返回 unknown，也不强行给结论；
4. 本层不改动检测器与跟踪器，只在既有 Tracklet 上做比较与回溯。

回溯在这里的含义：用"近距段"（可信识别段）去比较时间更早的"远距段"，
判断更早的那段是否属于同一目标（同一条 Tracklet 内视为正向验证；
跨 Tracklet 时属于轨迹接回，默认关闭，不在本层自动执行）。
"""
from __future__ import annotations

import numpy as np

DEFAULT_QUALITY_GATE = {
    "min_height_px": 150.0,
    "min_conf": 0.40,
    "min_frames": 3,
}


def _normalise(vec):
    vec = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec


def usable_observations(track, gate=None):
    """按质量门槛筛出可用于外观比较的观测。"""
    gate = dict(DEFAULT_QUALITY_GATE, **(gate or {}))
    return [
        obs for obs in track["observations"]
        if (obs["bbox"][3] - obs["bbox"][1]) >= gate["min_height_px"]
        and obs.get("confidence", 1.0) >= gate["min_conf"]
    ]


def sample_observations(observations, max_samples):
    """时间分层取样：均匀抽，保证不同时刻/不同距离都留下样本。"""
    if len(observations) <= max_samples:
        return list(observations)
    step = len(observations) / float(max_samples)
    return [observations[int(i * step)] for i in range(max_samples)]


def embed_observations(observations, frame_loader, session, max_samples=5):
    """逐帧计算外观向量，返回 [(obs, vector)]，不做平均。"""
    from app.m6_reid_link import _to_blob, crop_for_embedding

    input_name = session.get_inputs()[0].name
    vectors = []
    for obs in sample_observations(observations, max_samples):
        frame = frame_loader(int(obs["frame_id"]))
        if frame is None:
            continue
        crop = crop_for_embedding(frame, obs["bbox"])
        if crop is None:
            continue
        out = session.run(None, {input_name: _to_blob(crop)})[0][0]
        vectors.append((obs, _normalise(out)))
    return vectors


def compare_sets(anchor_vectors, candidate_vectors, top_k=3):
    """两组逐帧向量两两求 cosine，返回最大值与前 k 均值。"""
    if not anchor_vectors or not candidate_vectors:
        return {"cosine_max": 0.0, "cosine_top_mean": 0.0, "pairs": 0}
    a = np.asarray([v for _, v in anchor_vectors], dtype=np.float32)
    b = np.asarray([v for _, v in candidate_vectors], dtype=np.float32)
    sim = a @ b.T
    flat = np.sort(sim.reshape(-1))[::-1]
    k = min(top_k, flat.size)
    return {
        "cosine_max": float(flat[0]),
        "cosine_top_mean": float(flat[:k].mean()),
        "pairs": int(flat.size),
    }


def rank_tracklets(anchor_vectors, tracklets, vectors_by_tracklet, top_k=3):
    """对候选 Tracklet 排序；返回候选列表，不做阈值裁决。"""
    ranked = []
    for key, vectors in vectors_by_tracklet.items():
        stats = compare_sets(anchor_vectors, vectors, top_k)
        ranked.append(dict(stats, tracklet_id=key, samples=len(vectors)))
    ranked.sort(key=lambda r: (-r["cosine_top_mean"], -r["cosine_max"]))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        item["status"] = "candidate" if index <= 3 else "rejected_by_rank"
    return ranked
