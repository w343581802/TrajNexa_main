"""M6-S4-3A：同点位多摄像头择优规则（离线，不需要真实多通道录像）。

输入是**已知的摄像头档案**，输出是"针对当前任务，先看哪几路、为什么"。
信息项对齐设计文档：位置、朝向、预期像素尺寸、道路与出入口、录像时间范围、用途、已有研判结果。

两条原则：

1. **按任务择优，不是"先找到就停"**。找人是覆盖优先；找人脸/车牌是"预期像素尺寸 + 用途"优先。
   如果 A 已覆盖目标但只有 40 px、B 能给出 200 px，那么 B 必须被选中——
   否则"轨迹任务成功、清晰证据任务失败"。
2. **跳过必须给理由**。每一路被跳过的摄像头都带 `why`，便于人工复核"有没有漏掉更好的画面"。
"""
from __future__ import annotations

DEFAULT_CAMERA_PARAMS = {
    "coverage_weight": 0.35,
    "angle_weight": 0.25,
    "size_weight": 0.40,
    "purpose_bonus": 0.30,
    "reanalyzed_penalty": 0.50,
    "min_expected_px": {"face": 80.0, "plate": 60.0, "track": 24.0},
    "task": "track",
}


def _norm(value, low, high):
    if high <= low:
        return 1.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def expected_height_px(camera, distance_m):
    """按小孔成像估算目标在画面里的高度（像素）。"""
    focal = camera.get("focal_px")
    if not focal:
        focal = camera.get("sensor_height_px", 1080) * camera.get("focal_mm", 6.0) / 4.8
    person_height = camera.get("person_height_m", 1.7)
    return focal * person_height / max(distance_m, 0.1)


def angle_score(camera, target_heading_deg):
    """目标朝向与镜头光轴的夹角：正面/背面（0°/180°）比侧面更有辨识价值。"""
    delta = abs((camera["heading_deg"] - target_heading_deg + 180) % 360 - 180)
    return 1.0 - _norm(delta, 0.0, 90.0)


def coverage_score(camera, distance_m, route_hit=True):
    """是否落在有效距离内、且目标确实可能经过。"""
    if not route_hit:
        return 0.0
    return 1.0 - _norm(distance_m, camera.get("near_m", 5.0), camera.get("far_m", 40.0))


def evaluate(camera, query, params=None):
    """给一路摄像头在当前任务下的打分与逐项理由。"""
    params = dict(DEFAULT_CAMERA_PARAMS, **(params or {}))
    task = query.get("task", params["task"])
    distance = float(query.get("distance_m", 15.0))
    heading = float(query.get("target_heading_deg", camera.get("heading_deg", 0.0)))

    px = expected_height_px(camera, distance)
    coverage = coverage_score(camera, distance, query.get("route_hit", True))
    angle = angle_score(camera, heading)
    size = _norm(px, 0.0, max(params["min_expected_px"].get(task, 60.0) * 2.5, 1.0))
    purpose = params["purpose_bonus"] if camera.get("purpose") == task else 0.0
    penalty = params["reanalyzed_penalty"] if camera.get("already_analyzed") else 0.0

    score = (params["coverage_weight"] * coverage
             + params["angle_weight"] * angle
             + params["size_weight"] * size
             + purpose
             - penalty)
    reasons = []
    if coverage <= 0.0:
        reasons.append("不在覆盖范围内")
    if px < params["min_expected_px"].get(task, 60.0):
        reasons.append("预期像素 %dpx 低于 %s 任务下限 %dpx" % (
            px, task, params["min_expected_px"].get(task, 60.0)))
    if purpose:
        reasons.append("用途匹配（%s）" % task)
    if penalty:
        reasons.append("已有同结论研判，重复分析价值低")
    return {"camera_id": camera["id"], "score": round(score, 4),
            "expected_height_px": round(px, 1), "coverage": round(coverage, 3),
            "angle": round(angle, 3), "size": round(size, 3),
            "purpose_match": bool(purpose), "reasons": reasons}


def select_cameras(cameras, query, k=2, params=None):
    """按任务挑前 k 路，并给出每路被跳过/被选中的理由。

    选中的可以是"覆盖一般但更清晰"的那一路——这正是"不要第一个找到就停"的要求。
    """
    params = dict(DEFAULT_CAMERA_PARAMS, **(params or {}))
    task = query.get("task", params["task"])
    evaluated = [evaluate(c, query, params) for c in cameras]
    evaluated.sort(key=lambda item: -item["score"])
    picked = []
    skipped = []
    for item in evaluated:
        blocked = [r for r in item["reasons"] if "低于" in r or "不在覆盖范围" in r]
        if item["score"] <= 0 or blocked:
            skipped.append(dict(item, why="；".join(blocked) or "综合得分不足"))
        elif len(picked) < k:
            picked.append(item)
        else:
            skipped.append(dict(item, why="已选满 %d 路后按得分排序" % k))
    return {"task": task, "selected": picked, "skipped": skipped}
