"""B4.1-C：从 runner 输出生成只读审核包。

审核包不改任何状态，只把"要人看什么"整理出来：候选轨迹、实际帧时间、证据图路径、
绑定与时钟质量、外观排序及其不确定性。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chain", required=True)
    parser.add_argument("--frames", required=True, help="帧根目录，用于校验证据存在")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    chain = json.loads(Path(args.chain).read_text(encoding="utf-8"))
    hop = chain.get("hop")
    if not hop:
        raise SystemExit("no_candidate_in_chain")
    refs = []
    for reference in hop["evidence_refs"]:
        kind, _, rel = reference.partition(":")
        refs.append({"reference": reference,
                     "resolves": kind == "frame" and (Path(args.frames) / rel).is_file()})
    bundle = {"schema": "m6-review-bundle/1",
              "case_id": "purple-rider",
              "target_id": "purple-rider",
              "candidate_id": "%s/%s" % (hop["camera"], hop["track_id"]),
              "camera_label": hop["camera"],
              "map_camera_id": hop["map_camera_id"],
              "track_id": hop["track_id"],
              "observed_start": hop["observed_start"],
              "observed_end": hop["observed_end"],
              "frame_span": [hop["first_frame"], hop["last_frame"]],
              "clock_quality": hop["clock_quality"],
              "binding_status": chain.get("binding_status", "pending_human_review"),
              "exploratory": chain.get("exploratory", True),
              "appearance": {"retrieval_score": hop["retrieval_score"],
                             "margin_top1_top2": hop.get("margin_top1_top2"),
                             "note": "仅排序依据，不是身份概率"},
              "evidence": refs,
              "decision": "",
              "reason": "",
              "reviewer": "",
              "reviewed_at": ""}
    Path(args.out).write_text(json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")
    print("bundle:", args.out)
    print("candidate:", bundle["candidate_id"], "frames", bundle["frame_span"])
    print("evidence resolvable:", [r["resolves"] for r in refs])
    print("reviewer 留空：必须由真实审核人填写")


if __name__ == "__main__":
    main()
