"""S6-15D：把"窗口里有人"变成"可能是指定目标"的最小适配。

语义分层（design/47 §2 P0-A）：
  WindowObservation(tracks) → TargetCandidate(supports/conflicts/unknown)
  → ReviewDecision → ConfirmedAssociation

本模块**只产出 candidate**，永不返回 confirmed；没有外观后端时返回 empty + 明确原因。
"""
from __future__ import annotations

CANDIDATE_STATES = ("candidate", "weak_candidate", "rejected", "unknown")

# 工程默认值，不是校准过的阈值：只用来区分"一个有明显领先"与"一群人分数挤在一起"。
DEFAULT_MIN_SCORE = 0.60
DEFAULT_MIN_MARGIN = 0.08


def candidates_from_scores(scored, min_score=DEFAULT_MIN_SCORE, min_margin=DEFAULT_MIN_MARGIN):
    """把窗口内的外观得分变成候选，并用 top1-top2 间距区分强弱。

    外观分只能排序；"弱候选"（分数挤在一起）不触发复核暂停，继续查其他窗口。
    """
    ranked = [s for s in scored if s.get("retrieval_score") is not None]
    ranked.sort(key=lambda s: -s["retrieval_score"])
    if not ranked:
        return {"status": "no_scored_tracks", "target_candidates": [], "margin": None}
    top = ranked[0]
    margin = round(top["retrieval_score"] - ranked[1]["retrieval_score"], 3) if len(ranked) > 1 else None
    strong = (top["retrieval_score"] >= min_score
              and (margin is None or margin >= min_margin))
    candidate = {"track_id": top["track_id"],
                 "retrieval_score": top["retrieval_score"],
                 "margin_top1_top2": margin,
                 "state": "candidate" if strong else "weak_candidate",
                 "supports": ["appearance_similarity"],
                 "conflicts": [],
                 "unknown": ["spatiotemporal_unverified"]}
    return {"status": "ok", "target_candidates": [candidate], "margin": margin,
            "min_score": min_score, "min_margin": min_margin,
            "note": "candidate 仍需人工确认；weak_candidate 不应暂停搜索"}


def target_candidates(window_result, target_profile=None, embedding_fn=None,
                      min_retrieval_score=None):
    """对窗口 Tracklet 做候选筛选。

    embedding_fn: 可选，签名 (track, target_profile) -> float|None。
                  None 时说明当前没有可用的 ReID 后端，直接返回 empty。
    """
    tracks = window_result.get("tracks") or []
    if not tracks:
        return {"status": "no_tracks", "target_candidates": [], "other_tracks": []}
    if embedding_fn is None:
        return {"status": "candidate_backend_missing",
                "target_candidates": [], "other_tracks": [],
                "scored_tracks": [], "reason": "no_embedding_fn",
                "uncertainties": ["candidate_selection_unavailable"]}
    scored = []
    for track in tracks:
        try:
            score = embedding_fn(track, target_profile)
        except Exception as exc:  # 后端异常不能让调度误判"没有目标"
            return {"status": "candidate_backend_error", "target_candidates": [],
                    "other_tracks": [], "scored_tracks": [],
                    "reason": "embedding_error: %s" % exc}
        scored.append({"track_id": track["track_id"], "retrieval_score": score})
    if min_retrieval_score is None:
        return {"status": "ranking_only", "target_candidates": [], "other_tracks": [],
                "scored_tracks": sorted(scored, key=lambda s: (s["retrieval_score"] is None,
                                                               -(s["retrieval_score"] or 0.0))),
                "uncertainties": ["threshold_not_configured"]}
    candidates = [s for s in scored
                  if s["retrieval_score"] is not None and s["retrieval_score"] >= min_retrieval_score]
    return {"status": "ok",
            "target_candidates": [{"track_id": c["track_id"],
                                   "retrieval_score": c["retrieval_score"],
                                   "state": "candidate",
                                   "supports": [],
                                   "conflicts": [],
                                   "unknown": ["appearance_only", "spatiotemporal_unverified"]}
                                  for c in candidates],
            "other_tracks": [s for s in scored if s not in candidates],
            "scored_tracks": scored,
            "candidate_state": "candidate",
            "note": "candidate 不是确认；确认必须来自人工审核事件"}
