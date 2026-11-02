"""M6-S5：指定目标离线研判流程（把已完成的实验模块串成一条链）。

流程（与设计文档一致）：

    参考图 → 参考档案 → 检测+跟踪 → 重复轨迹归组 → 中近距离外观比对
           → 目标候选 → 轨迹回溯（延伸/空档） → 关联证据检查 → 关键画面 → 证据报告

本模块只做**编排与证据汇总**，不训练、不改库、不画 UI：

- 检测+跟踪结果以 `tracks` 形式传入（离线复用已验证的 ByteTrack 输出）；
- 外观向量以 `frame_vectors` 传入，避免把 ONNX 推理耦合进流程；
- 关键画面分数由调用方传入（来自 S4-4A 的 `score_frames`）。

输出同时回答两个**独立**的问题：
1. 这段画面是否有足够证据属于指定目标（association_status）；
2. 这些画面是否具有人工核验价值（quality_status / key frames）。
"""
from __future__ import annotations

from app.m6_evidence_label import evidence_value
from app.m6_multiview_match import compare
from app.m6_track_grouping import coverage_gaps, group_tracks

DEFAULT_PIPELINE_PARAMS = {
    "top_k_candidates": 5,
    "keyframe_count": 6,
    "match_mode": "per_frame",
}


def _pairs(track_ids, frame_vectors):
    """把若干轨道的逐帧向量拼成 compare() 需要的 [(obs, view, vec)] 形式。"""
    pairs = []
    for tid, vectors in frame_vectors.items():
        for frame_id, vector in vectors.items():
            pairs.append(({"frame_id": frame_id, "track_id": tid,
                           "timestamp_seconds": float(frame_id) / 5.0}, "unknown", vector))
    return pairs


def run_pipeline(*, tracks, frame_vectors, reference_track, params=None):
    params = dict(DEFAULT_PIPELINE_PARAMS, **(params or {}))
    stages = []

    # 1) 参考档案：记录参考轨迹与可用帧
    reference_frames = sorted(frame_vectors.get(reference_track, {}))
    if not reference_frames:
        return {"ok": False, "reason": "reference_has_no_frames",
                "reference_track": reference_track, "stages": stages}
    archive = {"target_track": reference_track, "reference_frames": reference_frames,
               "frame_count": len(reference_frames)}
    stages.append({"stage": "reference_archive", "status": "ok",
                   "detail": {"frames": len(reference_frames)}})

    # 2) 归组
    groups, pair_evidence = group_tracks(tracks, frame_vectors)
    multi = [g for g in groups if len(g["track_ids"]) > 1]
    stages.append({"stage": "grouping", "status": "ok",
                   "detail": {"objects": len(groups), "multi_track_groups": len(multi),
                              "merged_relations": sum(len(g["evidence"]) for g in multi),
                              "group_conflicts": sum(1 for g in groups if g["group_conflict"])}})

    # 3) 参考轨迹所在候选对象
    owner = next((g for g in groups if reference_track in g["track_ids"]), None)
    if owner is None:
        return {"ok": False, "reason": "reference_not_grouped", "stages": stages}
    anchor = _pairs(owner["track_ids"], {t: frame_vectors.get(t, {})
                                         for t in owner["track_ids"]})

    # 4) 逐候选对象比对（同一身份的其他轨迹不参与负样本）
    ranked = []
    for group in groups:
        if group is owner:
            # 参考对象自己和自己比没有意义（时间间隔过滤会把所有配对去掉）
            ranked.append({"candidate": "-".join(str(t) for t in group["track_ids"]),
                           "is_reference": True, "score": 1.0,
                           "supporting_pairs": len(anchor),
                           "supporting_anchor_frames": len(anchor),
                           "supporting_candidate_frames": len(anchor),
                           "views_used": 0, "pairs": 0, "evidence_kind": "reference",
                           "anchor_frames_used": len(anchor),
                           "candidate_frames_used": len(anchor)})
            continue
        pairs = _pairs(group["track_ids"], {t: frame_vectors.get(t, {})
                                            for t in group["track_ids"]})
        if not pairs:
            continue
        stats = compare(anchor, pairs, params["match_mode"])
        ranked.append({"candidate": "-".join(str(t) for t in group["track_ids"]),
                       "is_reference": group is owner, **stats})
    ranked.sort(key=lambda item: -item["score"])
    for index, item in enumerate(ranked):
        item["rank"] = index + 1
        nxt = ranked[index + 1] if index + 1 < len(ranked) else None
        item["margin_to_next"] = round(item["score"] - nxt["score"], 4) if nxt else None
        if item["is_reference"]:
            item["status"] = "reference"
        elif item["supporting_candidate_frames"] < 2:
            item["status"] = "insufficient_evidence"
        elif item["margin_to_next"] is not None and item["margin_to_next"] < 0.03:
            item["status"] = "ambiguous"
        else:
            item["status"] = "candidate"
    stages.append({"stage": "reid_match", "status": "ok",
                   "detail": {"mode": params["match_mode"], "candidates": len(ranked)}})

    # 5) 轨迹回溯：目标候选的覆盖与空档
    owner_tracks = [tr for tr in tracks if tr["track_id"] in set(owner["track_ids"])]
    gaps_result = coverage_gaps(owner_tracks)
    spans = gaps_result if isinstance(gaps_result, dict) else {"max_gap_seconds": None}
    stages.append({"stage": "trackback", "status": "ok",
                   "detail": {"tracks": len(owner["track_ids"]),
                              "max_gap_seconds": spans["max_gap_seconds"]}})

    # 6) 关联证据：只表达"关联是否成立"，不替用户下结论
    best = next((item for item in ranked if item["is_reference"]), None)
    association = "candidate" if best and best["supporting_candidate_frames"] >= 2 else "ambiguous"

    report = {
        "ok": True,
        "archive": archive,
        "stages": stages,
        "candidates": ranked[: params["top_k_candidates"]],
        "association_status": association,
        "grouping": {"objects": len(groups), "multi_track_groups": len(multi)},
        "trackback": spans,
        "evidence_pairs": len(pair_evidence),
    }
    return report


def attach_quality(report, keyframes, quality_status="usable"):
    """把关键画面质量接进报告，给出"关联 × 画质"的联合结论。"""
    report["keyframes"] = keyframes
    report["quality_status"] = quality_status if keyframes else "unusable"
    report["evidence"] = evidence_value(report["association_status"], report["quality_status"])
    return report
