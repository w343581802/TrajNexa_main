"""B5-Q2：同区域多摄像头分批分析 + 画质反馈（不重写检测/跟踪/ReID）。

三条硬约束：
  1. verify_all 可以突破镜头数上限，但仍受预算约束；预算不足保留未核验任务；
  2. 模糊镜头只降权，不删除；
  3. 同批次多路不合并身份：每路 Tracklet 独立保存，关联仍走审核流程。
"""
from __future__ import annotations

from .m6_view_quality import DEFAULT_PARAMS as QUALITY_DEFAULTS


def select_batch(members, batch_size=2):
    """从已排序的区域组里取前 N 台，各自保留独立时间窗口。"""
    chosen = [m for m in members if m.get("selected", True)][:batch_size]
    return {"batch_id": "batch:%s" % "-".join(str(m["camera"]) for m in chosen),
            "members": [{"camera": m["camera"], "window": m.get("window"),
                         "rank": m.get("rank"), "quality": m.get("quality")}
                        for m in chosen],
            "note": "同一批次内顺序分析；窗口各自独立，不共享时间假设"}


def quality_feedback(quality_by_camera, batch_members, params=None):
    """实测画质反馈：模糊则提升互补镜头优先级，不删除任何镜头。"""
    params = {**QUALITY_DEFAULTS, **(params or {})}
    if not params["enabled"] or params["mode"] in ("off", "observe_only"):
        return {"applied": False, "reason": "quality_disabled_or_observe_only",
                "boost": [], "demote": []}
    boost, demote, measured = [], [], []
    for member in batch_members:
        quality = quality_by_camera.get(member["camera"])
        if not quality or quality.get("basis") != "measured":
            continue
        measured.append({"camera": member["camera"], "band": quality["band"],
                         "score": quality.get("score")})
        if quality["band"] == "low":
            demote.append(member["camera"])
    if demote:
        boost = [m["camera"] for m in batch_members if m["camera"] not in demote]
    return {"applied": bool(boost), "boost": boost, "demote": demote,
            "measured": measured,
            "note": "只调整后续优先级；模糊镜头仍保留在候选与调度列表里"}


def batch_evidence(batch, windows_by_camera, quality_by_camera=None):
    """把各路结果并列保存，明确标注"未做跨镜头身份合并"。"""
    quality_by_camera = quality_by_camera or {}
    routes = []
    for member in batch["members"]:
        window = windows_by_camera.get(member["camera"]) or {}
        routes.append({"camera": member["camera"], "window": member["window"],
                       "tracks": [t.get("track_id") for t in window.get("tracks") or []],
                       "candidates": window.get("target_candidates") or [],
                       "quality": quality_by_camera.get(member["camera"])})
    return {"batch_id": batch["batch_id"], "routes": routes,
            "cross_camera_identity_merged": False,
            "note": "各路证据独立；身份关联必须经审核事件，不能由批次推断"}
