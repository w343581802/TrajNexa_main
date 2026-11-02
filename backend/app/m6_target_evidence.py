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


DEFAULT_RANK_PARAMS = {
    "top_k": 3,
    # 支撑帧：某帧的最佳匹配 ≥ 最高分 × 该比例，即视为"独立支撑"
    "support_ratio": 0.90,
    "weak_cosine": 0.60,       # 低于它连"候选"都算不上，只能算证据不足
    "margin": 0.03,            # 与下一名的差距小于它 → ambiguous
}


def compare_sets(anchor_vectors, candidate_vectors, top_k=3, support_ratio=0.90):
    """两组逐帧向量两两求 cosine，并给出多帧一致性统计。

    只报 cosine_max 容易被个别高分画面带偏，因此同时给出：
    - cosine_top_mean：前 k 个配对的均值；
    - cosine_median：全部配对的中位数（抗单帧噪声）；
    - supporting_pairs：达到支撑阈值的配对数；
    - supporting_anchor_frames / supporting_candidate_frames：这些配对覆盖到的**独立原始帧**数。
      候选只有 1 帧、锚点有 5 帧时配对数可以是 5，但候选支撑帧只有 1，不能当"多帧证据"；
    - single_frame_share：**按独立候选帧**算的领先幅度——最高分那一帧比第二好的候选帧高多少，
      越大说明结果越可能只靠一帧碰巧相似。
    """
    if not anchor_vectors or not candidate_vectors:
        return {"cosine_max": 0.0, "cosine_top_mean": 0.0, "cosine_median": 0.0,
                "pairs": 0, "supporting_pairs": 0, "supporting_anchor_frames": 0,
                "supporting_candidate_frames": 0, "single_frame_share": 0.0}
    a = np.asarray([v for _, v in anchor_vectors], dtype=np.float32)
    b = np.asarray([v for _, v in candidate_vectors], dtype=np.float32)
    sim = a @ b.T
    flat = np.sort(sim.reshape(-1))[::-1]
    k = min(top_k, flat.size)
    threshold = float(flat[0]) * support_ratio
    per_candidate = sim.max(axis=0)          # 每个候选帧的最好匹配
    per_candidate_sorted = np.sort(per_candidate)[::-1]
    best = float(per_candidate_sorted[0])
    second = float(per_candidate_sorted[1]) if per_candidate_sorted.size > 1 else 0.0
    lead = (best - second) / best if best > 0 else 0.0
    return {
        "cosine_max": float(flat[0]),
        "cosine_top_mean": float(flat[:k].mean()),
        "cosine_median": float(np.median(flat)),
        "pairs": int(flat.size),
        "supporting_pairs": int((sim >= threshold).sum()),
        "supporting_anchor_frames": int((sim.max(axis=1) >= threshold).sum()),
        "supporting_candidate_frames": int((per_candidate >= threshold).sum()),
        "single_frame_share": float(lead),
    }


def rank_tracklets(anchor_vectors, tracklets, vectors_by_tracklet, params=None, excluded=None):
    """对候选排序并给出证据状态。

    状态含义（对齐 design/45 §9.2 "不确定性与拒绝质量"）：
    - candidate：有稳定多帧支持、且在候选前列；
    - ambiguous：分数不低但与竞争者差距太小，不能据此下结论；
    - excluded_by_evidence：**只有拿到明确冲突证据**（调用方通过 excluded 传入）才可标；
    - insufficient_evidence：样本不足或整体相似度太低，连比较都不成立。

    排名靠后本身**不是**排除证据，因此不再出现 rejected_by_rank。
    """
    params = dict(DEFAULT_RANK_PARAMS, **(params or {}))
    excluded = set(excluded or ())
    ranked = []
    for key, vectors in vectors_by_tracklet.items():
        stats = compare_sets(anchor_vectors, vectors, params["top_k"], params["support_ratio"])
        ranked.append(dict(stats, tracklet_id=key, samples=len(vectors)))
    ranked.sort(key=lambda r: (-r["cosine_top_mean"], -r["cosine_max"]))

    for index, item in enumerate(ranked):
        item["rank"] = index + 1
        nxt = ranked[index + 1] if index + 1 < len(ranked) else None
        item["margin_to_next"] = round(item["cosine_top_mean"] - nxt["cosine_top_mean"], 4) if nxt else None
        if item["tracklet_id"] in excluded:
            item["status"] = "excluded_by_evidence"
        elif item["samples"] < 2 or item["cosine_top_mean"] < params["weak_cosine"]:
            item["status"] = "insufficient_evidence"
        elif item["rank"] <= 3 and item["supporting_candidate_frames"] >= 2 \
                and (item["margin_to_next"] is None or item["margin_to_next"] >= params["margin"]):
            item["status"] = "candidate"
        else:
            item["status"] = "ambiguous"
    return ranked
