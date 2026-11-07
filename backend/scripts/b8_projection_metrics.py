"""生成 B8-S §6.2 投影指标报告。

用法（在 `TrajNexa_main/backend` 下）：

    python scripts/b8_projection_metrics.py --case <case_id> --run <run_id>
    python scripts/b8_projection_metrics.py --case <case_id> --run <run_id> \
        --truth data/truth/pilot_camera_truth.json --out data/metrics/run_x.json

默认从本机 api（`http://127.0.0.1:8010`）读 `/track-segments`，不直连数据库，
这样报告与前端看到的是同一份数据。真值集是可选的：没有就只报覆盖率类指标。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import m6_projection_metrics as metrics  # noqa: E402


def fetch_envelope(base_url: str, case_id: str, run_id: str, timeout: float = 30.0) -> dict:
    url = f"{base_url.rstrip('/')}/api/v1/cases/{case_id}/tracking-runs/{run_id}/track-segments"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"track_segments_http_error:{exc.code}:{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"api_unreachable:{exc.reason}") from exc
    except json.JSONDecodeError as exc:  # pragma: no cover - 防御性
        raise SystemExit(f"api_bad_json:{exc}") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="B8-S projection metrics report")
    parser.add_argument("--case", required=True, help="case id")
    parser.add_argument("--run", required=True, help="tracking run id")
    parser.add_argument("--api", default=os.environ.get("TRAJNEXA_API", "http://127.0.0.1:8010"))
    parser.add_argument("--truth", default="", help="optional truth set json")
    parser.add_argument("--out", default="", help="report path (default backend/data/metrics/<run>.json)")
    parser.add_argument("--time-tolerance", type=float, default=5.0)
    parser.add_argument("--max-distance", type=float, default=100.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    envelope = fetch_envelope(args.api, args.case, args.run)

    truth = None
    if args.truth:
        payload = json.loads(Path(args.truth).read_text(encoding="utf-8"))
        try:
            truth = metrics.load_truth(payload)
        except metrics.TruthSetError as exc:
            raise SystemExit(f"truth_set_invalid:{exc}") from exc

    report = metrics.build_report(
        envelope,
        truth=truth,
        run_id=args.run,
        case_id=args.case,
        time_tolerance_s=args.time_tolerance,
        max_distance_m=args.max_distance,
    )

    out_path = Path(args.out) if args.out else BACKEND_ROOT / "data" / "metrics" / f"projection_{args.run[:8]}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.quiet:
        points = report["overall"]["points"]
        print(f"report: {out_path}")
        print(f"points total={points['total']} experimental={points['experimental']} degraded={points['degraded']}")
        print(f"uncertainty p90={report['overall']['uncertainty_radius_m']['p90']}")
        if report["truth"]:
            print(
                "truth matched={matched} unmatched={unmatched} median={m} p90={p}".format(
                    matched=report["truth"]["matched"],
                    unmatched=report["truth"]["unmatched"],
                    m=report["truth"]["error_m"]["median"],
                    p=report["truth"]["error_m"]["p90"],
                )
            )
        else:
            print("truth: none (只报覆盖率类指标)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())