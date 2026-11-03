"""B4.1-C：把人工填写的审核事件应用到真值包。

事件文件是 JSON 数组，每条至少包含 event_type / candidate_id / reviewer /
reviewed_at / evidence；本工具校验后应用，任何一条被拒都会返回非零退出码。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m6_review_events import apply_events, review_event  # noqa: E402
from app.m6_truth_registry import export_package, validate_package  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--evidence-root", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    package = json.loads(Path(args.package).read_text(encoding="utf-8"))
    raw_events = json.loads(Path(args.events).read_text(encoding="utf-8"))
    registry = {"case_id": package["case_id"],
                "observations": package["observations"],
                "applied_events": package.get("applied_events", [])}
    events = []
    for item in raw_events:
        events.append(review_event(item["event_type"], item.get("case_id", package["case_id"]),
                                   item["target_id"], item["candidate_id"],
                                   item.get("reviewer"), item.get("reviewed_at"),
                                   evidence=item.get("evidence"),
                                   reason=item.get("reason"),
                                   source=item.get("source", "human_review"),
                                   event_id=item.get("event_id")))
    results = apply_events(registry, events, evidence_root=args.evidence_root)
    updated = export_package(registry, bindings=package.get("bindings", []))
    updated["problems"] = validate_package(registry, evidence_root=args.evidence_root)
    Path(args.out).write_text(json.dumps(updated, ensure_ascii=False, indent=1), encoding="utf-8")
    for result in results:
        print(result)
    failures = [r for r in results if r["status"] == "rejected"]
    print("applied:", len(results) - len(failures), "rejected:", len(failures))
    print("problems:", updated["problems"] or "none")
    return 1 if failures or updated["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
