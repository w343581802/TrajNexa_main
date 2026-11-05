"""SA2 CV_ONLY：只用结构线与运动轨迹估计消失点与姿态，失败就明确降级。

不使用人工控制点，不调用 Qwen3-VL，不产生经纬度。
输出候选参数与可追溯证据（线段、内点、残差、稳定性），供 SA1 校验台账参数。
"""
from __future__ import annotations

import math

import numpy as np

FOCAL_MIN_PX = 300.0
FOCAL_MAX_PX = 4000.0
MIN_INLIERS = 6
ANGLE_TOL_DEG = 0.35


class CvUnavailable(ValueError):
    """几何不可估：调用方必须降级，不能强行求解。"""


def line_segments(gray, min_length=60.0):
    """返回线段列表 [(x1,y1,x2,y2), ...]；优先 LSD，缺失时退回 Hough。"""
    segments = []
    try:
        import cv2
        lsd = cv2.createLineSegmentDetector()
        detected = lsd.detect(gray)[0]
        for row in (detected if detected is not None else []):
            x1, y1, x2, y2 = (float(v) for v in row[0])
            if math.hypot(x2 - x1, y2 - y1) >= min_length:
                segments.append([x1, y1, x2, y2])
    except Exception:
        segments = []
    if not segments:
        import cv2
        edges = cv2.Canny(gray, 60, 160)
        raw = cv2.HoughLinesP(edges, 1, np.pi / 180, 70, minLineLength=int(min_length), maxLineGap=12)
        rows = raw.reshape(-1, 4) if raw is not None else []
        segments = [[float(a), float(b), float(c), float(d)] for a, b, c, d in rows]
    return segments


def to_line(segment):
    """线段 → (a,b,c, 中点x, 中点y, 单位方向x, 单位方向y)，a x + b y + c = 0。"""
    x1, y1, x2, y2 = segment
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy)
    if norm < 1e-6:
        return None
    a, b = dy / norm, -dx / norm
    return (a, b, -(a * x1 + b * y1), (x1 + x2) / 2.0, (y1 + y2) / 2.0, dx / norm, dy / norm)


def intersect(l1, l2):
    px = l1[1] * l2[2] - l1[2] * l2[1]
    py = l1[2] * l2[0] - l1[0] * l2[2]
    pz = l1[0] * l2[1] - l1[1] * l2[0]
    if abs(pz) < 1e-12:
        return None
    return (px / pz, py / pz)


def residual_deg(line, vp):
    """消失点与线段方向的角度残差：线段方向 vs 中点指向消失点的方向。"""
    mx, my, dx, dy = line[3], line[4], line[5], line[6]
    vx, vy = vp[0] - mx, vp[1] - my
    norm = math.hypot(vx, vy)
    if norm < 1e-6:
        return 0.0
    dot = abs(dx * vx / norm + dy * vy / norm)
    dot = min(1.0, max(0.0, dot))
    return math.degrees(math.acos(dot))


def _collect(lines, vp, tol_deg):
    return [i for i, line in enumerate(lines) if residual_deg(line, vp) <= tol_deg]


def _fit(lines, idx):
    A = np.array([[lines[i][0], lines[i][1], lines[i][2]] for i in idx])
    _, _, vt = np.linalg.svd(A)
    if abs(vt[-1][2]) < 1e-9:
        return None
    return (float(vt[-1][0] / vt[-1][2]), float(vt[-1][1] / vt[-1][2]))


def estimate_vanishing_point(lines, *, width, height, max_offset_factor=3.0,
                             tol_deg=ANGLE_TOL_DEG, min_inliers=MIN_INLIERS, seed=7):
    if len(lines) < min_inliers:
        raise CvUnavailable("insufficient_lines")
    rng = np.random.default_rng(seed)
    best = None
    limit_x, limit_y = width * max_offset_factor, height * max_offset_factor
    for _ in range(1500):
        i, j = rng.integers(0, len(lines), 2)
        if i == j:
            continue
        vp = intersect(lines[i], lines[j])
        if vp is None or not (-limit_x < vp[0] < limit_x) or not (-limit_y < vp[1] < limit_y):
            continue
        inliers = _collect(lines, vp, tol_deg)
        if best is None or len(inliers) > len(best[1]):
            best = (vp, inliers)
    if best is None or len(best[1]) < min_inliers:
        raise CvUnavailable("vanishing_point_unstable")
    vp = _fit(lines, best[1]) or best[0]
    inliers = _collect(lines, vp, tol_deg)
    if len(inliers) < min_inliers:
        raise CvUnavailable("vanishing_point_unstable")
    _require_conditioning(lines, inliers, width)
    return {"vp": [round(vp[0], 1), round(vp[1], 1)], "inliers": inliers,
            "max_residual_deg": round(max(residual_deg(lines[i], vp) for i in inliers), 3),
            "line_count": len(lines)}


def _require_conditioning(lines, inliers, width):
    """近邻平行线族不能定出有限消失点：方向必须足够分散、中点必须铺开。"""
    angles = sorted(math.degrees(math.atan2(lines[i][6], lines[i][5])) % 180.0 for i in inliers)
    spread = angles[-1] - angles[0]
    mids = [lines[i][3] for i in inliers]
    if spread < 4.0 or (max(mids) - min(mids)) < 0.2 * width:
        raise CvUnavailable("vanishing_point_ill_conditioned")


def stability_check(lines, *, width, height, split_seed=11):
    """线段随机分两半各估一次；两次 VP 偏差过大即判不稳定。"""
    rng = np.random.default_rng(split_seed)
    order = rng.permutation(len(lines))
    half = len(order) // 2
    try:
        first = estimate_vanishing_point([lines[i] for i in order[:half]], width=width, height=height)
        second = estimate_vanishing_point([lines[i] for i in order[half:]], width=width, height=height)
    except CvUnavailable as exc:
        raise CvUnavailable(f"split_half_failed:{exc}") from None
    gap = math.hypot(first["vp"][0] - second["vp"][0], first["vp"][1] - second["vp"][1])
    allowed = 0.15 * width
    if gap > allowed:
        raise CvUnavailable("vanishing_point_unstable_across_halves")
    return {"gap_px": round(gap, 1), "allowed_px": round(allowed, 1)}


def implied_focal(vp_y, cy, pitch_deg):
    """已知俯角时由地平线反推焦距；仅作诊断，不单独作为估计结果。"""
    if pitch_deg is None:
        raise CvUnavailable("pitch_missing")
    offset = cy - vp_y
    if offset <= 0:
        raise CvUnavailable("horizon_below_center_for_downward_tilt")
    return offset / math.tan(math.radians(pitch_deg))


def plausible_focal(focal_px):
    if focal_px is None:
        raise CvUnavailable("focal_missing")
    if not math.isfinite(focal_px) or focal_px < FOCAL_MIN_PX or focal_px > FOCAL_MAX_PX:
        raise CvUnavailable("focal_out_of_range")
    return round(float(focal_px), 1)


def pitch_from_vp(vp_y, cy, focal_px):
    if focal_px <= 0:
        raise CvUnavailable("focal_invalid")
    return round(math.degrees(math.atan((cy - vp_y) / focal_px)), 2)


def tracks_to_lines(tracks):
    """轨迹（地面接地点序列）→ 直线；每条轨迹至少 4 个点，按最小二乘拟合。"""
    lines = []
    for track in tracks:
        pts = np.array(track, dtype=float)
        if len(pts) < 4:
            continue
        mean = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - mean)
        direction = vt[0]
        if np.linalg.norm(direction) < 1e-9:
            continue
        a, b = -direction[1], direction[0]
        norm = math.hypot(a, b)
        a, b = a / norm, b / norm
        lines.append((a, b, -(a * mean[0] + b * mean[1]), mean[0], mean[1],
                      direction[0], direction[1]))
    return lines


def analyze(gray, *, width, height, pitch_deg=None, focal_px=None, min_length=60.0, tracks=None):
    """单帧分析：给出候选与降级原因，绝不返回未经检验的外推参数。"""
    report = {"frame": {"width": width, "height": height}, "status": "failed",
              "method": "structural_lines_vanishing_point", "candidates": []}
    segments = line_segments(gray, min_length=min_length)
    lines = [ln for ln in (to_line(s) for s in segments) if ln]
    report["segments"] = len(lines)
    if tracks:
        lines = lines + tracks_to_lines(tracks)
        report["track_lines"] = len(lines) - report["segments"]
    try:
        vp = estimate_vanishing_point(lines, width=width, height=height)
        candidate = {"vanishing_point_px": vp["vp"], "inliers": len(vp["inliers"]),
                     "max_residual_deg": vp["max_residual_deg"],
                     "stability": stability_check(lines, width=width, height=height),
                     "offscreen": not (0 <= vp["vp"][0] <= width and 0 <= vp["vp"][1] <= height)}
        if pitch_deg is not None:
            try:
                candidate["implied_focal_px"] = plausible_focal(
                    implied_focal(vp["vp"][1], height / 2.0, pitch_deg))
            except CvUnavailable as exc:
                candidate["implied_focal_rejected"] = str(exc)
        if focal_px:
            candidate["pitch_from_vp_deg"] = pitch_from_vp(vp["vp"][1], height / 2.0, focal_px)
        report["candidates"].append(candidate)
        report["status"] = "estimated"
    except CvUnavailable as exc:
        report["degrade_reason"] = str(exc)
    return report


def validate_against_pose(candidate, *, reported_pitch_deg=None, reported_focal_px=None,
                          cy=None, frame_height=None,
                          pitch_tolerance_deg=3.0, focal_tolerance=0.15):
    """把 CV 候选与台账参数对照；只给判定，不改台账。"""
    result = {"verdict": "unverifiable", "reasons": []}
    if "pitch_from_vp_deg" in candidate and reported_pitch_deg is not None:
        delta = candidate["pitch_from_vp_deg"] - reported_pitch_deg
        result["delta_pitch_deg"] = round(delta, 2)
        if abs(delta) > pitch_tolerance_deg:
            result["reasons"].append("pitch_conflict")
    if "implied_focal_px" in candidate and reported_focal_px:
        ratio = candidate["implied_focal_px"] / reported_focal_px
        result["focal_ratio"] = round(ratio, 3)
        if abs(ratio - 1.0) > focal_tolerance:
            result["reasons"].append("focal_conflict")
    if reported_focal_px and reported_pitch_deg is not None and cy is not None:
        horizon = cy - reported_focal_px * math.tan(math.radians(reported_pitch_deg))
        result["expected_horizon_y"] = round(horizon, 1)
        gap = abs(candidate["vanishing_point_px"][1] - horizon)
        result["horizon_gap_px"] = round(gap, 1)
        if frame_height is not None and gap > max(8.0, 0.01 * frame_height):
            result["reasons"].append("vanishing_point_not_on_horizon")
    if result["reasons"]:
        result["verdict"] = "conflict"
    elif "delta_pitch_deg" in result or "focal_ratio" in result:
        result["verdict"] = "consistent"
    return result


def motion_tracks(frames, *, min_area=600.0, max_age=3, iou_threshold=0.2):
    """纯传统视觉：背景差分 + IoU 关联，返回每条轨迹的框底中心点序列。

    frames: 灰度帧序列（同一视角、相机静止）。不依赖任何模型或人工标注。
    """
    import cv2

    subtractor = cv2.createBackgroundSubtractorMOG2(history=120, varThreshold=36, detectShadows=False)
    tracks, active = [], {}
    next_id = 0
    for frame in frames:
        mask = subtractor.apply(frame)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            if cv2.contourArea(contour) < min_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if h < 12 or w < 8:
                continue
            boxes.append((float(x), float(y), float(x + w), float(y + h)))
        assigned = {}
        for track_id, state in list(active.items()):
            best, best_iou = None, iou_threshold
            for index, box in enumerate(boxes):
                if index in assigned:
                    continue
                overlap = _iou(box, state["box"])
                if overlap > best_iou:
                    best, best_iou = index, overlap
            if best is not None:
                assigned[best] = track_id
                state["box"] = boxes[best]
                state["age"] = 0
                tracks[track_id].append(((boxes[best][0] + boxes[best][2]) / 2.0, boxes[best][3], boxes[best][3] - boxes[best][1]))
            else:
                state["age"] += 1
                if state["age"] > max_age:
                    active.pop(track_id)
        for index, box in enumerate(boxes):
            if index in assigned:
                continue
            track_id = next_id
            next_id += 1
            tracks.append([((box[0] + box[2]) / 2.0, box[3], box[3] - box[1])])
            active[track_id] = {"box": box, "age": 0}
    return [{"points": [[round(x, 1), round(y, 1)] for x, y, _ in track],
             "heights": [round(h, 1) for _, _, h in track]}
            for track in tracks if len(track) >= 4]


def depth_motion_tracks(tracks, *, min_points=12, min_height_ratio=1.4):
    """只保留“明显沿纵深运动”的轨迹：框高单调变化且幅度足够。"""
    kept = []
    for track in tracks:
        points, heights = track["points"], track["heights"]
        if len(points) < min_points or not heights:
            continue
        first, last = heights[:max(3, len(heights) // 4)], heights[-max(3, len(heights) // 4):]
        start = sum(first) / len(first)
        end = sum(last) / len(last)
        if start <= 0 or end <= 0:
            continue
        ratio = max(start, end) / min(start, end)
        if ratio < min_height_ratio:
            continue
        steps = [b - a for a, b in zip(heights, heights[1:])]
        monotone = sum(1 for d in steps if d > 0) / max(1, len(steps))
        if not (monotone > 0.75 or monotone < 0.25):
            continue
        kept.append({"points": points, "height_ratio": round(ratio, 2)})
    return kept


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0
