"""M6 单镜头轨迹关联：ByteTrack 适配层。

职责边界（V2.0 §6.2）：
- 不自研 MOT。卡尔曼滤波、两阶段关联、轨迹生命周期全部交给成熟实现
  （Roboflow trackers，Apache-2.0；ByteTrack 论文方法）。
- 本模块只做四件事：把检测观测转成跟踪器输入、按真实采样间隔换算缺口约束、
  记录轨迹证据（每帧框与原始检测编号）、发出可追溯事件。
- 不接触摄像头永久 ID、案件身份、数据库，也不做跨镜头关联。

碎片归属（头肩框 vs 完整身体框，IoU≈0.30）不在本模块处理：先观察原生 ByteTrack
的实际输出，再在下一提交里做对照，避免把两处改动混在一起无法归因。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

logger = logging.getLogger("app.m6_track")

DEFAULT_PARAMS = {
    # 缺口同时约束帧数与秒数：0.25s×3 帧 ≈ 0.75s，2s×3 帧 = 6s，不能混用
    "max_gap_seconds": 1.5,
    "max_missing_frames": 3,
    # ByteTrack 内部分两阶段：高置信先关联，低置信补关联。
    # 默认值按 D-FINE N 的实际分数分布校准（person 置信度 p50≈0.29、p90≈0.54）：
    # 沿用 YOLO 习惯的 0.5/0.5 会让大部分真实框无法建轨，实测覆盖率 74%→83%。
    "high_conf_threshold": 0.25,
    "activation_threshold": 0.2,
    "minimum_iou_threshold": 0.1,
    "minimum_consecutive_frames": 1,
}


class TrackDependencyError(RuntimeError):
    """跟踪依赖缺失或输入非法时抛出，不能伪装成“没有目标”。"""


@dataclass
class TrackObservation:
    """一帧中的一个检测观测。detection_index 指回原始检测列表，便于复核。"""

    frame_id: str
    timestamp_seconds: float
    bbox: tuple
    confidence: float
    class_id: int = 0
    detection_index: int = 0
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "frame_id": self.frame_id,
            "timestamp_seconds": round(float(self.timestamp_seconds), 3),
            "bbox": [round(float(v), 2) for v in self.bbox],
            "confidence": round(float(self.confidence), 4),
            "class_id": int(self.class_id),
            "label": self.label,
            "detection_index": int(self.detection_index),
        }


@dataclass
class Tracklet:
    track_id: int
    session_id: int = 1
    local_track_id: int = -1
    observations: list = field(default_factory=list)
    first_seen_seconds: float = 0.0
    last_seen_seconds: float = 0.0
    missing_frames: int = 0
    state: str = "active"
    events: list = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return round(self.last_seen_seconds - self.first_seen_seconds, 3)

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "session_id": self.session_id,
            "local_track_id": self.local_track_id,
            "state": self.state,
            "start_seconds": round(self.first_seen_seconds, 3),
            "end_seconds": round(self.last_seen_seconds, 3),
            "duration_seconds": self.duration_seconds,
            "observation_count": len(self.observations),
            "observations": [o.to_dict() for o in self.observations],
        }


def gap_frames_from_seconds(max_gap_seconds: float, sample_interval_seconds: float,
                            max_missing_frames: int | None = None) -> int:
    """把“允许缺口秒数”换算成帧数缓冲，并与帧数上限取小。

    2 秒采样时 1.5 秒缺口还不到 1 帧，说明这份素材本来就不适合判断“短时遮挡”，
    此时按 1 帧处理并在调用方留下提示，而不是静默放大成 3 秒。
    """
    if sample_interval_seconds is None or sample_interval_seconds <= 0:
        raise TrackDependencyError("sample_interval_invalid")
    frames = int(math.ceil(float(max_gap_seconds) / float(sample_interval_seconds)))
    frames = max(1, frames)
    if max_missing_frames:
        frames = min(frames, int(max_missing_frames))
    return frames


class ByteTrackAdapter:
    """ByteTrack 的最小适配层：输入观测，输出轨迹与事件。"""

    ENGINE = "bytetrack"
    IMPLEMENTATION = "roboflow-trackers"

    def __init__(self, *, sample_interval_seconds: float, params: dict | None = None):
        self._params = dict(DEFAULT_PARAMS)
        self._params.update(params or {})
        self.sample_interval_seconds = float(sample_interval_seconds)
        self.lost_track_buffer = gap_frames_from_seconds(
            self._params["max_gap_seconds"],
            self.sample_interval_seconds,
            self._params.get("max_missing_frames"),
        )
        self._tracker = None
        self._tracks: dict[int, Tracklet] = {}
        self._events: list[dict] = []
        self._warnings: list[str] = []
        # 轨迹号由适配器统一分配：ByteTrack 重置后本地号会从 0 重新开始，
        # 若直接沿用会与历史轨迹撞号（两段素材被误认成同一条轨迹）。
        self._session_id = 1
        self._local_to_global: dict[int, int] = {}
        self._next_global_id = 1
        # 只按“输入时间轴”判断断档；单条轨迹暂时丢失走 _age，不重置整段。
        self._last_frame_seconds = None
        if self.sample_interval_seconds >= 1.0:
            self._warnings.append(
                "sample_interval_ge_1s: 采样间隔 %.2fs，短时遮挡与交错无法用本素材判断"
                % self.sample_interval_seconds
            )

    # ---- 依赖装配 ----
    def _ensure_tracker(self):
        if self._tracker is not None:
            return self._tracker
        try:
            from trackers import ByteTrackTracker
        except Exception as exc:  # pragma: no cover - 依赖缺失路径
            raise TrackDependencyError("bytetrack_unavailable") from exc
        self._tracker = ByteTrackTracker(
            lost_track_buffer=self.lost_track_buffer,
            frame_rate=float(1.0 / self.sample_interval_seconds),
            track_activation_threshold=float(self._params["activation_threshold"]),
            minimum_consecutive_frames=int(self._params["minimum_consecutive_frames"]),
            minimum_iou_threshold=float(self._params["minimum_iou_threshold"]),
            high_conf_det_threshold=float(self._params["high_conf_threshold"]),
        )
        return self._tracker

    # ---- 主流程 ----
    def update(self, observations: Sequence[TrackObservation]) -> list[Tracklet]:
        """喂入一帧的观测，返回当前仍然活跃的轨迹。"""
        try:
            import numpy as np
            from supervision import Detections
        except Exception as exc:  # pragma: no cover
            raise TrackDependencyError("supervision_unavailable") from exc

        if observations:
            now = min(o.timestamp_seconds for o in observations)
            # 先判断档、再取跟踪器：否则本帧仍会调用已经被丢弃的旧实例，
            # 重置要拖到下一帧才生效（断档当帧就可能在旧状态上接错人）。
            self._handle_stream_gap(now)
            self._last_frame_seconds = now
        tracker = self._ensure_tracker()
        if not observations:
            # 本帧没有任何检测：不喂跟踪器（空 Detections 的内部状态不可靠），
            # 只推进适配器的老化，lost 事件由 _age 负责。
            self._age(set())
            return [t for t in self._tracks.values() if t.state == "active"]

        xyxy = np.asarray([o.bbox for o in observations], dtype=np.float32)
        conf = np.asarray([o.confidence for o in observations], dtype=np.float32)
        cls = np.asarray([o.class_id for o in observations], dtype=np.int32)
        # 把观测编号随检测一起传给跟踪器，输出时可直接对回原始检测（比按坐标反查可靠）
        detections = Detections(xyxy=xyxy, confidence=conf, class_id=cls,
                                data={"obs_index": np.arange(len(observations))})
        tracked = tracker.update(detections)
        return self._absorb(tracked, observations)

    def _handle_stream_gap(self, now: float):
        """输入时间轴出现断档时，结束全部在跟踪的轨迹并重建跟踪器。

        判断依据只有“上一帧输入时间”与“本帧时间”。某条轨迹自己暂时丢失
        （被挡住两帧）不应该牵连其他正常轨迹，那属于 _age 的职责。
        """
        if self._last_frame_seconds is None:
            return
        gap = now - self._last_frame_seconds
        limit = float(self._params["max_gap_seconds"])
        if gap <= limit:
            return
        for tid, track in list(self._tracks.items()):
            if track.state == "ended":
                continue
            track.state = "ended"
            self._event("track_ended", tid, None,
                        {"reason": "stream_gap", "gap_seconds": round(gap, 3),
                         "gap_limit_seconds": limit})
        # 跨断档重建跟踪器：内部卡尔曼状态会把两段素材接成一条轨迹
        self._tracker = None
        self._local_to_global.clear()
        self._session_id += 1
        self._warnings.append(
            "tracker_reset_after_stream_gap: %.1fs 处断档 %.1fs 超过 max_gap，"
            "已重建跟踪器（新 session=%d）" % (now, gap, self._session_id))

    def _global_id(self, local_track_id: int) -> int:
        """把 ByteTrack 的本地号映射成适配器全局号，重置后不会撞号。"""
        gid = self._local_to_global.get(local_track_id)
        if gid is None:
            gid = self._next_global_id
            self._next_global_id += 1
            self._local_to_global[local_track_id] = gid
        return gid

    def _absorb(self, tracked, observations: Sequence[TrackObservation]) -> list[Tracklet]:
        ids = getattr(tracked, "tracker_id", None)
        boxes = getattr(tracked, "xyxy", None)
        confs = getattr(tracked, "confidence", None)
        data = getattr(tracked, "data", None) or {}
        obs_index = data.get("obs_index") if isinstance(data, dict) else None
        seen: set[int] = set()
        if ids is not None and len(ids):
            for idx, tid in enumerate(ids):
                local = int(tid)
                if local < 0:
                    # ByteTrack 用 -1 表示“本帧输出但未归属任何轨迹”，
                    # 把它当轨迹会把整段素材并成一条假长轨，必须丢弃。
                    continue
                tid = self._global_id(local)
                seen.add(tid)
                obs = None
                if obs_index is not None and idx < len(obs_index):
                    pos = int(obs_index[idx])
                    if 0 <= pos < len(observations):
                        obs = observations[pos]
                if obs is None:
                    obs = self._match_observation(
                        boxes[idx], confs[idx] if confs is not None else None, observations)
                if obs is None:
                    self._warnings.append("observation_unresolved: track %d 无法对回原始检测" % tid)
                    continue
                self._push(tid, obs, local)
        self._age(seen)
        return [t for t in self._tracks.values() if t.state == "active"]

    @staticmethod
    def _match_observation(box, conf, observations):
        """按坐标把跟踪器输出对回原始检测，保证可追溯。"""
        best, best_err, tol = None, None, 1.0
        for o in observations:
            err = max(abs(float(a) - float(b)) for a, b in zip(box, o.bbox))
            limit = max(1.0, 0.02 * (o.bbox[3] - o.bbox[1]))
            if conf is not None and abs(float(conf) - o.confidence) > 1e-3:
                continue
            if best_err is None or err < best_err:
                best, best_err, tol = o, err, limit
        if best is None:
            return None
        return best if best_err is not None and best_err <= tol else None

    def _push(self, tid: int, obs: TrackObservation, local: int | None = None):
        track = self._tracks.get(tid)
        created = track is None
        if created:
            track = Tracklet(track_id=tid, session_id=self._session_id,
                             local_track_id=tid if local is None else local,
                             first_seen_seconds=obs.timestamp_seconds)
            self._tracks[tid] = track
            self._event("track_created", tid, obs, {"reason": "first_observation"})
        elif track.state == "lost":
            track.state = "active"
            self._event("track_reacquired", tid, obs,
                        {"gap_frames": track.missing_frames,
                         "gap_seconds": round(obs.timestamp_seconds - track.last_seen_seconds, 3)})
        elif not created:
            track.last_seen_seconds = obs.timestamp_seconds
        track.observations.append(obs)
        track.last_seen_seconds = obs.timestamp_seconds
        track.missing_frames = 0
        self._event("observation_matched", tid, obs, {})

    def _age(self, seen: set[int]):
        """没有出现在本帧输出里的轨迹：先标 lost，超过缺口上限才 ended。

        lost 只表示“这一帧没看到”，不代表目标离场；超过 max_gap 才结束。
        """
        limit = self.lost_track_buffer
        for tid, track in list(self._tracks.items()):
            if track.state == "ended" or tid in seen:
                continue
            track.missing_frames += 1
            if track.state == "active":
                track.state = "lost"
                self._event("track_lost", tid, None,
                            {"missing_frames": track.missing_frames, "gap_limit_frames": limit})
            if track.missing_frames > limit:
                track.state = "ended"
                self._event("track_ended", tid, None,
                            {"missing_frames": track.missing_frames, "gap_limit_frames": limit})
        return None

    # ---- 事件 ----
    def _event(self, kind: str, tid: int, obs: TrackObservation | None, extra: dict):
        item = {"event": kind, "track_id": int(tid)}
        if obs is not None:
            item["frame_id"] = obs.frame_id
            item["timestamp_seconds"] = round(float(obs.timestamp_seconds), 3)
            item["bbox"] = [round(float(v), 2) for v in obs.bbox]
            item["confidence"] = round(float(obs.confidence), 4)
            item["detection_index"] = int(obs.detection_index)
        if extra:
            item.update(extra)
        self._events.append(item)

    def duplicate_overlaps(self, iou_threshold: float = 0.5) -> list[dict]:
        """同一帧里两条活跃轨迹高度重叠：可能是碎片，也可能是换人，先记录不合并。"""
        by_frame: dict[str, list[tuple[int, TrackObservation]]] = {}
        for tid, track in self._tracks.items():
            for obs in track.observations:
                by_frame.setdefault(obs.frame_id, []).append((tid, obs))
        out = []
        for frame_id, items in by_frame.items():
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    a, b = items[i], items[j]
                    if a[0] == b[0]:
                        continue
                    value = _iou(a[1].bbox, b[1].bbox)
                    if value >= iou_threshold:
                        out.append({"event": "duplicate_overlap", "frame_id": frame_id,
                                    "timestamp_seconds": a[1].timestamp_seconds,
                                    "track_ids": [a[0], b[0]], "iou": round(value, 3)})
        return out

    # ---- 输出 ----
    def tracks_json(self) -> dict:
        return {
            "engine": self.ENGINE,
            "implementation": self.IMPLEMENTATION,
            "params": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self._params.items()},
            "sample_interval_seconds": self.sample_interval_seconds,
            "lost_track_buffer_frames": self.lost_track_buffer,
            "warnings": list(self._warnings),
            "tracks": [self._tracks[tid].to_dict() for tid in sorted(self._tracks)],
        }

    def events_json(self) -> dict:
        return {"event_count": len(self._events), "events": self._events}

    def summary(self) -> dict:
        total = len(self._tracks)
        active = sum(1 for t in self._tracks.values() if t.state == "active")
        return {"track_count": total, "active_tracks": active,
                "event_count": len(self._events),
                "lost_track_buffer_frames": self.lost_track_buffer,
                "sample_interval_seconds": self.sample_interval_seconds}


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / area if area > 0 else 0.0


def observations_from_detections(frame_id: str, timestamp_seconds: float,
                                 detections: Iterable[dict]) -> list[TrackObservation]:
    """把一帧检测（bbox/confidence/label/class_id）转成观测列表。"""
    out = []
    for idx, det in enumerate(detections):
        box = det.get("bbox") or det.get("box")
        if not box or len(box) != 4:
            raise TrackDependencyError("detection_bbox_invalid")
        out.append(TrackObservation(
            frame_id=frame_id,
            timestamp_seconds=float(det.get("timestamp_seconds", timestamp_seconds)),
            bbox=tuple(float(v) for v in box),
            confidence=float(det.get("confidence", det.get("score", 1.0))),
            class_id=int(det.get("class_id", 0)),
            detection_index=int(det.get("index", idx)),
            label=str(det.get("label") or ""),
        ))
    return out
