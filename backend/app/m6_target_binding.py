"""TB4：目标绑定与单镜头空间轨迹（实验估计，不写正式观测）。"""
from __future__ import annotations

from app import m6_track_spatial

BIND_IOU = 0.3
TRACK_IOU = 0.1
MAX_JUMP_PX = 180.0
MAX_GAP_S = 2.0
AMBIGUITY_MARGIN = 0.1


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def center_distance(a, b):
    ax, ay = _center(a)
    bx, by = _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _frame_t(frame):
    return float(frame.get("t", frame.get("t_abs", 0.0)))


def _nearest_frame(frames, t):
    if not frames:
        return None
    return min(frames, key=lambda f: abs(_frame_t(f) - t))


def bind_tracklets(anchor, tracklets, *, bind_iou=BIND_IOU, ambiguity_margin=AMBIGUITY_MARGIN):
    """anchor={"t","box"}；返回绑定结果与候选评分，不做“最像”猜测。"""
    anchor_t = float(anchor["t"])
    anchor_box = [float(v) for v in anchor["box"]]
    scored = []
    for tracklet in tracklets:
        frame = _nearest_frame(tracklet.get("frames") or [], anchor_t)
        if frame is None or abs(_frame_t(frame) - anchor_t) > 1.0:
            continue
        score = iou(anchor_box, [float(v) for v in frame["box"]])
        scored.append({"track_id": tracklet.get("track_id"), "iou": round(score, 4),
                       "center_distance": round(center_distance(anchor_box, frame["box"]), 2),
                       "t": _frame_t(frame), "score": score})
    passed = [s for s in scored if s["score"] >= bind_iou]
    passed.sort(key=lambda s: -s["score"])
    if not passed:
        return {"identity_status": "candidate_unverified", "bound": None, "scored": scored,
                "reason": "no_tracklet_matches_anchor"}
    best = passed[0]
    ambiguous = len(passed) > 1 and (best["score"] - passed[1]["score"]) < ambiguity_margin
    bound_id = best["track_id"]
    return {
        "identity_status": "ambiguous" if ambiguous else "candidate_unverified",
        "bound": bound_id,
        "bind_evidence": {"track_id": bound_id, "iou": best["iou"],
                          "center_distance_px": best["center_distance"], "t": best["t"],
                          "runner_up": passed[1]["track_id"] if len(passed) > 1 else None,
                          "ambiguous": ambiguous},
        "scored": scored,
        "reason": "identity_ambiguous" if ambiguous else "bound_by_iou_at_anchor",
    }


def segment_frames(frames, *, max_gap_s=MAX_GAP_S, track_iou=TRACK_IOU, max_jump_px=MAX_JUMP_PX):
    """按时间间隔与位移切断轨迹；返回 [(frames, break_reason)]，不补帧。"""
    ordered = sorted((dict(f) for f in frames), key=_frame_t)
    segments = []
    current = []
    prev = None
    for frame in ordered:
        if prev is not None:
            gap = _frame_t(frame) - _frame_t(prev)
            jump = center_distance(prev["box"], frame["box"])
            overlap = iou(prev["box"], frame["box"])
            if gap > max_gap_s:
                segments.append((current, "gap_exceeded"))
                current = []
            elif overlap < track_iou and jump > max_jump_px:
                segments.append((current, "position_jump"))
                current = []
        current.append(frame)
        prev = frame
    if current:
        segments.append((current, "end_of_track"))
    return segments


def build_track(anchor, tracklets, camera, *, kind="person", evidence=None, playback=None):
    """绑定 → 分段 → 逐帧投影；返回实验估计轨迹，persisted 恒为 False。"""
    playback = dict(playback or {})
    start_of_day = playback.get("start_of_day_seconds")
    binding = bind_tracklets(anchor, tracklets)
    if not binding["bound"]:
        return {"identity_status": binding["identity_status"], "persisted": False,
                "layer": "experimental_estimate", "segments": [], "bind_evidence": None,
                "reason": binding["reason"], "scored": binding["scored"],
                "summary": {"segments": 0, "projected": 0, "degraded": 0}}
    tracklet = next(t for t in tracklets if t.get("track_id") == binding["bound"])
    segments_out = []
    projected = 0
    degraded = 0
    for frames, break_reason in segment_frames(tracklet.get("frames") or []):
        points = []
        for frame in frames:
            sample = {"t": _frame_t(frame), "box": frame["box"]}
            frame_t = _frame_t(frame)
            point_evidence = {**(evidence or {}),
                              "track_id": tracklet.get("track_id"),
                              "frame": frame.get("frame"),
                              "clip_id": playback.get("clip_id"),
                              "time_basis": playback.get("time_basis"),
                              "time_quality": playback.get("time_quality"),
                              "day_seconds": frame_t}
            if start_of_day is not None:
                point_evidence["offset_seconds"] = round(frame_t - float(start_of_day), 2)
            report = m6_track_spatial.project_track(
                camera, [sample], kind=kind, evidence=point_evidence)
            point = dict(report["points"][0])
            point["frame"] = frame.get("frame")
            if frames and frame is not frames[0]:
                pass
            points.append(point)
            if point.get("spatial_level") == "L2-candidate":
                projected += 1
            else:
                degraded += 1
        segments_out.append({"track_id": tracklet.get("track_id"),
                             "continuity_status": "single_segment" if len(segments_out) == 0 else "after_break",
                             "break_reason": break_reason, "points": points})
    return {"identity_status": binding["identity_status"], "persisted": False,
            "layer": "experimental_estimate", "quality": "experimental_estimate",
            "bind_evidence": binding["bind_evidence"], "scored": binding["scored"],
            "playback": playback,
            "segments": segments_out,
            "note": "候选身份绑定 + 实验估计投影；未经身份确认与标定审核，不写正式观测",
            "summary": {"segments": len(segments_out), "projected": projected, "degraded": degraded}}
