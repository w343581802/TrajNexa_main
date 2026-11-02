"""M6-S4-4B：A/B/C 三策略的关键画面评测。

  python scripts/p4s4b_evaluate.py --labels design/assets/m6_s4_4b_labels_track41.json \
      --strategies /work/mk/s4_4a.json --key-frames 00201,00204 --out /work/mk/s4_4b.json

三组使用**同一批候选帧、同样数量、同样的时间去重规则**，只是排序依据不同。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.m6_evidence_label import (key_evidence_found,  # noqa: E402
                                   top_k_hit_rate, validate_labels)

STRATEGIES = (("A_uniform", "baseline_uniform"),
              ("B_size_only", "baseline_size_only"),
              ("C_composite", "picked"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", required=True)
    parser.add_argument("--strategies", required=True)
    parser.add_argument("--key-frames", required=True, help="逗号分隔的关键证据帧")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    labels = payload["labels"]
    errors = validate_labels(labels, task=payload.get("task"))
    if errors:
        raise SystemExit("标注不合规：\n" + "\n".join(errors))

    report = json.loads(Path(args.strategies).read_text(encoding="utf-8"))
    keys = [k.strip() for k in args.key_frames.split(",") if k.strip()]

    results = {}
    for name, field in STRATEGIES:
        picks = [item["frame_id"] for item in report[field]]
        hit = top_k_hit_rate(labels, picks)
        found = key_evidence_found(labels, picks, keys)
        results[name] = {"picks": picks, "top_k": hit, "key_evidence": found}

    print("%-13s %-38s 可用 边缘 未标  命中率   Any  All  召回" % ("策略", "推荐帧"))
    for name, _ in STRATEGIES:
        item = results[name]
        print("%-13s %-38s %4d %4d %4d  %5.0f%%   %3s  %3s  %4.0f%%" % (
            name, ",".join(item["picks"]), item["top_k"]["usable"],
            item["top_k"]["marginal"], item["top_k"]["unlabeled"],
            item["top_k"]["usable_rate"] * 100,
            "是" if item["key_evidence"]["any_hit"] else "否",
            "是" if item["key_evidence"]["all_hit"] else "否",
            item["key_evidence"]["key_recall"] * 100))

    Path(args.out).write_text(json.dumps(
        {"labels": args.labels, "key_frames": keys, "key_evidence_source":
         "人工标注的清晰大脸帧；全片段真值尚未全量标注", "results": results},
        ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
