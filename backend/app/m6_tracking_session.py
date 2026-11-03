"""S6-16A / B5-A：多跳目标追踪状态机。

不变式：
  1. 主链只接受**人工审核通过**的观测；
  2. 未确认候选（含 weak_candidate）留在候选池，不进入主链、也不被丢弃；
  3. 任何派生候选都记 depends_on，撤销上游时级联失效；
  4. 预算耗尽 / 无候选时状态为 unresolved 或 budget_exhausted，绝不等于"目标不存在"。
"""
from __future__ import annotations

from .m6_review_events import ReviewEventError, validate_event

ACTIVE = ("candidate", "weak_candidate")


class TrackingSession:
    def __init__(self, case_id, target_id, anchor):
        self.case_id = case_id
        self.target_id = target_id
        self.main_chain = [dict(anchor, role="A")]
        self.candidates = []
        self.derived_actions = []
        self.status = "running"
        self.budget_hit = False
        self.history = [{"event": "session_started", "anchor": anchor.get("observation_id")}]
        self.plan = None

    # ---- 候选 ----
    def observe(self, candidates, from_anchor_id):
        """登记一批候选；weak 与 strong 都保留，只区分优先级。"""
        added = []
        for item in candidates:
            entry = {"candidate_id": item["candidate_id"],
                     "camera": item.get("camera_label") or item.get("camera"),
                     "score": item.get("retrieval_score"),
                     "margin": item.get("margin_top1_top2"),
                     "state": item.get("state", "candidate"),
                     "observed_start": item.get("observed_start"),
                     "observed_end": item.get("observed_end"),
                     "evidence": list(item.get("evidence_refs") or item.get("evidence") or []),
                     "depends_on": from_anchor_id,
                     "role": "branch"}
            self.candidates.append(entry)
            added.append(entry)
        self.history.append({"event": "candidates_observed", "count": len(added),
                             "from": from_anchor_id})
        return added

    def register_actions(self, anchor_id, action_ids):
        """登记由某个已确认（或假设）锚点派生的调度动作，撤销时一起失效。"""
        for action_id in action_ids:
            self.derived_actions.append({"action_id": action_id, "depends_on": anchor_id,
                                         "state": "scheduled"})
        self.history.append({"event": "actions_registered", "from": anchor_id,
                             "count": len(action_ids)})
        return action_ids

    def pending(self):
        return [c for c in self.candidates if c["state"] in ACTIVE]

    def best(self):
        ranked = sorted(self.pending(), key=lambda c: ((c["score"] or 0), (c["margin"] or 0)),
                        reverse=True)
        return ranked[0] if ranked else None

    def _candidate(self, candidate_id):
        for item in self.candidates:
            if item["candidate_id"] == candidate_id:
                return item
        raise KeyError(candidate_id)

    def confirm(self, candidate_id, event):
        validate_event(event, require_human=True)
        if event.get("event_type") != "confirm":
            raise ReviewEventError("not_a_confirm_event")
        item = self._candidate(candidate_id)
        item["state"] = "human_confirmed"
        self.main_chain.append({"observation_id": item["candidate_id"], "role": "B",
                                "camera": item["camera"], "observed_start": item["observed_start"],
                                "observed_end": item["observed_end"], "reviewer": event["reviewer"],
                                "evidence": item["evidence"], "depends_on": item["depends_on"]})
        self.status = "running"
        self.history.append({"event": "confirmed", "candidate": candidate_id,
                             "reviewer": event["reviewer"]})
        return self.main_chain[-1]

    def reject(self, candidate_id, event):
        validate_event(event, require_human=True)
        if event.get("event_type") != "reject":
            raise ReviewEventError("not_a_reject_event")
        item = self._candidate(candidate_id)
        item["state"] = "human_rejected"
        item["reject_reason"] = event.get("reason")
        self.status = "running" if self.pending() else "unresolved"
        self.history.append({"event": "rejected", "candidate": candidate_id})
        return item

    def undecided(self, candidate_id, event):
        item = self._candidate(candidate_id)
        item["state"] = "candidate"
        item["review_note"] = event.get("reason")
        self.history.append({"event": "undecided", "candidate": candidate_id})
        return item

    # ---- 计划与状态 ----
    def plan_next(self, spatial_fn):
        anchor = self.main_chain[-1]
        result = spatial_fn(anchor)
        self.plan = {"from": anchor["observation_id"], "result": result}
        if not result.get("top5"):
            self.status = "unresolved"
        self.history.append({"event": "planned", "from": anchor["observation_id"]})
        return result

    def budget_exhausted(self):
        self.status = "budget_exhausted"
        self.history.append({"event": "budget_exhausted"})

    def pause(self, reason="paused"):
        self.status = "paused"
        self.history.append({"event": "paused", "reason": reason})

    def resume(self):
        self.status = "running"
        self.history.append({"event": "resumed"})

    # ---- 撤销 ----
    def revoke(self, observation_id, reason="revoked"):
        revoked = {observation_id}
        changed = True
        while changed:
            changed = False
            for item in self.candidates:
                if item["depends_on"] in revoked and item["state"] != "revoked":
                    item["state"] = "revoked"
                    revoked.add(item["candidate_id"])
                    changed = True
        self.main_chain = [entry for entry in self.main_chain
                           if entry["observation_id"] not in revoked]
        cancelled = []
        for action in self.derived_actions:
            if action["depends_on"] in revoked and action["state"] == "scheduled":
                action["state"] = "cancelled_derived"
                cancelled.append(action["action_id"])
        self.status = "running" if self.pending() else "unresolved"
        self.history.append({"event": "revoked", "observation": observation_id,
                             "cascade": sorted(revoked), "actions": cancelled,
                             "reason": reason})
        return {"observations": sorted(revoked), "actions": cancelled}

    def settle(self, analyzed_windows=(), remaining_actions=None):
        """收敛本轮状态：有候选待审、预算不足、还是搜完未确认。"""
        if self.budget_hit or (remaining_actions or 0) > 0:
            self.status = "budget_exhausted"
            reason = "analysis_budget_exhausted_with_pending_review" if self.pending() \
                else "analysis_budget_exhausted"
        elif self.pending():
            self.status = "awaiting_review"
            reason = "candidates_waiting_for_human_review"
        else:
            self.status = "unresolved"
            reason = "all_available_windows_analyzed_without_confirmation"
        self.plan = {"analyzed_windows": [list(w) for w in analyzed_windows],
                     "remaining_actions": remaining_actions or 0,
                     "reason": reason}
        self.history.append({"event": "settled", "status": self.status, "reason": reason})
        return {"status": self.status, "reason": reason}

    def summary(self):
        return {"case_id": self.case_id, "target_id": self.target_id,
                "status": self.status,
                "reason": (self.plan or {}).get("reason"),
                "main_chain": [n["observation_id"] for n in self.main_chain],
                "analyzed_windows": (self.plan or {}).get("analyzed_windows") or [],
                "remaining_actions": (self.plan or {}).get("remaining_actions", 0),
                "candidates": [{"id": c["candidate_id"], "state": c["state"],
                                "score": c["score"], "depends_on": c["depends_on"]}
                               for c in self.candidates],
                "derived_actions": [dict(a) for a in self.derived_actions],
                "history": self.history[-20:]}
