"""M6-S1：BoT-SORT 适配层（第一阶段不启用 ReID）。

与 `ByteTrackAdapter` 的关系：**继承而不是复制**。丢失缓冲换算、空帧推进、
输入时间轴断档、全局轨迹号分配、观测对回原始检测——这些语义必须与现有
ByteTrack 实现完全一致，否则 A/B 两组的差异就不只来自跟踪算法。

三处刻意不同：
1. 固定机位 → 关闭摄像头运动补偿（`enable_cmc=False`），否则 CMC 会把
   画面里行人的整体位移当成相机在动，反而破坏关联；
2. 置信度门槛按 D-FINE N 的实际分数分布配置（与 ByteTrack 同值），
   不套用 BoT-SORT 默认的 0.7/0.6——那会让大部分真实框无法建轨；
3. 本阶段不接外观特征：`trackers` 2.6.1 的 `BoTSORTTracker` 没有 ReID 入口，
   接入 OSNet 需要另写关联层，属于 C 组实验，不在这里偷塞。
"""
from __future__ import annotations

from .m6_track import DEFAULT_PARAMS, ByteTrackAdapter, TrackDependencyError

DEFAULT_BOTSORT_PARAMS = {
    **DEFAULT_PARAMS,
    # 固定机位：不需要相机运动补偿
    "enable_cmc": False,
    "cmc_method": "sparseOptFlow",
    # 三段 IoU 关联阈值（BoT-SORT 把关联拆成 first / second / unconfirmed）
    "iou_first_assoc": 0.2,
    "iou_second_assoc": 0.5,
    "iou_unconfirmed_assoc": 0.3,
    # 第一帧检测直接激活轨迹，避免短片开头 5 帧内建不出轨迹
    "instant_first_frame_activation": True,
}


class BoTSortAdapter(ByteTrackAdapter):
    """BoT-SORT 适配层，接口与 ByteTrackAdapter 完全一致。"""

    ENGINE = "botsort"
    IMPLEMENTATION = "roboflow-trackers"

    def __init__(self, *, sample_interval_seconds: float, params: dict | None = None):
        merged = dict(DEFAULT_BOTSORT_PARAMS)
        merged.update(params or {})
        super().__init__(sample_interval_seconds=sample_interval_seconds, params=merged)
        if self._params.get("enable_cmc"):
            # 固定机位下开 CMC 会让整体运动被误当成相机抖动，这里直接拒绝
            raise TrackDependencyError("botsort_cmc_requires_moving_camera")

    def _ensure_tracker(self):
        if self._tracker is not None:
            return self._tracker
        try:
            from trackers import BoTSORTTracker
        except Exception as exc:  # pragma: no cover - 依赖缺失路径
            raise TrackDependencyError("botsort_unavailable") from exc
        self._tracker = BoTSORTTracker(
            lost_track_buffer=self.lost_track_buffer,
            frame_rate=float(1.0 / self.sample_interval_seconds),
            track_activation_threshold=float(self._params["activation_threshold"]),
            minimum_consecutive_frames=int(self._params["minimum_consecutive_frames"]),
            minimum_iou_threshold_first_assoc=float(self._params["iou_first_assoc"]),
            minimum_iou_threshold_second_assoc=float(self._params["iou_second_assoc"]),
            minimum_iou_threshold_unconfirmed_assoc=float(self._params["iou_unconfirmed_assoc"]),
            high_conf_det_threshold=float(self._params["high_conf_threshold"]),
            enable_cmc=bool(self._params["enable_cmc"]),
            cmc_method=str(self._params["cmc_method"]),
            instant_first_frame_activation=bool(self._params["instant_first_frame_activation"]),
        )
        return self._tracker

    def describe(self) -> dict:
        """把"实际生效"的配置摊开，方便实验结果记录。"""
        return {
            "engine": self.ENGINE,
            "implementation": self.IMPLEMENTATION,
            "sample_interval_seconds": self.sample_interval_seconds,
            "frame_rate": round(1.0 / self.sample_interval_seconds, 3),
            "lost_track_buffer": self.lost_track_buffer,
            "track_activation_threshold": self._params["activation_threshold"],
            "high_conf_det_threshold": self._params["high_conf_threshold"],
            "minimum_consecutive_frames": self._params["minimum_consecutive_frames"],
            "enable_cmc": self._params["enable_cmc"],
            "cmc_method": self._params["cmc_method"],
            "reid": None,
            "gap_seconds_limit": self._params["max_gap_seconds"],
        }
