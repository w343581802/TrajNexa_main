"""B8-S §6.2 指标：把「实验投影」的可用性、误差区域与真值误差算成可复核的数字。

设计边界（与计划文档 §6.1 一致）：
  - 没有独立真值时，只报**覆盖率类**指标，`truth` 段必须是 `None`，不允许用算法自身
    推算出的点冒充真值；
  - `experimental_estimate` 永远不计入「正式 L2 发布成功率」，`mis_published` 只是统计
    「有没有把实验估计错标成正式等级」，正常应为 0；
  - 误差区域覆盖率只用**投影时声明**的 `uncertainty_radius_m` 判定，事后不放大；
  - 数值门槛在本轮不冻结（文档 §6.2 明确「验证数据准备后冻结」），报告里写 `null`。

输入是 `/cases/{case}/tracking-runs/{run}/track-segments` 的响应体（`segments[].points[]`），
输出是一份可直接存档的 JSON 报告。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

REPORT_KIND = "b8s_projection_metrics_v1"

#: 只有这些来源能当真值；算法自己的输出一律拒绝（§6.1「不能用算法自身推算点当真值」）。
TRUSTED_SOURCES = (
    "surveyed",
    "gnss_walk",
    "map_control_point",
    "manual_control_point",
)
REFUSED_SOURCES = ("ray_projection", "auto_projection", "self_estimate", "derived")

#: 近 / 中 / 远分层，单位米（§6.2「按照近/中/远及场景类型分组」）。
RANGE_BUCKETS = ((0.0, 15.0, "near"), (15.0, 40.0, "mid"), (40.0, float("inf"), "far"))

#: 正式等级；实验估计即使 `spatial_level` 写成 L2-candidate 也不算。
FORMAL_LEVELS = ("L2", "L3")


class TruthSetError(ValueError):
    """真值集不合格时抛出，`str(exc)` 是稳定错误码。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def lonlat_of(point: dict) -> tuple[float, float] | None:
    """从 `ground_point` 取经纬度：主系统同时给 x=lon / y=lat 与 lon / lat。"""
    ground = point.get("ground_point") or {}
    lon = _finite(ground.get("lon"))
    lat = _finite(ground.get("lat"))
    if lon is None:
        lon = _finite(ground.get("x"))
    if lat is None:
        lat = _finite(ground.get("y"))
    if lon is None or lat is None:
        return None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return lon, lat


def classify(point: dict) -> str:
    """把一个投影点归类：`experimental` / `camera_only` / `degraded`。"""
    level = str(point.get("spatial_level") or "")
    if lonlat_of(point) is None:
        return "camera_only" if level == "L0" else "degraded"
    if level in FORMAL_LEVELS:
        # 正式等级只能来自已审核标定，轨迹接口不会产出；出现即视为误标。
        return "formal"
    return "experimental"


def bucket_of(range_m) -> str:
    value = _finite(range_m)
    if value is None:
        return "unknown"
    for low, high, name in RANGE_BUCKETS:
        if low <= value < high:
            return name
    return "far"


def percentile(values, share: float):
    """线性插值分位数；空列表返回 None（不返回 0，避免把「没有样本」写成「误差为 0」）。"""
    data = sorted(v for v in (_finite(x) for x in values) if v is not None)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    share = min(max(float(share), 0.0), 1.0)
    position = (len(data) - 1) * share
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return data[int(position)]
    weight = position - low
    return data[low] * (1.0 - weight) + data[high] * weight


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    radius = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def iter_points(envelope: dict):
    """展开 `track-segments` 响应里的所有点，带上 track / segment 归属。"""
    for track in envelope.get("tracks") or []:
        for segment in track.get("segments") or []:
            for point in segment.get("points") or []:
                yield {
                    "track_id": track.get("track_id"),
                    "camera_label": track.get("camera_label"),
                    "segment_track": segment.get("track_id"),
                    "break_reason": segment.get("break_reason"),
                    "point": point,
                }


def summarize(points) -> dict:
    """覆盖率类指标：不看真值也能算，用于「这套数据够不够画实验轨迹」。"""
    rows = list(points)
    counts = {"total": len(rows), "experimental": 0, "formal": 0, "camera_only": 0, "degraded": 0}
    reasons: dict[str, int] = {}
    radii = []
    ranges = []
    buckets = {name: {"total": 0, "experimental": 0, "degraded": 0} for _, _, name in RANGE_BUCKETS}
    buckets["unknown"] = {"total": 0, "experimental": 0, "degraded": 0}
    for row in rows:
        point = row["point"]
        kind = classify(point)
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "degraded":
            reason = str(point.get("degrade_reason") or "unspecified")
            reasons[reason] = reasons.get(reason, 0) + 1
        radius = _finite(point.get("uncertainty_radius_m"))
        if radius is not None:
            radii.append(radius)
        range_m = _finite(point.get("range_m"))
        if range_m is not None:
            ranges.append(range_m)
        bucket = buckets[bucket_of(range_m)]
        bucket["total"] += 1
        if kind == "experimental":
            bucket["experimental"] += 1
        elif kind in ("degraded", "camera_only"):
            bucket["degraded"] += 1
    total = counts["total"]

    def rate(value: int):
        return None if not total else round(value / total, 4)

    return {
        "points": counts,
        "rates": {
            "experimental": rate(counts["experimental"]),
            "camera_only": rate(counts["camera_only"]),
            "degraded": rate(counts["degraded"]),
        },
        "degrade_reasons": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        "uncertainty_radius_m": {
            "count": len(radii),
            "median": percentile(radii, 0.5),
            "p90": percentile(radii, 0.9),
            "max": max(radii) if radii else None,
        },
        "range_m": {
            "count": len(ranges),
            "median": percentile(ranges, 0.5),
            "p90": percentile(ranges, 0.9),
            "max": max(ranges) if ranges else None,
        },
        "buckets": buckets,
    }


def load_truth(payload: dict | None, *, camera_uuid: str | None = None) -> dict | None:
    """校验并规整真值集；不合格直接抛错，不静默降级。"""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise TruthSetError("truth_not_object")
    points = payload.get("points")
    if not isinstance(points, list) or not points:
        raise TruthSetError("truth_points_missing")
    seen = set()
    cleaned = []
    for index, row in enumerate(points):
        if not isinstance(row, dict):
            raise TruthSetError("truth_point_not_object")
        point_id = str(row.get("id") or "").strip()
        if not point_id:
            raise TruthSetError(f"truth_point_missing_id:{index}")
        if point_id in seen:
            raise TruthSetError(f"truth_point_duplicate_id:{point_id}")
        seen.add(point_id)
        source = str(row.get("source") or "").strip()
        if source in REFUSED_SOURCES:
            raise TruthSetError(f"truth_source_is_algorithm_output:{point_id}")
        if source not in TRUSTED_SOURCES:
            raise TruthSetError(f"truth_source_not_allowed:{point_id}")
        lon = _finite(row.get("lon"))
        lat = _finite(row.get("lat"))
        if lon is None or lat is None:
            raise TruthSetError(f"truth_point_missing_coordinates:{point_id}")
        target_camera = row.get("camera_uuid")
        cleaned.append(
            {
                "id": point_id,
                "lon": lon,
                "lat": lat,
                "at": row.get("at") or row.get("t"),
                "source": source,
                "range_class": row.get("range_class"),
                "camera_uuid": target_camera,
                "evidence": row.get("evidence"),
            }
        )
    dataset_camera = payload.get("camera_uuid") or camera_uuid
    return {
        "dataset_id": payload.get("dataset_id") or "unnamed",
        "camera_uuid": dataset_camera,
        "collected_at": payload.get("collected_at"),
        "points": cleaned,
    }


def _clock_seconds(text):
    """`2026-10-23T15:00:08` / `15:00:08` → 秒；解析不了返回 None。"""
    if not isinstance(text, str) or not text:
        return None
    clock = text.split("T")[-1].strip()
    parts = clock.split(":")
    if len(parts) < 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2]) if len(parts) > 2 else 0.0
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _point_clock(text):
    """`track-segments` 里 `t` 是**片段内**秒数（可能是 Decimal），不是当天时刻。

    只有字符串形式的时刻才算绝对时钟；`evidence.day_seconds` 在当前实现里同样是
    片段内偏移，所以不用它——宁可标 `time_checked=false`，也不假装校验过时刻。
    """
    if isinstance(text, dict):
        text = text.get("t")
    if not isinstance(text, str):
        return None
    return _clock_seconds(text)


def match_truth(points, truth: dict, *, time_tolerance_s: float = 5.0, max_distance_m: float = 100.0) -> dict:
    """把每个真值点配最近的投影点；有时刻的按时刻容差先筛，避免跨时段乱配。"""
    rows = [row for row in points if classify(row["point"]) == "experimental"]
    errors = []
    unmatched = []
    for item in truth["points"]:
        truth_ll = (item["lon"], item["lat"])
        truth_clock = _clock_seconds(item.get("at"))
        best = None
        nearest_any = None
        for row in rows:
            lonlat = lonlat_of(row["point"])
            if lonlat is None:
                continue
            distance = haversine_m(truth_ll, lonlat)
            if nearest_any is None or distance < nearest_any:
                nearest_any = distance
            if truth_clock is not None:
                point_clock = _point_clock(row["point"])
                if point_clock is not None and abs(point_clock - truth_clock) > time_tolerance_s:
                    continue
            if best is None or distance < best[0]:
                best = (distance, row)
        if best is None:
            reason = "no_time_match" if truth_clock is not None else "no_projected_point"
            unmatched.append(
                {
                    "id": item["id"],
                    "reason": reason,
                    "nearest_m": None if nearest_any is None else round(nearest_any, 3),
                }
            )
            continue
        if best[0] > max_distance_m:
            unmatched.append({"id": item["id"], "reason": "too_far", "nearest_m": round(best[0], 3)})
            continue
        distance, row = best
        point = row["point"]
        radius = _finite(point.get("uncertainty_radius_m"))
        errors.append(
            {
                "id": item["id"],
                "error_m": round(distance, 3),
                "uncertainty_radius_m": radius,
                "within_declared_region": None if radius is None else bool(distance <= radius),
                "range_class": item.get("range_class") or bucket_of(point.get("range_m")),
                "time_checked": truth_clock is not None and _point_clock(point) is not None,
                "track_id": row["track_id"],
                "point_t": point.get("t"),
            }
        )
    return {"errors": errors, "unmatched": unmatched}


def error_summary(matches: dict) -> dict:
    errors = matches["errors"]
    values = [item["error_m"] for item in errors]
    inside = [item["within_declared_region"] for item in errors if item["within_declared_region"] is not None]
    by_class: dict[str, list[float]] = {}
    for item in errors:
        by_class.setdefault(str(item["range_class"] or "unknown"), []).append(item["error_m"])
    return {
        "matched": len(errors),
        "unmatched": len(matches["unmatched"]),
        "unmatched_detail": matches["unmatched"],
        "time_checked": sum(1 for item in errors if item.get("time_checked")),
        "error_m": {
            "median": percentile(values, 0.5),
            "p90": percentile(values, 0.9),
            "max": max(values) if values else None,
        },
        "declared_region": {
            "checked": len(inside),
            "inside": sum(1 for flag in inside if flag),
            "rate": None if not inside else round(sum(1 for flag in inside if flag) / len(inside), 4),
        },
        "by_range_class": {
            name: {
                "count": len(items),
                "median": percentile(items, 0.5),
                "p90": percentile(items, 0.9),
            }
            for name, items in sorted(by_class.items())
        },
        "unmatched_ids": [item["id"] for item in matches["unmatched"]],
    }


def build_report(
    envelope: dict,
    *,
    truth: dict | None = None,
    run_id: str | None = None,
    case_id: str | None = None,
    generated_at: str | None = None,
    time_tolerance_s: float = 5.0,
    max_distance_m: float = 100.0,
) -> dict:
    """组装报告。`thresholds` 故意留空：本轮不冻结门槛（§6.2）。"""
    rows = list(iter_points(envelope))
    points = summarize(rows)
    report = {
        "report": REPORT_KIND,
        "generated_at": generated_at or _utc_now(),
        "dataset": {
            "case_id": case_id or envelope.get("case_id"),
            "run_id": run_id or envelope.get("run_id"),
            "layer": envelope.get("layer"),
            "persisted": envelope.get("persisted"),
            "spatial_revision": envelope.get("spatial_revision"),
            "spatial_error": envelope.get("spatial_error"),
            "tracks": (envelope.get("summary") or {}).get("tracks"),
            "samples": (envelope.get("summary") or {}).get("samples"),
        },
        "gate": {
            "mis_published": points["points"]["formal"],
            "mis_publish_rate": None
            if not points["points"]["total"]
            else round(points["points"]["formal"] / points["points"]["total"], 4),
            "requirement": "mis_publish_rate must be 0 (hard gate)",
        },
        "overall": points,
        "truth": None,
        "thresholds": None,
        "notes": [
            "实验估计不计入正式 L2/L3 发布成功率。",
            "误差区域覆盖率只用投影时声明的 uncertainty_radius_m，事后不放大。",
            "没有独立真值时 truth 为 null，不能把算法输出当基准。",
        ],
    }
    if truth is not None:
        matches = match_truth(rows, truth, time_tolerance_s=time_tolerance_s, max_distance_m=max_distance_m)
        report["truth"] = {
            "dataset_id": truth["dataset_id"],
            "camera_uuid": truth["camera_uuid"],
            "collected_at": truth["collected_at"],
            "point_count": len(truth["points"]),
            "time_tolerance_s": time_tolerance_s,
            "max_distance_m": max_distance_m,
            **error_summary(matches),
        }
    return report


def load_report(path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)