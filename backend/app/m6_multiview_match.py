"""M6-S4-2：多视角特征匹配（A/B/C 三组对照）。

同一目标在录像里会因为朝向、距离、遮挡而呈现完全不同的画面，
本模块比较三种比对方式在"视角变化"下的稳定性：

- A `aggregate`：把一个候选对象的所有采样平均成一个向量，只算一次余弦（当前基线）；
- B `per_frame`：逐帧两两比较，取前 k 名的均值（保留不同视角的画面）；
- C `view_weighted`：逐帧比较 + **视角分层**，按画面质量（像素高度、置信度、是否贴边裁切）
  加权，并保证前 k 名里至少包含两个不同视角的证据，避免结果被某一帧"幸运高分"决定。

评测约束（防止自己给自己打分）：
1. 参考（anchor）与候选之间必须有最小时间间隔；
2. 参考所在的视角段不允许出现在候选池里；
3. 低质量画面允许判 `insufficient_evidence`，不强行给匹配结论。

视角标签由人工标注提供，本模块不引入姿态识别模型。
"""
from __future__ import annotations

import numpy as np

DEFAULT_MATCH_PARAMS = {
    "top_k": 3,
    "quality_height_ref": 200.0,   # 达到这个像素高度算"清晰"
    "min_height_px": 40.0,
    "min_gap_seconds": 1.0,        # 参考与候选的最小时间间隔
    "support_ratio": 0.90,
}


def label_views(observations, segments):
    """按人工标注的时间段给每帧打视角标签；没有标注的帧为 unknown。"""
    labelled = []
    for obs in observations:
        ts = float(obs["timestamp_seconds"])
        view = "unknown"
        for seg in segments:
            if float(seg["from"]) <= ts < float(seg["to"]):
                view = seg["view"]
                break
        labelled.append((obs, view))
    return labelled


def quality_weight(obs, params=None):
    """画面质量权重：像素高度、置信度、是否贴边（贴边说明人已被画面切断）。"""
    params = dict(DEFAULT_MATCH_PARAMS, **(params or {}))
    x1, y1, x2, y2 = obs["bbox"]
    height = max(y2 - y1, 1.0)
    size_term = min(height / params["quality_height_ref"], 1.0) ** 0.5
    conf_term = float(obs.get("confidence", 1.0))
    # 框贴到左/上边界说明目标被画面切断，可见性不足，降权但不丢弃
    border = 1.0
    if x1 <= 2 or y1 <= 2:
        border = 0.6
    return max(0.05, size_term * conf_term * border)


def separated(anchor_obs, cand_obs, params=None):
    params = dict(DEFAULT_MATCH_PARAMS, **(params or {}))
    gap = params["min_gap_seconds"]
    return abs(float(anchor_obs["timestamp_seconds"]) - float(cand_obs["timestamp_seconds"])) >= gap


def compare(anchor_pairs, cand_pairs, mode="per_frame", params=None):
    """anchor_pairs/cand_pairs: [(obs, view, vector), ...]。返回相似度与支撑证据。"""
    params = dict(DEFAULT_MATCH_PARAMS, **(params or {}))
    if not anchor_pairs or not cand_pairs:
        return {"score": 0.0, "supporting_frames": 0, "views_used": 0, "pairs": 0}

    if mode == "aggregate":
        a = np.mean([v for _, _, v in anchor_pairs], axis=0)
        c = np.mean([v for _, _, v in cand_pairs], axis=0)
        a = a / (np.linalg.norm(a) or 1.0)
        c = c / (np.linalg.norm(c) or 1.0)
        return {"score": float(a @ c), "supporting_frames": len(cand_pairs),
                "views_used": len({view for _, view, _ in cand_pairs}), "pairs": 1}

    scored = []
    for a_obs, a_view, a_vec in anchor_pairs:
        for c_obs, c_view, c_vec in cand_pairs:
            if not separated(a_obs, c_obs, params):
                continue
            cos = float(a_vec @ c_vec)
            weight = 1.0
            if mode == "view_weighted":
                weight = quality_weight(a_obs, params) * quality_weight(c_obs, params)
            scored.append((cos, weight, c_view, cos * weight))
    if not scored:
        return {"score": 0.0, "supporting_frames": 0, "views_used": 0, "pairs": 0}

    if mode == "view_weighted":
        # 质量/视角只用来**挑选证据**，最终得分仍是原始余弦的均值，
        # 否则"画面小"的候选天然拿低分，跨候选就不可比了。
        scored.sort(key=lambda item: -(item[0] * item[1]))
        top = scored[: params["top_k"]]
        views = {item[2] for item in top}
        if len(views) < 2:
            for item in scored:
                if item[2] not in views:
                    top = top[: params["top_k"] - 1] + [item]
                    break
    else:
        scored.sort(key=lambda item: -item[0])
        top = scored[: params["top_k"]]

    best = max(item[0] for item in scored)
    support = len([1 for item in scored if item[0] >= best * params["support_ratio"]])
    return {"score": float(np.mean([item[0] for item in top])),
            "supporting_frames": support,
            "views_used": len({item[2] for item in top}),
            "pairs": len(scored)}
