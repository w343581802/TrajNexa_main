"""M6-S4-4A：单镜头关键画面发现。

给定一条已知的目标轨迹（几十到上百帧），挑出**最值得人工查看**的少数几帧，
而不是让人从头翻到尾。第一轮不引入人脸/车牌识别模型，只用可解释的画面证据：

- `size`：目标框像素高度（相对该轨迹最大值）——人越大，脸/车牌像素越多；
- `sharpness`：裁剪图的拉普拉斯方差（相对该轨迹的排名）——运动模糊的直接代理；
- `edge`：目标框贴近/越出画面边界的程度——被切断的画面价值低；
- `crowding`：同帧其他目标框与它的最大 IoU——遮挡/重叠的直接代理；
- `orientation`：人工标注的朝向（可选），按任务加权（找人脸偏好正面，找车牌偏好背面/侧面）。

**重要边界**：人体框清晰 ≠ 人脸清晰，车辆框清晰 ≠ 车牌可读。
第一轮只验证"关键画面筛选"能力，人脸/车牌是否真正可辨由人工标注判定。
"""
from __future__ import annotations

DEFAULT_KEYFRAME_PARAMS = {
    "size_weight": 0.45,
    "sharpness_weight": 0.30,
    "crowding_weight": 0.25,
    "edge_penalty": 0.60,
    "orientation_bonus": 0.15,
    "min_gap_seconds": 0.4,
    "preferred_views": ("front",),
    # 低于这个高度，"清晰"没有意义：30 px 高的人脸即便不糊也认不出是谁
    "min_height_px": 60.0,
    "below_min_penalty": 0.40,
}


def crop_box(bbox, frame_width=0, frame_height=0, pad_ratio=0.0):
    """给出（可带 padding 的）裁剪框，并夹在画面内。"""
    x1, y1, x2, y2 = [float(v) for v in bbox]
    pad_x = (x2 - x1) * pad_ratio
    pad_y = (y2 - y1) * pad_ratio
    x1, y1, x2, y2 = x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y
    if frame_width > 0:
        x1, x2 = max(0.0, x1), min(float(frame_width), x2)
    if frame_height > 0:
        y1, y2 = max(0.0, y1), min(float(frame_height), y2)
    return [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))]


def edge_ratio(bbox, frame_width, frame_height, margin=2):
    """框有多少比例被画面边界切掉（0 = 完整，1 = 完全在画外）。

    按**面积**而不是周长折算：宽 90 的框被切掉 30 px，丢的是三分之一的主体像素，
    按周长算只有 0.08，会明显低估"脸已经被切掉一块"的严重程度。
    """
    x1, y1, x2, y2 = [float(v) for v in bbox]
    area = max((x2 - x1) * (y2 - y1), 1.0)
    vis_x1, vis_y1 = max(x1, 0.0), max(y1, 0.0)
    vis_x2, vis_y2 = min(x2, float(frame_width)), min(y2, float(frame_height))
    visible = max(0.0, vis_x2 - vis_x1) * max(0.0, vis_y2 - vis_y1)
    return round(min(1.0, max(0.0, 1.0 - visible / area)), 6)


def crowding(bbox, others):
    """同帧其他目标框与它的最大 IoU，作为遮挡/重叠代理。"""
    best = 0.0
    for other in others:
        best = max(best, _iou(bbox, other))
    return best


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _ranks(values):
    """把一组数值映射到 0..1 的排名（相同值取平均名次）。"""
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        average = (position + end) / 2.0
        for index in range(position, end + 1):
            ranks[order[index]] = average / max(len(values) - 1, 1)
        position = end + 1
    return ranks


def score_frames(observations, sharpness_by_frame, others_by_frame=None, views_by_frame=None,
                 params=None):
    """给一条轨迹的每一帧打分，返回每帧的分项与总分（均在轨迹内归一化）。"""
    params = dict(DEFAULT_KEYFRAME_PARAMS, **(params or {}))
    others_by_frame = others_by_frame or {}
    views_by_frame = views_by_frame or {}
    heights = [max(float(o["bbox"][3]) - float(o["bbox"][1]), 1.0) for o in observations]
    max_height = max(heights) if heights else 1.0
    sharp = [float(sharpness_by_frame.get(str(o["frame_id"]), 0.0)) for o in observations]
    sharp_rank = _ranks(sharp)

    scored = []
    for index, obs in enumerate(observations):
        bbox = [float(v) for v in obs["bbox"]]
        size = heights[index] / max_height if max_height > 0 else 0.0
        edge = edge_ratio(bbox, obs.get("frame_width", 100000), obs.get("frame_height", 100000))
        crowd = crowding(bbox, others_by_frame.get(str(obs["frame_id"]), []))
        view = views_by_frame.get(str(obs["frame_id"]), "unknown")
        orientation = params["orientation_bonus"] if view in params["preferred_views"] else 0.0

        total = (params["size_weight"] * size
                 + params["sharpness_weight"] * sharp_rank[index]
                 + params["crowding_weight"] * (1.0 - crowd)
                 + orientation
                 - params["edge_penalty"] * edge)
        below_min = heights[index] < params["min_height_px"]
        if below_min:
            total *= params["below_min_penalty"]
        scored.append({
            "below_min_size": below_min,
            "frame_id": str(obs["frame_id"]),
            "timestamp_seconds": float(obs["timestamp_seconds"]),
            "bbox": bbox,
            "height_px": round(heights[index], 1),
            "size": round(size, 3),
            "sharpness": round(sharp_rank[index], 3),
            "edge": round(edge, 3),
            "crowding": round(crowd, 3),
            "view": view,
            "score": round(total, 4),
        })
    return scored


def select_keyframes(scored, k=6, min_gap_seconds=0.4, prefer_above_min_size=True):
    """贪心挑选：先按分数排序，再按时间间隔去重，避免挑出连续 5 帧几乎一样的画面。

    `prefer_above_min_size` 优先挑"框足够大"的帧：太小的高分帧对人脸/车牌没有价值，
    只有在确实凑不够 k 帧时才回退到它们。
    """
    ordered = sorted(scored, key=lambda s: -s["score"])
    if prefer_above_min_size:
        ordered = [s for s in ordered if not s.get("below_min_size")] + \
                  [s for s in ordered if s.get("below_min_size")]
    picked = []
    for item in ordered:
        if len(picked) >= k:
            break
        if any(abs(item["timestamp_seconds"] - p["timestamp_seconds"]) < min_gap_seconds
               for p in picked):
            continue
        picked.append(item)
    return picked
