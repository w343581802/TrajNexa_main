"""S6-15D：录像分析调度器（串行 / 小批次，可恢复）。

职责边界：
  预测层给候选与时间窗 → 本模块决定"下一次分析哪台、哪一段"
  → 调 m6_window_analysis 取观测 → 结果只算 candidate，**不自动确认**。

状态：planned / running / candidate_found / needs_review / no_valid_observation /
      detection_missed / recording_unavailable / budget_exhausted / deferred / hop_confirmed
"""
from __future__ import annotations

DEFAULT_SCHEDULER_PARAMS = {
    "segment_seconds": 6.0,
    "fps": 5.0,
    "max_decoded_frames": 3000,
    "allow_expand": True,
    "modes": ("batch", "serial", "segmented"),
}

STATES = ("planned", "running", "candidate_found", "needs_review",
          "no_valid_observation", "detection_missed", "recording_unavailable",
          "budget_exhausted", "deferred", "hop_confirmed")


def _frames(seconds, fps):
    return max(1, int(round(seconds * fps)))


def build_actions(predictions, params=None):
    """把预测候选变成可调度的镜头动作，保留排名与两个时间窗。"""
    fixed = {**DEFAULT_SCHEDULER_PARAMS, **(params or {})}
    actions = []
    for rank, pred in enumerate(predictions, start=1):
        start, end = pred["estimated_window"]
        search_start, search_end = pred.get("search_window", (start, end))
        actions.append({
            "action_id": "%s#%d" % (pred["camera"], rank),
            "camera": pred["camera"],
            "rank": rank,
            "priority": pred.get("priority", "medium"),
            "estimated_window": (start, end),
            "search_window": (search_start, search_end),
            "cursor": start,
            "state": "planned",
            "segments": 0,
            "planned_frames": _frames(end - start, fixed["fps"]),
            "uncertainties": list(pred.get("uncertainties", [])),
            "exploratory": bool(pred.get("exploratory", True)),
        })
    return actions


class CameraScheduler:
    """可恢复的串行调度器；同一时刻只推进一个摄像头的下一段。"""

    def __init__(self, predictions, recording_index=None, params=None, mode="segmented"):
        fixed = {**DEFAULT_SCHEDULER_PARAMS, **(params or {})}
        if mode not in fixed["modes"]:
            raise ValueError("invalid_mode")
        self.params = fixed
        self.mode = mode
        self.actions = build_actions(predictions, fixed)
        self.recording_index = recording_index or (lambda camera, start, end: {"available": True})
        self.cursor = 0
        self.current = None
        self.stopped = None
        self.ledger = {"planned_frames": sum(a["planned_frames"] for a in self.actions),
                       "planned_frames_all_candidates": sum(a["planned_frames"] for a in self.actions),
                       "decoded_frames": 0, "segments": 0, "cameras_visited": 0,
                       "reid_calls": 0, "vlm_calls": 0, "review_count": 0}

    # ---- 调度 ----
    def _availability(self, action, window):
        try:
            return self.recording_index(action["camera"], window[0], window[1])
        except Exception:
            return {"available": False, "reason": "recording_index_error"}

    def next_action(self):
        """返回下一段要分析的窗口；None 表示本轮无可执行动作。"""
        if self.stopped:
            return None
        while self.cursor < len(self.actions):
            action = self.actions[self.cursor]
            if action["state"] in ("needs_review", "hop_confirmed", "deferred"):
                self.cursor += 1
                continue
            if action["state"] in ("no_valid_observation", "detection_missed",
                                   "recording_unavailable"):
                self.cursor += 1
                continue
            if action["state"] == "planned":
                window = action["estimated_window"]
                info = self._availability(action, window)
                if not info.get("available", False):
                    action["state"] = "recording_unavailable"
                    action["reason"] = info.get("reason", "recording_unavailable")
                    self.cursor += 1
                    continue
                self.ledger["cameras_visited"] += 1
            step = self.params["segment_seconds"] if self.mode != "serial" else action["estimated_window"][1] - action["estimated_window"][0]
            start = action["cursor"]
            end = min(action["cursor"] + step, action["estimated_window"][1])
            if self.mode == "batch":
                end = action["search_window"][1]
            if start >= end:
                action["state"] = "no_valid_observation"
                self.cursor += 1
                continue
            if self.ledger["decoded_frames"] + _frames(end - start, self.params["fps"]) > self.params["max_decoded_frames"]:
                action["state"] = "budget_exhausted"
                self.stopped = "budget_exhausted"
                return None
            action["state"] = "running"
            self.current = {"action": action, "window": (start, end)}
            return self.current

    def submit(self, result, appearance_score=None):
        """登记一次窗口分析结果。外观分再高也**不**自动确认。"""
        if self.current is None:
            raise ValueError("no_running_action")
        action = self.current["action"]
        start, end = self.current["window"]
        frames = _frames(end - start, self.params["fps"])
        self.ledger["decoded_frames"] += frames
        self.ledger["segments"] += 1
        action["segments"] += 1
        action["cursor"] = end
        status = result.get("status")
        if status == "ok" and result.get("tracks"):
            action["state"] = "needs_review"
            action["candidates"] = [t.get("track_id") for t in result["tracks"]]
            action["appearance_score"] = appearance_score
            self.ledger["review_count"] += 1
        elif status == "detection_missed":
            action["last_result"] = "detection_missed"
            action["state"] = "running" if self._can_expand(action) else "detection_missed"
        elif status == "no_frames":
            action["state"] = "no_valid_observation"
            action["reason"] = result.get("reason", "no_frames")
        else:
            action["state"] = "no_valid_observation"
        self.current = None
        return action["state"]

    def _can_expand(self, action):
        if not self.params["allow_expand"] or self.mode == "batch":
            return action["cursor"] < action["estimated_window"][1]
        if action["cursor"] < action["estimated_window"][1]:
            return True
        if action["cursor"] < action["search_window"][1]:
            action["estimated_window"] = (action["estimated_window"][0], action["search_window"][1])
            action["expanded"] = True
            return True
        return False

    def defer(self, reason="deferred_by_operator"):
        if self.current is None:
            raise ValueError("no_running_action")
        self.current["action"]["state"] = "deferred"
        self.current["action"]["reason"] = reason
        self.current = None
        self.stopped = reason

    def confirm_hop(self, action_id=None, human=True):
        """只有人工确认才结束本轮；OSNet/VLM 一致不构成确认。"""
        if not human:
            raise ValueError("confirmation_requires_human")
        target = None
        for action in self.actions:
            if action_id is None or action["action_id"] == action_id:
                if action["state"] == "needs_review":
                    target = action
                    break
        if target is None:
            raise ValueError("no_candidate_to_confirm")
        target["state"] = "hop_confirmed"
        self.stopped = "hop_confirmed"
        return target

    def resume(self):
        self.stopped = None
        return self.next_action()

    def status(self):
        return {"mode": self.mode, "stopped": self.stopped,
                "actions": [{k: a[k] for k in ("action_id", "camera", "rank", "state")}
                            for a in self.actions],
                "ledger": dict(self.ledger)}
