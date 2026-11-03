"""S6-15D：录像分析调度器（串行 / 小批次，可恢复）。

职责边界：预测层给候选与时间窗 → 本模块决定"下一次分析哪台、哪一段"
→ 调 m6_window_analysis 取观测 → m6_target_candidate_adapter 判候选
→ 只有人工审核事件才能确认。

关键语义（design/47 §2 P0-A/P0-B）：
  - "窗口里检测到人" ≠ "找到指定目标"：没有 target_candidates 只能继续查；
  - 计划帧数（估算）与实测帧数（执行返回）分开记账；
  - 录像可用性 unknown 与 unavailable 分开，未知不等于不存在；
  - 撤销/否决后恢复搜索，不把未查区间当"目标没经过"。
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
          "no_target_candidate", "candidate_rejected", "no_valid_observation",
          "detection_missed", "recording_unavailable", "recording_unknown",
          "budget_exhausted", "deferred", "hop_confirmed")

SKIPPABLE = ("no_valid_observation", "detection_missed", "recording_unavailable",
             "recording_unknown", "cancelled_derived")


def _frames(seconds, fps):
    return max(1, int(round(seconds * fps)))


def _clip_to_ranges(start, end, ranges):
    """只分析确实有录像的区间；返回 None 表示这段完全没有可用录像。"""
    best = None
    for r_start, r_end in ranges:
        lo, hi = max(start, r_start), min(end, r_end)
        if hi - lo <= 0:
            continue
        if best is None or hi - lo > best[1] - best[0]:
            best = (lo, hi)
    return best


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
            "review_state": "none",
            "rejected_tracks": [],
            "segments": 0,
            "estimated_frames": _frames(end - start, fixed["fps"]),
            "measured_frames": 0,
            "detector_frames": 0,
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
        self.recording_index = recording_index or (lambda camera, start, end: {"available": None})
        self.cursor = 0
        self.current = None
        self.stopped = None
        self.ledger = {"planned_frames": sum(a["estimated_frames"] for a in self.actions),
                       "estimated_frames_run": 0,
                       "measured_frames": 0,
                       "detector_frames": 0,
                       "segments": 0, "cameras_visited": 0,
                       "reid_calls": 0, "vlm_calls": 0, "review_count": 0,
                       "simulated_reviews": 0}

    # ---- 调度 ----
    def _availability(self, action, window):
        try:
            return self.recording_index(action["camera"], window[0], window[1])
        except Exception:
            return {"available": None, "reason": "recording_index_error"}

    def next_action(self):
        """返回下一段要分析的窗口；None 表示本轮无可执行动作。"""
        if self.stopped:
            return None
        while self.cursor < len(self.actions):
            action = self.actions[self.cursor]
            if action["state"] in ("needs_review", "hop_confirmed", "deferred"):
                self.cursor += 1
                continue
            if action["state"] in SKIPPABLE:
                self.cursor += 1
                continue
            if action["state"] == "planned":
                info = self._availability(action, action["estimated_window"])
                if info.get("available") is False:
                    action["state"] = "recording_unavailable"
                    action["reason"] = info.get("reason", "recording_unavailable")
                    self.cursor += 1
                    continue
                if info.get("available") is None:
                    action["state"] = "recording_unknown"
                    action["reason"] = info.get("reason", "recording_unknown")
                    self.cursor += 1
                    continue
                action["available_ranges"] = info.get("ranges")
                self.ledger["cameras_visited"] += 1
            step = (action["estimated_window"][1] - action["estimated_window"][0]
                    if self.mode == "serial" else self.params["segment_seconds"])
            start = action["cursor"]
            end = min(action["cursor"] + step, action["estimated_window"][1])
            if self.mode == "batch":
                end = action["search_window"][1]
            ranges = action.get("available_ranges")
            if ranges:
                clipped = _clip_to_ranges(start, end, ranges)
                if clipped is None:
                    action["cursor"] = end
                    action["partial_recording"] = True
                    continue
                start, end = clipped
            if start >= end:
                action["state"] = "no_valid_observation"
                self.cursor += 1
                continue
            estimate = _frames(end - start, self.params["fps"])
            if self.ledger["estimated_frames_run"] + estimate > self.params["max_decoded_frames"]:
                action["state"] = "budget_exhausted"
                self.stopped = "budget_exhausted"
                return None
            action["state"] = "running"
            self.current = {"action": action, "window": (start, end)}
            return self.current

    def submit(self, result, appearance_score=None):
        """登记一次窗口分析结果。只有 target_candidates 才进入复核。"""
        if self.current is None:
            raise ValueError("no_running_action")
        action = self.current["action"]
        start, end = self.current["window"]
        cost = result.get("cost") or {}
        estimate = _frames(end - start, self.params["fps"])
        measured = int(cost.get("frames_decoded") or 0)
        self.ledger["estimated_frames_run"] += estimate
        self.ledger["measured_frames"] += measured
        self.ledger["detector_frames"] += int(cost.get("detector_frames") or 0)
        self.ledger["segments"] += 1
        action["measured_frames"] += measured
        action["detector_frames"] += int(cost.get("detector_frames") or 0)
        action["segments"] += 1
        action["cursor"] = end

        status = result.get("status")
        candidates = [c for c in (result.get("target_candidates") or [])
                      if c.get("track_id") not in action["rejected_tracks"]]
        strong = [c for c in candidates if c.get("state") != "weak_candidate"]
        weak = [c for c in candidates if c.get("state") == "weak_candidate"]
        if status == "ok" and not strong and weak:
            action["state"] = "no_target_candidate"
            action["weak_candidates"] = [c.get("track_id") for c in weak]
            if self._can_advance(action):
                action["state"] = "running"
        elif status == "ok" and strong:
            action["state"] = "needs_review"
            action["candidates"] = [c.get("track_id") for c in candidates]
            action["appearance_score"] = appearance_score
            self.ledger["review_count"] += 1
            if result.get("time_status") == "relative_only":
                action.setdefault("uncertainties", []).append("time_mapping_missing")
        elif status == "ok" and result.get("tracks"):
            # 有人但没有目标候选：继续查，不算发现
            action["state"] = "no_target_candidate"
            action["tracks_seen"] = len(result["tracks"])
            if self._can_advance(action):
                action["state"] = "running"
        elif status == "detection_missed":
            action["last_result"] = "detection_missed"
            action["state"] = "running" if self._can_advance(action) else "detection_missed"
        elif status == "no_frames":
            action["state"] = "no_valid_observation"
            action["reason"] = result.get("reason", "no_frames")
        else:
            action["state"] = "no_valid_observation"
        self.current = None
        return action["state"]

    def _can_advance(self, action):
        if action["cursor"] < action["estimated_window"][1]:
            return True
        if not self.params["allow_expand"] or self.mode == "batch":
            return False
        if action["cursor"] < action["search_window"][1]:
            action["estimated_window"] = (action["estimated_window"][0], action["search_window"][1])
            action["expanded"] = True
            return True
        return False

    def reject(self, action_id=None, candidate_id=None, reason="human_rejected"):
        """否决审核：给了 candidate_id 就只否这一条，同窗口其他候选继续待审。"""
        target = self._find(action_id)
        if target is None or target["state"] != "needs_review":
            raise ValueError("no_candidate_to_reject")
        remaining = list(target.get("candidates") or [])
        if candidate_id is not None:
            if candidate_id not in remaining:
                raise ValueError("candidate_not_pending_review")
            target["rejected_tracks"].append(candidate_id)
            target["candidates"] = [c for c in remaining if c != candidate_id]
            target["reason"] = reason
            if target["candidates"]:
                target["review_state"] = "pending"
                self.stopped = None
                return target
        else:
            target["rejected_tracks"].extend(remaining)
            target["candidates"] = []
        target["review_state"] = "rejected"
        target["reason"] = reason
        target["state"] = "running" if self._can_advance(target) else "no_valid_observation"
        self.stopped = None
        self.cursor = self.actions.index(target)
        return target

    def confirm_candidate(self, candidate_id, action_id=None, evidence=None):
        """只确认指定候选；同窗口其他候选保持候选态，不被顺带确认。"""
        target = self._find(action_id)
        if target is None or target["state"] != "needs_review":
            raise ValueError("no_candidate_to_confirm")
        if candidate_id not in (target.get("candidates") or []):
            raise ValueError("candidate_not_pending_review")
        target["confirmed_track"] = candidate_id
        target["review_state"] = "confirmed"
        target["state"] = "hop_confirmed"
        target["evidence_refs"] = list(evidence or [])
        self.stopped = None
        self.cursor = self.actions.index(target) + 1
        return target

    def boost_cameras(self, cameras, reason="quality_feedback_complementary_view"):
        """把指定镜头的待查动作前移；不删除任何动作，也不改窗口。"""
        cameras = set(cameras)
        if not cameras:
            return []
        head, tail = [], []
        for action in self.actions:
            if action["camera"] in cameras and action["state"] in ("planned", "segmented"):
                action["boosted"] = True
                action["boost_reason"] = reason
                head.append(action)
            else:
                tail.append(action)
        self.actions = head + tail
        return [a["action_id"] for a in head]

    def cancel_actions(self, action_ids, reason="derived_from_revoked"):
        """撤销上游后，把它派生的调度动作一起作废；不影响其它动作。"""
        cancelled = []
        ids = set(action_ids)
        for action in self.actions:
            if action["action_id"] in ids and action["state"] not in ("hop_confirmed",):
                action["state"] = "cancelled_derived"
                action["reason"] = reason
                cancelled.append(action["action_id"])
        if self.current and self.current["action"]["action_id"] in ids:
            self.current = None
        return cancelled

    def snapshot(self):
        """可持久化的任务进度：镜头游标、预算、已处理窗口与审核态。"""
        return {"mode": self.mode, "cursor": self.cursor, "stopped": self.stopped,
                "ledger": dict(self.ledger),
                "actions": [dict(action) for action in self.actions]}

    @classmethod
    def restore(cls, snapshot, recording_index=None, predictions=None, params=None):
        if predictions is None:
            predictions = [{"camera": action["camera"], "priority": action["priority"],
                            "estimated_window": action["estimated_window"],
                            "search_window": action["search_window"],
                            "uncertainties": action.get("uncertainties", []),
                            "exploratory": action.get("exploratory", True)}
                           for action in snapshot["actions"]]
        session = cls(predictions, recording_index=recording_index, params=params,
                      mode=snapshot.get("mode", "segmented"))
        session.actions = [dict(action) for action in snapshot["actions"]]
        session.cursor = snapshot["cursor"]
        session.stopped = snapshot.get("stopped")
        session.ledger = dict(snapshot["ledger"])
        session.current = None
        return session

    def _find(self, action_id):
        for action in self.actions:
            if action_id is None and action["state"] == "needs_review":
                return action
            if action_id is not None and action["action_id"] == action_id:
                return action
        return None

    def defer(self, reason="deferred_by_operator"):
        if self.current is None:
            raise ValueError("no_running_action")
        self.current["action"]["state"] = "deferred"
        self.current["action"]["reason"] = reason
        self.current = None
        self.stopped = reason

    def confirm_hop(self, action_id=None, human=True, evidence=None, simulated=False):
        """人工确认必须带证据引用；研发模拟需显式 simulated=True 并单独记账。"""
        if not human:
            raise ValueError("confirmation_requires_human")
        if not simulated and not evidence:
            raise ValueError("confirmation_requires_evidence")
        target = self._find(action_id)
        if target is None or target["state"] != "needs_review":
            raise ValueError("no_candidate_to_confirm")
        target["state"] = "hop_confirmed"
        target["review_state"] = "confirmed"
        target["simulated_review"] = bool(simulated)
        target["evidence_refs"] = list(evidence or [])
        if simulated:
            self.ledger["simulated_reviews"] += 1
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
