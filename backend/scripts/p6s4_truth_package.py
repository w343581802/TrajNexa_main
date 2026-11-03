"""S6-15V：真值包自检（只读）。

用法：
    python scripts/p6s4_truth_package.py design/assets/m6_s6_15_truth_package.json

退出码 1 表示这份真值包不能用于宣称结论（有问题项）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m6_truth_registry import validate_package  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package")
    args = parser.parse_args()
    package = json.loads(Path(args.package).read_text(encoding="utf-8"))
    registry = {"case_id": package["case_id"], "observations": package["observations"]}
    problems = validate_package(registry)
    print("case:", package["case_id"])
    print("exploratory:", package.get("exploratory"))
    print("confirmed chain:", [(r["tracklet_id"], r["osd_anchor_seconds"])
                               for r in package.get("confirmed_chain", [])])
    print("observations:", len(package["observations"]))
    print("problems:", problems or "none")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
