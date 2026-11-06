"""B8 时间轴聚合读模型（V1.1 P0）。

只读：从既有的 target_observations / tracking_runs / tracking_windows /
tracking_candidates / tracking_review_events / target_spatial_observations
派生时间轴卡片与任务事件。

刻意的三条约束：
1. 不新增事实表，也不改任何 tracking_* 状态——时间轴是视图，不是第四套事实。
2. 拿不到地面坐标时 `position` 就是 None，`displayHint` 说明为什么（无位置/仅设备点），
   绝不按摄像头名称、道路方向补造坐标。
3. 身份与空间分开表达：`identityStatus` 只来自审核事件，`spatialQuality` 只描述几何。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (TargetObservation, TargetSpatialObservation, TrackingCandidate,
                        TrackingReviewEvent, TrackingRun, TrackingWindow)

CARD_KINDS = ("anchor", "camera_task", "track_segment", "gap", "spatial_observation",
              "spatial_observation")

PHASES = {
    "queued": "queued",
    "running": "running",
    "awaiting_review": "awaiting_review",
    "completed": "completed",
    "cancelled": "cancelled",
    "unresolved": "awaiting_review",
    "budget_exhausted": "completed",
    "failed": "failed",
}


class TimelineError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _phase(status: str) -> str:
    return PHASES.get(status, status or "queued")


def _point(row: TargetSpatialObservation) -> dict | None:
    """只有 L2 且确有地面点才给坐标；L1/区域级一律 None。"""
    point = row.ground_point or {}
    if row.spatial_level != "L2":
        return None
    try:
        lat = float(point.get("lat"))
        lon = float(point.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return {"lat": lat, "lon": lon}


def _anchor_card(obs: TargetObservation, anchor: dict) -> dict:
    bbox = (obs.analysis_json or {}).get("bbox_pixels")
    playback = None
    if anchor.get("clip_id") or anchor.get("clip_relpath"):
        playback = {
            "clipId": anchor.get("clip_id"),
            "offsetSeconds": anchor.get("offset_in_clip_seconds"),
            "timeBasis": anchor.get("time_basis"),
            "timeQuality": anchor.get("clock_quality"),
        }
    return {
        "cardId": f"anchor:{obs.id}",
        "kind": "anchor",
        "title": "起始锚点",
        "subtitle": f"{obs.object_class} · 人工框选 · {obs.review_state}",
        "observedAt": _iso(obs.created_at),
        "analysisAt": _iso(obs.created_at),
        "targetId": obs.id,
        "cameraLabel": anchor.get("camera_label"),
        "identityStatus": "candidate_unverified",
        "spatialQuality": None,
        "degradeReason": None,
        "position": None,
        "displayHint": "no_position",
        "playback": playback,
        "evidence": {"bboxPixels": bbox, "mediaId": obs.media_id},
        "segment": None,
    }


def _task_card(run: TrackingRun, windows: list[TrackingWindow]) -> dict:
    progress = run.progress_json or {}
    done = int(progress.get("windows_done") or len(windows) or 0)
    phase = _phase(run.status)
    return {
        "cardId": f"task:{run.id}",
        "kind": "camera_task",
        "title": "追踪任务",
        "subtitle": f"{phase} · 已完成 {done} 个窗口 · 尝试 {run.attempt} 次",
        "observedAt": None,
        "analysisAt": _iso(run.created_at),
        "targetId": run.observation_id,
        "cameraLabel": (run.anchor_json or {}).get("camera_label"),
        "identityStatus": "candidate_unverified",
        "spatialQuality": None,
        "degradeReason": run.error_code,
        "position": None,
        "displayHint": "no_position",
        "playback": None,
        "evidence": {"runId": run.id, "phase": phase, "status": run.status,
                     "windowsDone": done,
                     "framesUsed": progress.get("frames_used"),
                     "errorCode": run.error_code, "errorMessage": run.error_message},
        "segment": None,
    }


def _gap_card(run: TrackingRun, window: TrackingWindow) -> dict:
    broken = window.status not in ("ok", "completed", "done") or window.tracks == 0
    if not broken:
        return None
    return {
        "cardId": f"gap:{window.id}",
        "kind": "gap",
        "title": "证据缺口",
        "subtitle": (f"{window.camera_label} · {window.window_start:.1f}-{window.window_end:.1f}s"
                     f" · {window.status} · {window.tracks} 条轨迹"),
        "observedAt": _iso(window.created_at),
        "analysisAt": _iso(window.created_at),
        "targetId": run.observation_id,
        "cameraLabel": window.camera_label,
        "identityStatus": None,
        "spatialQuality": None,
        "degradeReason": window.status or "no_track",
        "position": None,
        "displayHint": "no_position",
        "playback": {"clipId": window.clip_id, "offsetSeconds": window.window_start,
                     "timeQuality": "uncalibrated"},
        "evidence": {"runId": run.id, "windowId": window.id,
                     "window": [window.window_start, window.window_end],
                     "tracks": window.tracks, "note": window.note},
        "segment": None,
    }


def _segment_card(run: TrackingRun, candidate: TrackingCandidate) -> dict:
    details = candidate.details_json or {}
    return {
        "cardId": f"segment:{candidate.id}",
        "kind": "track_segment",
        "title": f"候选轨迹 {candidate.track_id}",
        "subtitle": (f"{candidate.camera_label} · 窗口 {candidate.window_start}-{candidate.window_end}"
                     f" · {candidate.state}"),
        "observedAt": _iso(candidate.created_at),
        "analysisAt": _iso(candidate.created_at),
        "targetId": run.observation_id,
        "cameraLabel": candidate.camera_label,
        "identityStatus": candidate.identity_status,
        "spatialQuality": "experimental_estimate" if details.get("projected") else None,
        "degradeReason": details.get("degrade_reason"),
        "position": None,
        "displayHint": "no_position",
        "playback": {"clipId": details.get("clip_id"),
                     "offsetSeconds": candidate.window_start,
                     "timeQuality": "uncalibrated"},
        "evidence": {"trackId": candidate.track_id, "state": candidate.state,
                     "retrievalScore": candidate.retrieval_score,
                     "marginTop1Top2": candidate.margin_top1_top2,
                     "evidenceRefs": candidate.evidence_refs or [],
                     "mapCameraId": candidate.map_camera_id},
        "segment": {"segmentId": candidate.id, "trackId": candidate.track_id,
                    "window": [candidate.window_start, candidate.window_end]},
    }


def _spatial_card(obs: TargetObservation, row: TargetSpatialObservation) -> dict:
    position = _point(row)
    return {
        "cardId": f"spatial:{row.id}",
        "kind": "spatial_observation",
        "title": "空间观测",
        "subtitle": f"{row.spatial_level} · {row.quality} · 半径 {row.uncertainty_radius_m}m",
        "observedAt": _iso(row.created_at),
        "analysisAt": _iso(row.created_at),
        "targetId": obs.id,
        "cameraLabel": row.camera_uuid,
        "identityStatus": row.identity_status,
        "spatialQuality": row.spatial_level,
        "degradeReason": None if position else "no_ground_point",
        "position": position,
        "displayHint": "device_point" if position else "no_position",
        "playback": None,
        "evidence": {"calibrationId": row.calibration_id,
                     "calibrationRevision": row.calibration_revision,
                     "uncertaintyRadiusM": row.uncertainty_radius_m,
                     "payload": row.payload or {}},
        "segment": None,
    }


def _sort_key(card: dict) -> tuple:
    stamp = card.get("observedAt") or card.get("analysisAt") or ""
    return (stamp, card["cardId"])


def build_timeline(db: Session, case_id: str, target_id: str) -> dict:
    obs = db.get(TargetObservation, target_id)
    if obs is None or obs.case_id != case_id:
        raise TimelineError("target_not_found", "目标不在该案件内。")

    runs = db.scalars(select(TrackingRun).where(TrackingRun.case_id == case_id,
                                               TrackingRun.observation_id == target_id)
                      .order_by(TrackingRun.created_at)).all()
    run_ids = [run.id for run in runs]
    windows = db.scalars(select(TrackingWindow).where(TrackingWindow.run_id.in_(run_ids))
                         .order_by(TrackingWindow.created_at)).all() if run_ids else []
    candidates = db.scalars(select(TrackingCandidate).where(TrackingCandidate.run_id.in_(run_ids))
                            .order_by(TrackingCandidate.created_at)).all() if run_ids else []
    spatial = db.scalars(select(TargetSpatialObservation)
                         .where(TargetSpatialObservation.case_id == case_id,
                                TargetSpatialObservation.observation_id == target_id)
                         .order_by(TargetSpatialObservation.created_at)).all()

    latest_anchor = (runs[-1].anchor_json or {}) if runs else {}
    cards: list[dict] = [_anchor_card(obs, latest_anchor)]
    for run in runs:
        run_windows = [w for w in windows if w.run_id == run.id]
        cards.append(_task_card(run, run_windows))
        for window in run_windows:
            gap = _gap_card(run, window)
            if gap:
                cards.append(gap)
    for candidate in candidates:
        run = next((r for r in runs if r.id == candidate.run_id), None)
        if run is not None:
            cards.append(_segment_card(run, candidate))
    for row in spatial:
        cards.append(_spatial_card(obs, row))

    cards.sort(key=_sort_key)
    counts: dict[str, int] = {"total": len(cards)}
    for kind in CARD_KINDS:
        counts[kind] = sum(1 for card in cards if card["kind"] == kind)

    latest_run = runs[-1] if runs else None
    return {
        "schema": "b8-timeline/1",
        "caseId": case_id,
        "targetId": target_id,
        "derived": True,
        "note": "时间轴由既有记录派生，不新增事实表；无坐标的卡片不参与地图绘制。",
        "phase": _phase(latest_run.status) if latest_run else "not_started",
        "counts": counts,
        "cards": cards,
    }


def build_events(db: Session, case_id: str, run_id: str, after_seq: int = 0) -> dict:
    run = db.get(TrackingRun, run_id)
    if run is None or run.case_id != case_id:
        raise TimelineError("tracking_run_not_found", "追踪任务不存在。")

    raw: list[tuple[datetime | None, str, str, str, dict]] = []
    raw.append((run.created_at, f"run:{run.id}:queued", "task.queued", "queued", {}))
    if run.started_at is not None:
        raw.append((run.started_at, f"run:{run.id}:running", "task.running", "running", {}))
    if run.finished_at is not None:
        raw.append((run.finished_at, f"run:{run.id}:finished", "task.finished",
                    run.status, {"status": run.status, "errorCode": run.error_code}))

    for window in db.scalars(select(TrackingWindow).where(TrackingWindow.run_id == run_id)
                             .order_by(TrackingWindow.created_at)).all():
        raw.append((window.created_at, f"window:{window.id}", "window.analyzed",
                    window.status,
                    {"window": [window.window_start, window.window_end],
                     "cameraLabel": window.camera_label, "tracks": window.tracks}))
    for candidate in db.scalars(select(TrackingCandidate).where(TrackingCandidate.run_id == run_id)
                                .order_by(TrackingCandidate.created_at)).all():
        raw.append((candidate.created_at, f"candidate:{candidate.id}", "candidate.found",
                    candidate.state,
                    {"trackId": candidate.track_id, "cameraLabel": candidate.camera_label,
                     "retrievalScore": candidate.retrieval_score}))
    for review in db.scalars(select(TrackingReviewEvent).where(TrackingReviewEvent.run_id == run_id)
                             .order_by(TrackingReviewEvent.created_at)).all():
        raw.append((review.created_at, f"review:{review.id}", "review.decided",
                    review.decision,
                    {"candidateId": review.candidate_id, "reviewer": review.reviewer,
                     "note": review.note}))

    raw.sort(key=lambda item: ((item[0].isoformat() if item[0] else ""), item[1]))
    events = []
    for index, (at, event_id, event_type, status, payload) in enumerate(raw, start=1):
        if index <= after_seq:
            continue
        events.append({"seq": index, "eventId": event_id, "type": event_type,
                       "at": _iso(at), "status": status, "payload": payload})
    return {"schema": "b8-task-events/1", "runId": run_id, "derived": True,
            "note": "事件由既有记录派生；需要严格重放时再落 append-only 事件表。",
            "lastSeq": len(raw), "afterSeq": after_seq, "events": events}