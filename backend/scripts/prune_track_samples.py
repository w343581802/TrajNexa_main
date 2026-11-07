"""按保留策略清理逐帧样本（`track_samples`）。

用法（在 `TrajNexa_main/backend` 下）：

    python scripts/prune_track_samples.py                     # 只报告，不删
    python scripts/prune_track_samples.py --apply             # 每案件保留最近 20 个任务
    python scripts/prune_track_samples.py --keep-runs 5 --apply
    python scripts/prune_track_samples.py --case <case_id> --apply

默认 `dry-run`：先看会删多少行。只删 `track_samples`，追踪任务、候选与窗口记录都保留。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import m6_track_sample_retention as retention  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按保留策略清理逐帧样本")
    parser.add_argument("--case", default=None, help="只处理这个案件")
    parser.add_argument("--keep-runs", type=int, default=retention.DEFAULT_KEEP_RUNS,
                        help="每案件保留最近多少个追踪任务（默认 20）")
    parser.add_argument("--apply", action="store_true", help="真正删除；不加则只报告")
    args = parser.parse_args(argv)

    try:
        with SessionLocal() as db:
            report = retention.prune(
                db, case_id=args.case, keep_runs=args.keep_runs, dry_run=not args.apply,
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())