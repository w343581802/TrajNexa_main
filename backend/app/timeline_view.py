"""B8 时间轴主视图（R1.9）：把技术卡压成目标活动过程。

只读派生，不新增事实表，不写任何 tracking_* 或空间观测。三条硬约束：
1. 主视图只有已确认轨迹 / 待核查线索 / 区域排查三类卡；技术对象进详情。
2. 区域按小范围相邻设备聚合，不按 runId；无坐标时只合并完全同名镜头。
3. 卡片时间必须是目标事件时间；映射不完整就为 None，不用 created_at 冒充。
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.camera_catalog import canonical_name
from app.timeline_projection import (CONFIRMED_IDENTITY, PRESENTATION_CONFIRMED,
                                     build_timeline)

REGION_JOIN_METERS = 60.0
KIND_ORDER = {"confirmed_track": 0, "candidate_clue": 1, "region_investigation": 2}


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _haversine_m(a: dict, b: dict) -> float:
    radius = 6371000.0
    lat1, lon1 = math.radians(a["lat"]), math.radians(a["lon"])
    lat2, lon2 = math.radians(b["lat"]), math.radians(b["lon"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


def _short_camera(label: str) -> str:
    text = re.sub(r"^\d+-[A-Za-z]{2,}-", "", str(label or "")).strip()
    text = re.sub(r"-(向东|向西|向南|向北|球)$", "", text).strip()
    return text or str(label or "未标注设备")


def _region_place(labels: list[str]) -> str:
    bases = sorted({_short_camera(label) for label in labels if label})
    if not bases:
        return "未标注设备"
    prefix = bases[0]
    for base in bases[1:]:
        while prefix and not base.startswith(prefix):
            prefix = prefix[:-1]
    base = prefix.rstrip("·-、 ") if len(prefix) >= 3 else bases[0]
    return base if base.endswith("附近") else f"{base} 附近"


def _cluster_labels(labels: list[str], positions: dict[str, dict]) -> dict[str, str]:
    """返回 label -> 稳定区域 key；有坐标按 60m 连通，无坐标只合并完全同名。"""
    nodes = sorted({label for label in labels if label})
    parent = {label: label for label in nodes}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[max(root_left, root_right)] = min(root_left, root_right)

    for index, left in enumerate(nodes):
        point_left = positions.get(canonical_name(left))
        if not point_left:
            continue
        for right in nodes[index + 1:]:
            point_right = positions.get(canonical_name(right))
            if not point_right:
                continue
            if _haversine_m(point_left, point_right) <= REGION_JOIN_METERS:
                union(left, right)

    members: dict[str, list[str]] = {}
    for node in nodes:
        members.setdefault(find(node), []).append(node)
    keys: dict[str, str] = {}
    for root, group in members.items():
        canonical = sorted(canonical_name(item) for item in group)
        geo = any(positions.get(canonical_name(item)) for item in group)
        prefix = "geo" if geo else "name"
        keys.update({label: f"{prefix}:{_digest('|'.join(canonical))}" for label in group})
    return keys


def _frames(card: dict) -> bool:
    segment = card.get("segment") or {}
    evidence = card.get("evidence") or {}
    return bool(segment.get("frames") or evidence.get("frames") or evidence.get("points"))


def _candidate_rank(card: dict) -> tuple:
    identity = card.get("identityStatus")
    rank = 2 if identity in CONFIRMED_IDENTITY else (1 if identity == "rejected" else 0)
    return (rank, 1 if _frames(card) else 0, card.get("analysisAt") or "")


def _candidate_key(card: dict) -> tuple:
    segment = card.get("segment") or {}
    window = segment.get("window") or [None]
    return (card.get("cameraLabel"), (card.get("evidence") or {}).get("trackId") or card.get("cardId"),
            round(float(window[0]), 1) if window and window[0] is not None else None)


def _dedupe_candidates(cards: list[dict]) -> list[dict]:
    chosen: dict[tuple, dict] = {}
    for card in cards:
        key = _candidate_key(card)
        if key not in chosen or _candidate_rank(card) > _candidate_rank(chosen[key]):
            chosen[key] = card
    return sorted(chosen.values(), key=lambda item: (item.get("observedAt") or "", item["cardId"]))


def _run_budget(region: dict) -> dict:
    progress = region or {}
    return {
        "runId": progress.get("runId"),
        "attempt": progress.get("attempt"),
        "status": progress.get("runStatus"),
        "budgetSeconds": progress.get("researchBudgetSeconds"),
        "usedSeconds": progress.get("researchSecondsUsed"),
        "executionCount": progress.get("executionCount"),
        "stopReason": progress.get("stopReason"),
    }


def _region_status(tasks: list[dict], candidate_count: int, run_rows: list[dict]) -> str:
    if not tasks:
        return "planned"
    phases = [task.get("phase") for task in tasks]
    if "running" in phases:
        return "running"
    if "queued" in phases:
        return "queued"
    if all(phase in ("failed", "no_recording") for phase in phases):
        return "failed"
    if any(row.get("runStatus") == "cancelled" for row in run_rows) and not candidate_count:
        return "cancelled"
    if any(row.get("runStatus") in ("awaiting_review", "unresolved") for row in run_rows):
        return "awaiting_review"
    if candidate_count:
        return "completed_with_evidence"
    return "completed_no_match"


def _window_ranges(tasks: list[dict]) -> list[dict]:
    ranges: list[dict] = []
    for task in sorted(tasks, key=lambda item: (item.get("windowSeconds") or [0])[0]):
        window = task.get("windowSeconds") or [None, None]
        if window[0] is None or window[1] is None:
            continue
        if ranges and float(window[0]) <= float(ranges[-1]["endSeconds"]) + 1.0:
            ranges[-1]["endSeconds"] = max(float(ranges[-1]["endSeconds"]), float(window[1]))
            if task.get("plannedEndAt"):
                ranges[-1]["endAt"] = task["plannedEndAt"]
            continue
        ranges.append({"startSeconds": window[0], "endSeconds": window[1],
                       "startAt": task.get("plannedStartAt"), "endAt": task.get("plannedEndAt"),
                       "contiguousToPrev": not ranges})
    return ranges


def _merge_camera_tasks(tasks: list[dict]) -> list[dict]:
    """按摄像头合并多次检索，避免同一台设备在区域详情里重复出现多行。

    一个 Run 只覆盖一小段录像，同一台设备被多次检索是常态；区域详情应回答
    “这台设备查了几次、覆盖哪些时间、有没有候选”，而不是把 Run 当设备。
    """
    merged: dict[str, dict] = {}
    for task in sorted(tasks, key=lambda item: (item.get("windowSeconds") or [0])[0]):
        key = task.get("cameraLabel") or task.get("cameraUuid") or ""
        row = merged.get(key)
        if row is None:
            row = dict(task)
            row["windowRanges"] = []
            row["runIds"] = []
            row["windowCount"] = 0
            merged[key] = row
        run_id = task.get("runId")
        if run_id and run_id not in row["runIds"]:
            row["runIds"].append(run_id)
        window = task.get("windowSeconds") or []
        if len(window) >= 2 and window[0] is not None and window[1] is not None:
            row["windowRanges"].append({"startSeconds": window[0], "endSeconds": window[1],
                                        "startAt": task.get("plannedStartAt"),
                                        "endAt": task.get("plannedEndAt")})
        row["windowCount"] += int(task.get("windowCount") or 1)
        phase = task.get("phase") or "unknown"
        current = row.get("phase") or "unknown"
        priority = {"running": 0, "queued": 1, "failed": 2, "no_recording": 3,
                    "completed": 4, "unknown": 5}
        if priority.get(phase, 9) < priority.get(current, 9):
            row["phase"] = phase
        starts = [value for value in (row.get("plannedStartAt"), task.get("plannedStartAt")) if value]
        ends = [value for value in (row.get("plannedEndAt"), task.get("plannedEndAt")) if value]
        row["plannedStartAt"] = min(starts) if starts else None
        row["plannedEndAt"] = max(ends) if ends else None
    rows: list[dict] = []
    for row in merged.values():
        window_ranges = sorted(row["windowRanges"], key=lambda item: float(item["startSeconds"]))
        compact: list[dict] = []
        for item in window_ranges:
            if compact and float(item["startSeconds"]) <= float(compact[-1]["endSeconds"]) + 1.0:
                compact[-1]["endSeconds"] = max(float(compact[-1]["endSeconds"]), float(item["endSeconds"]))
                compact[-1]["endAt"] = item.get("endAt") or compact[-1].get("endAt")
                continue
            compact.append(dict(item))
        row["windowRanges"] = compact
        if compact:
            row["windowSeconds"] = [compact[0]["startSeconds"], compact[0]["endSeconds"]]
        row["runId"] = row.get("runId") or (row["runIds"][0] if row["runIds"] else None)
        rows.append(row)
    return rows


def _region_payload(group: dict) -> dict:
    source_tasks = sorted(group["tasks"], key=lambda item: (item.get("windowSeconds") or [0])[0])
    runs = list(group["runs"].values())
    candidates = group["candidates"]
    # 候选按摄像头归并，让区域详情能说清“哪台设备发现了候选”，而不是只给区域总数
    candidate_by_camera: dict[str, int] = {}
    pending_by_camera: dict[str, int] = {}
    for card in candidates:
        label = card.get("cameraLabel")
        if not label:
            continue
        candidate_by_camera[label] = candidate_by_camera.get(label, 0) + 1
        if card.get("identityStatus") not in CONFIRMED_IDENTITY:
            pending_by_camera[label] = pending_by_camera.get(label, 0) + 1
    tasks = _merge_camera_tasks(source_tasks)
    for task in tasks:
        label = task.get("cameraLabel")
        task["candidateCount"] = candidate_by_camera.get(label, 0)
        task["pendingCandidateCount"] = pending_by_camera.get(label, 0)
    ranges = _window_ranges(source_tasks)
    observed = [task.get("plannedStartAt") for task in tasks if task.get("plannedStartAt")]
    status = _region_status(tasks, len(candidates), runs)
    budgets = [row.get("budgetSeconds") for row in runs if row.get("budgetSeconds") is not None]
    used = [row.get("usedSeconds") for row in runs if row.get("usedSeconds") is not None]
    phase_counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0,
                    "no_recording": 0, "unknown": 0}
    for task in source_tasks:
        phase = task.get("phase") or "unknown"
        phase_counts[phase] = phase_counts.get(phase, 0) + 1
    labels = sorted({task.get("cameraLabel") for task in tasks if task.get("cameraLabel")})
    camera_uuids = sorted({task.get("cameraUuid") for task in tasks if task.get("cameraUuid")})
    preferred = sorted(runs, key=lambda row: (
        0 if row.get("runStatus") == "running" else
        1 if row.get("runStatus") == "queued" else
        2 if row.get("runStatus") in ("awaiting_review", "unresolved") else 3,
        str(row.get("runId")),
    ))
    primary = preferred[0]["runId"] if preferred else None
    time_quality = next((row.get("timeQuality") for row in group["regions"] if row.get("timeQuality")), None)
    planned = None
    if ranges:
        planned = {"startSeconds": ranges[0]["startSeconds"], "endSeconds": ranges[-1]["endSeconds"],
                   "startAt": ranges[0].get("startAt"), "endAt": ranges[-1].get("endAt"),
                   "contiguous": len(ranges) == 1}
    return {
        "status": status,
        "timeBasis": next((row.get("timeBasis") for row in group["regions"] if row.get("timeBasis")), "unknown"),
        "timeQuality": time_quality or "unverified",
        "regionGeometryRef": None,
        "regionSource": "planned_windows" if tasks else "none",
        "plannedObservationWindow": planned,
        "windowRanges": ranges,
        "hasWindowGap": len(ranges) > 1,
        "cameraTasks": tasks,
        "completedWindows": phase_counts["completed"],
        "plannedWindows": len(source_tasks),
        "deviceCount": len(labels),
        "phaseCounts": phase_counts,
        "candidateCount": len(candidates),
        "pendingCandidates": sum(1 for card in candidates if card.get("identityStatus") not in CONFIRMED_IDENTITY),
        "runIds": [row.get("runId") for row in runs if row.get("runId")],
        "runs": runs,
        "primaryRunId": primary,
        "researchBudgetSeconds": sum(float(item) for item in budgets) if budgets else None,
        "researchSecondsUsed": round(sum(float(item) for item in used), 1) if used else None,
        "executionCount": sum(int(row.get("executionCount") or 0) for row in runs),
        "lastUpdatedAt": max((row.get("lastUpdatedAt") for row in group["regions"] if row.get("lastUpdatedAt")), default=None),
        "cameraUuids": camera_uuids,
        "centroid": group.get("centroid"),
        "detailCardIds": sorted({card_id for row in group["regions"] for card_id in row.get("derivedCardIds") or []}),
        "derivedCardIds": sorted({card_id for row in group["regions"] for card_id in row.get("derivedCardIds") or []}),
        "note": "区域卡只表示正在检查这些设备，不代表目标到达该区域；时间范围是计划检索的录像时间。",
    }


def _region_key_for_card(card: dict, label_keys: dict[str, str]) -> str | None:
    labels = [task.get("cameraLabel") for task in (card.get("region") or {}).get("cameraTasks") or []]
    labels = [label for label in labels if label]
    if not labels:
        label = card.get("cameraLabel")
        labels = [label] if label else []
    keys = {label_keys.get(label) for label in labels if label_keys.get(label)}
    if len(keys) == 1:
        return keys.pop()
    return None


def compose_view(payload: dict, positions: dict[str, dict] | None = None) -> dict:
    positions = positions or {}
    cards = payload.get("cards") or []
    region_cards = [card for card in cards if card.get("kind") == "region_investigation"]
    segment_cards = [card for card in cards if card.get("kind") == "track_segment"]
    anchor_cards = [card for card in cards if card.get("kind") == "anchor"]
    labels = []
    for card in region_cards:
        region = card.get("region") or {}
        labels += [task.get("cameraLabel") for task in region.get("cameraTasks") or [] if task.get("cameraLabel")]
        if not region.get("cameraTasks") and card.get("cameraLabel"):
            labels.append(card["cameraLabel"])
    labels += [card.get("cameraLabel") for card in segment_cards if card.get("cameraLabel")]
    label_keys = _cluster_labels(labels, positions)

    groups: dict[str, dict] = {}

    def group_for(key: str) -> dict:
        return groups.setdefault(key, {"labels": set(), "tasks": {}, "regions": [], "runs": {},
                                       "candidates": [], "regionIds": set()})

    for card in region_cards:
        region = card.get("region") or {}
        tasks = region.get("cameraTasks") or []
        if not tasks:
            key = _region_key_for_card(card, label_keys)
            if key is None:
                label = card.get("cameraLabel") or "未标注设备"
                label_keys.setdefault(label, f"name:{_digest(canonical_name(label))}")
                key = label_keys[label]
            bucket = group_for(key)
            if card["cardId"] not in bucket["regionIds"]:
                bucket["regions"].append(region)
                bucket["regionIds"].add(card["cardId"])
                bucket["labels"].add(card.get("cameraLabel") or "未标注设备")
            if region.get("runId"):
                bucket["runs"][region["runId"]] = _run_budget(region)
            continue
        for task in tasks:
            key = label_keys.get(task.get("cameraLabel"))
            if key is None:
                continue
            bucket = group_for(key)
            bucket["labels"].add(task.get("cameraLabel"))
            # 同一台设备、同一段录像窗口被多个 run 重复分析时只算一个检索窗口；
            # windowId 是每次 run 各自的，不能拿来当去重键，否则“14 个窗口”里大半是重复。
            window = tuple(round(float(value), 1) for value in (task.get("windowSeconds") or []))
            task_key = (task.get("cameraLabel"), window)
            run_id = region.get("runId")
            existing = bucket["tasks"].get(task_key)
            if existing is None:
                bucket["tasks"][task_key] = {
                    **task, "runId": run_id, "runIds": [run_id] if run_id else [],
                    "regionCardId": card["cardId"],
                }
            elif run_id and run_id not in existing["runIds"]:
                existing["runIds"].append(run_id)
            if card["cardId"] not in bucket["regionIds"]:
                bucket["regions"].append(region)
                bucket["regionIds"].add(card["cardId"])
            if region.get("runId"):
                bucket["runs"][region["runId"]] = _run_budget(region)

    for card in segment_cards:
        key = label_keys.get(card.get("cameraLabel"))
        if key is None:
            continue
        group_for(key)["candidates"].append(card)

    for key, group in groups.items():
        group["tasks"] = list(group["tasks"].values()) if isinstance(group["tasks"], dict) else group["tasks"]
        points = [positions.get(canonical_name(label)) for label in group["labels"]]
        points = [point for point in points if point]
        if points:
            group["centroid"] = {"lat": sum(point["lat"] for point in points) / len(points),
                                 "lon": sum(point["lon"] for point in points) / len(points)}
        group["candidates"] = _dedupe_candidates(group["candidates"])

    main_cards: list[dict] = []
    for key, group in groups.items():
        if not group["tasks"] and not group["candidates"] and not group["regions"]:
            continue
        place = _region_place(sorted(group["labels"]))
        region = _region_payload(group)
        labels = sorted({task.get("cameraLabel") for task in group["tasks"] if task.get("cameraLabel")})
        confirmed = [card for card in group["candidates"]
                     if card.get("identityStatus") in CONFIRMED_IDENTITY and _frames(card)]
        others = [card for card in group["candidates"] if card not in confirmed]
        for card in confirmed:
            main_cards.append({
                "cardId": f"view:track:{card['cardId']}",
                "kind": "confirmed_track",
                "title": "已确认轨迹",
                "place": place,
                "subtitle": "身份已确认 · 有逐帧轨迹",
                "observedAt": card.get("observedAt"),
                "observedAtSource": card.get("observedAtSource"),
                "timeQuality": region.get("timeQuality"),
                "identityStatus": card.get("identityStatus"),
                "spatialQuality": card.get("spatialQuality"),
                "detailCardIds": [card["cardId"]],
                "candidate": {"count": 1, "pending": 0, "cardId": card["cardId"]},
                "region": None,
            })
        if others:
            pending = sum(1 for card in others if card.get("identityStatus") not in CONFIRMED_IDENTITY)
            confirmed_no_frames = sum(1 for card in others if card.get("identityStatus") in CONFIRMED_IDENTITY)
            subtitle = f"{len(others)} 条候选 · {pending} 条待确认"
            if confirmed_no_frames:
                subtitle += " · 含已确认身份但无逐帧轨迹"
            earliest = next((card for card in others if card.get("observedAt")), others[0])
            main_cards.append({
                "cardId": f"view:clue:{key}",
                "kind": "candidate_clue",
                "title": "待核查线索",
                "place": place,
                "subtitle": subtitle,
                "observedAt": earliest.get("observedAt"),
                "observedAtSource": earliest.get("observedAtSource"),
                "timeQuality": region.get("timeQuality"),
                "identityStatus": ("confirmed" if confirmed_no_frames == len(others) else "candidate_unverified"),
                "spatialQuality": None,
                "detailCardIds": [card["cardId"] for card in others],
                "candidate": {"count": len(others), "pending": pending,
                              "confirmedWithoutFrames": confirmed_no_frames,
                              "cardIds": [card["cardId"] for card in others]},
                "region": None,
            })
        observed = region.get("plannedObservationWindow") or {}
        main_cards.append({
            "cardId": f"view:{key}",
            "kind": "region_investigation",
            "title": "区域排查",
            "place": place,
            "subtitle": (f"{region['deviceCount']} 台设备 · {region['plannedWindows']} 次检索"
                         f" · 已完成 {region['completedWindows']}"),
            "observedAt": observed.get("startAt"),
            "observedAtSource": "event_time" if observed.get("startAt") else "unmapped",
            "timeQuality": region.get("timeQuality"),
            "identityStatus": "candidate_unverified",
            "spatialQuality": None,
            "detailCardIds": region.get("detailCardIds") or [],
            "region": region,
            "candidate": None,
            "labels": labels,
        })

    main_cards.sort(key=lambda card: (
        0 if card.get("observedAt") else 1,
        card.get("observedAt") or "",
        KIND_ORDER.get(card["kind"], 9),
        card["cardId"],
    ))
    counts = {"confirmed": 0, "review": 0, "region": 0, "total": len(main_cards)}
    for card in main_cards:
        if card["kind"] == "confirmed_track":
            counts["confirmed"] += 1
        elif card["kind"] == "candidate_clue":
            counts["review"] += 1
        else:
            counts["region"] += 1

    observed_cards = [card for card in main_cards if card.get("observedAt") and card["kind"] != "region_investigation"]
    anchor = anchor_cards[0] if anchor_cards else None
    if anchor and anchor.get("observedAt"):
        observed_cards.append(anchor)
    earliest = min(observed_cards, key=lambda card: card["observedAt"]) if observed_cards else None
    latest = max(observed_cards, key=lambda card: card["observedAt"]) if observed_cards else None
    navigation = {
        "earlier": {"available": bool(earliest), "anchorCardId": earliest["cardId"] if earliest else None,
                    "observedAt": earliest.get("observedAt") if earliest else None,
                    "basis": "earliest_target_observation", "enabled": False,
                    "reason": None if earliest else "no_target_time",
                    "disabledReason": "search_window_api_not_implemented" if earliest else None},
        "later": {"available": bool(latest), "anchorCardId": latest["cardId"] if latest else None,
                  "observedAt": latest.get("observedAt") if latest else None,
                  "basis": "latest_target_observation", "enabled": False,
                  "reason": None if latest else "no_target_time",
                  "disabledReason": "search_window_api_not_implemented" if latest else None},
    }
    return {
        "schema": "b8-timeline-view/1",
        "caseId": payload.get("caseId"),
        "targetId": payload.get("targetId"),
        "derived": True,
        "phase": payload.get("phase"),
        "anchorLinked": payload.get("anchorLinked"),
        "counts": counts,
        "cards": main_cards,
        "navigation": navigation,
        "note": "主视图只呈现目标活动过程；卡片按目标事件时间排序，相邻卡片不代表存在连续轨迹。",
    }


def build_timeline_view(db: Session, case_id: str, target_id: str,
                        camera_directory: dict[str, str] | None = None,
                        positions: dict[str, dict] | None = None) -> dict:
    payload = build_timeline(db, case_id, target_id, camera_directory=camera_directory)
    return compose_view(payload, positions)