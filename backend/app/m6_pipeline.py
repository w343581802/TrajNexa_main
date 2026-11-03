"""M6-S5：指定目标离线研判流程（集成收口）。

流程：参考档案 → 归组 → 外观比对 → 目标候选 → 重复候选复核 → 参考轨迹覆盖检查
      → 关联证据 → 关键画面 → 报告（含可复现清单）

三条硬约束（来自阶段验收要求）：

1. **结果必须可复现**：采样策略、采样帧数、尺寸/置信度门槛、模型版本、录像标识、
   代码版本全部写进 `manifest`，同输入同配置两次运行结果必须一致；
2. **参考组只做锚点**：不参与候选排名与 margin 计算，也不能用"参考组自己存在"当作身份关联成功；
3. **重复候选显式复核**：不靠放宽自动并轨阈值，而是把"疑似参考目标的重复轨迹"单列，
   每条都带可核查依据（余弦、支撑帧、时间重叠、冲突证据）；证据不足仍留在独立候选里。
"""
from __future__ import annotations

from app.m6_evidence_label import evidence_value
from app.m6_multiview_match import compare
from app.m6_track_grouping import coverage_gaps, group_tracks

DEFAULT_PIPELINE_PARAMS = {
    "top_k_candidates": 5,
    "keyframe_count": 6,
    "match_mode": "per_frame",
    # 重复候选复核门槛：只看"像"，不自动并档
    "duplicate_cosine": 0.85,
    "duplicate_min_support": 3,
    "margin": 0.03,
    "weak_cosine": 0.60,
}


def build_manifest(*, sampling_strategy, max_samples, min_bbox_height, quality_gate,
                   model_version, source_video, pipeline_version):
    """把影响结果的每个因素写进清单，便于复现与审计。"""
    return {
        "sampling_strategy": sampling_strategy,
        "max_samples": max_samples,
        "min_bbox_height": min_bbox_height,
        "quality_gate": quality_gate,
        "model_version": model_version,
        "source_video": source_video,
        "pipeline_version": pipeline_version,
        "note": "固定采样只保证可复现，不证明归组结果正确；采样密度敏感性是已知限制",
    }


def _observations(tracks):
    return {tr["track_id"]: {str(o["frame_id"]): o for o in tr["observations"]} for tr in tracks}


def _pairs(track_ids, tracks_by_id, frame_vectors):
    """拼 [(obs, view, vec)]，**直接用源观测的时间戳**，不按帧号反推。"""
    pairs = []
    for tid in track_ids:
        obs_by_frame = {str(o["frame_id"]): o for o in tracks_by_id[tid]["observations"]}
        for frame_id, vector in frame_vectors.get(tid, {}).items():
            obs = obs_by_frame.get(str(frame_id))
            if obs is None:
                continue
            pairs.append((obs, "unknown", vector))
    return pairs


def _evidence_index(pair_evidence):
    index = {}
    for item in pair_evidence:
        index[tuple(sorted((item["track_a"], item["track_b"])))] = item
    return index


def _conflict_between(group_a, group_b, evidence_index):
    """两组轨迹之间是否存在"同帧冲突"证据（存在即说明不是同一个人）。"""
    hits = []
    for a in group_a:
        for b in group_b:
            item = evidence_index.get(tuple(sorted((a, b))))
            if item and item["verdict"] == "conflict_coexist":
                hits.append({"track_a": a, "track_b": b,
                             "conflict_frames": item["conflict_frames"],
                             "mean_iou": item["mean_iou"]})
    return hits


def run_pipeline(*, tracks, frame_vectors, reference_track, params=None):
    params = dict(DEFAULT_PIPELINE_PARAMS, **(params or {}))
    tracks_by_id = {tr["track_id"]: tr for tr in tracks}
    stages = []

    anchor_frames = sorted(frame_vectors.get(reference_track, {}))
    if not anchor_frames:
        return {"ok": False, "reason": "reference_has_no_frames",
                "reference_track": reference_track, "stages": stages}

    groups, pair_evidence = group_tracks(tracks, frame_vectors)
    evidence_index = _evidence_index(pair_evidence)
    multi = [g for g in groups if len(g["track_ids"]) > 1]
    anchor_group = next((g for g in groups if reference_track in g["track_ids"]), None)
    if anchor_group is None:
        return {"ok": False, "reason": "reference_not_grouped", "stages": stages}

    anchor_pairs = _pairs(anchor_group["track_ids"], tracks_by_id, frame_vectors)
    stages.append({"stage": "anchor_archive", "status": "ok",
                   "detail": {"frames": len(anchor_pairs),
                              "tracks": len(anchor_group["track_ids"])}})
    stages.append({"stage": "grouping", "status": "ok",
                   "detail": {"objects": len(groups), "multi_track_groups": len(multi),
                              "merged_relations": sum(len(g["evidence"]) for g in multi),
                              "group_conflicts": sum(1 for g in groups if g["group_conflict"])}})

    # —— 逐个候选对象比对；参考组只做锚点，不参与排名 ——
    compared = []
    for group in groups:
        if group is anchor_group:
            continue
        pairs = _pairs(group["track_ids"], tracks_by_id, frame_vectors)
        if not pairs:
            continue
        stats = compare(anchor_pairs, pairs, params["match_mode"])
        compared.append({"candidate": "-".join(str(t) for t in group["track_ids"]),
                         "track_ids": list(group["track_ids"]),
                         "score": stats["score"],
                         "supporting_pairs": stats["supporting_pairs"],
                         "supporting_anchor_frames": stats["supporting_anchor_frames"],
                         "supporting_candidate_frames": stats["supporting_candidate_frames"],
                         "pairs": stats["pairs"],
                         "conflicts": _conflict_between(group["track_ids"],
                                                        anchor_group["track_ids"], evidence_index)})

    # —— 重复候选复核：像但没冲突 → 疑似同一个人；有同帧冲突 → 证据排除 ——
    duplicates, independents, excluded = [], [], []
    for item in compared:
        looks_same = (item["score"] >= params["duplicate_cosine"]
                      and item["supporting_candidate_frames"] >= params["duplicate_min_support"])
        if item["conflicts"]:
            # 同一时间出现在不同位置：外观再像也不可能是指定目标，保留证据但排除身份
            item["review"] = "excluded_by_evidence"
            item["review_reason"] = ("与参考组存在 %d 个同帧冲突（不同位置同时出现），"
                                     "外观相似度 %.3f 不足以推翻时空证据"
                                     % (len(item["conflicts"]), item["score"]))
            excluded.append(item)
        elif looks_same:
            item["review"] = "suspected_duplicate_of_reference"
            item["review_reason"] = ("余弦 %.3f ≥ %.2f、支撑帧 %d ≥ %d，且与参考组无同帧冲突证据"
                                     % (item["score"], params["duplicate_cosine"],
                                        item["supporting_candidate_frames"],
                                        params["duplicate_min_support"]))
            duplicates.append(item)
        else:
            item["review"] = "independent_insufficient_duplicate_evidence"
            independents.append(item)

    independents.sort(key=lambda item: -item["score"])
    for index, item in enumerate(independents):
        item["rank"] = index + 1
        nxt = independents[index + 1] if index + 1 < len(independents) else None
        item["margin_to_next"] = round(item["score"] - nxt["score"], 4) if nxt else None
        if item["supporting_candidate_frames"] < 2 or item["score"] < params["weak_cosine"]:
            item["status"] = "insufficient_evidence"
        elif item["margin_to_next"] is not None and item["margin_to_next"] < params["margin"]:
            item["status"] = "ambiguous"
        else:
            item["status"] = "candidate"
    duplicates.sort(key=lambda item: -item["score"])
    excluded.sort(key=lambda item: -item["score"])
    for item in excluded:
        item["rank"] = None
        item["margin_to_next"] = None
        item["status"] = "excluded_by_evidence"
    stages.append({"stage": "reid_match", "status": "ok",
                   "detail": {"mode": params["match_mode"],
                              "independent": len(independents),
                              "suspected_duplicates": len(duplicates),
                              "excluded_by_evidence": len(excluded)}})
    stages.append({"stage": "candidate_duplicate_review", "status": "ok",
                   "detail": {"duplicates": len(duplicates),
                              "independent": len(independents),
                              "excluded_by_evidence": len(excluded),
                              "policy": "不自动并档；疑似重复与证据排除分别列出供人工复核"}})

    # —— 参考轨迹覆盖检查（注意：这不是"沿独立候选向前回溯"的完整回溯）——
    owner_tracks = [tr for tr in tracks if tr["track_id"] in set(anchor_group["track_ids"])]
    gaps = coverage_gaps(owner_tracks)
    coverage = gaps if isinstance(gaps, dict) else {"max_gap_seconds": None}
    stages.append({"stage": "reference_track_coverage_check", "status": "ok",
                   "detail": {"tracks": len(anchor_group["track_ids"]),
                              "max_gap_seconds": coverage.get("max_gap_seconds")}})

    supported = any(item["status"] == "candidate" for item in independents)
    return {
        "ok": True,
        "anchor": {"track": reference_track,
                   "group": list(anchor_group["track_ids"]),
                   "frames": len(anchor_pairs)},
        "stages": stages,
        "grouping": {"objects": len(groups), "multi_track_groups": len(multi)},
        "suspected_duplicates_of_reference": duplicates,
        "independent_candidates": independents[: params["top_k_candidates"]],
        "excluded_by_evidence": excluded,
        "association_status": "supported_by_independent_candidates" if supported else "unconfirmed",
        "reference_track_coverage": coverage,
        "evidence_pairs": len(pair_evidence),
    }


def attach_quality(report, keyframes, quality_status="unverified"):
    """关键帧存在 ≠ 画面可用：默认 unverified，只有人工判读之后才能标 usable。"""
    report["keyframes"] = keyframes
    report["quality_status"] = quality_status if keyframes else "unusable"
    report["evidence"] = evidence_value(report["association_status"], report["quality_status"])
    return report
