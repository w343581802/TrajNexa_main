"""生成镜头参数缺口报告（B8-S 射线投影可用性台账）。

用法（在 `TrajNexa_main/backend` 下）：

    python scripts/b8_camera_parameter_gaps.py
    python scripts/b8_camera_parameter_gaps.py --out data/metrics/camera_parameter_gaps.json

数据取自本机 api 的 `GET /api/v1/spatial/cameras`（与前端地图同一份快照），
逐台列出「射线投影要什么参数、现在缺哪个、补齐前退化到哪一级」。

刻意不做的事：不给缺失参数填默认值、不按机型猜 FOV。缺的就是台账外场补录，
填 0 或 60 会让投影看起来「通了」，实际把误差藏进结果里。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# 射线投影（路线 A）真正需要的四个数：位置由 Map 提供，这四个来自姿态标定。
REQUIRED_FIELDS = ("bearing_deg", "tilt_deg", "mount_height_m", "horizontal_fov_deg")

FIELD_LABELS = {
    "bearing_deg": "方位角",
    "tilt_deg": "俯角",
    "mount_height_m": "安装高度",
    "horizontal_fov_deg": "水平视场角",
}

FIX_HINTS = {
    "bearing_deg": "外场台账补录方位角（0–360°）",
    "tilt_deg": "外场台账补录俯角（0–90°）",
    "mount_height_m": "外场台账补录安装高度（米）",
    "horizontal_fov_deg": "外场台账补录水平视场角（度），不要填默认值",
}


def fetch_snapshot(base_url: str, timeout: float = 20.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/v1/spatial/cameras"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - 部署环境
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"spatial_cameras_http_error:{exc.code}:{detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover
        raise SystemExit(f"api_unreachable:{exc.reason}") from exc
    if not isinstance(payload, dict) or payload.get("status") != "available":
        status = (payload or {}).get("status") if isinstance(payload, dict) else None
        raise SystemExit(f"spatial_cameras_unavailable:{status}")
    return payload


def camera_gap(camera: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIELDS if camera.get(field) is None]
    ready = bool(camera.get("ray_projection_ready"))
    # 服务端标记与四个字段必须一致；不一致说明快照算错了，报告要显式标出来。
    mismatch = ready != (not missing)
    if ready:
        fallback = "L1_ray_csv"
    elif "horizontal_fov_deg" in missing:
        fallback = "L0_camera_position"
    else:
        fallback = "L1_ray_csv_unverified"
    return {
        "map_camera_id": str(camera.get("map_camera_id") or ""),
        "camera_uuid": camera.get("camera_uuid"),
        "display_name": camera.get("display_name") or "",
        "camera_type": camera.get("camera_type"),
        "pose_quality": camera.get("pose_quality"),
        "bearing_deg": camera.get("bearing_deg"),
        "bearing_source": camera.get("bearing_source"),
        "tilt_deg": camera.get("tilt_deg"),
        "mount_height_m": camera.get("mount_height_m"),
        "horizontal_fov_deg": camera.get("horizontal_fov_deg"),
        "range_m": camera.get("range_m"),
        "has_video": bool(camera.get("has_video")),
        "ray_projection_ready": ready,
        "missing_fields": missing,
        "missing_labels": [FIELD_LABELS[field] for field in missing],
        "fix_hints": [FIX_HINTS[field] for field in missing],
        "anchor_fallback": fallback,
        "server_flag_mismatch": mismatch,
    }


def build_report(payload: dict[str, Any], *, base_url: str) -> dict[str, Any]:
    cameras = [camera_gap(camera) for camera in (payload.get("cameras") or [])]
    cameras.sort(key=lambda row: (row["ray_projection_ready"], -len(row["missing_fields"]), row["display_name"]))
    missing_counts = {
        field: sum(1 for row in cameras if field in row["missing_fields"]) for field in REQUIRED_FIELDS
    }
    pose_quality: dict[str, int] = {}
    provenance: dict[str, int] = {}
    for camera in payload.get("cameras") or []:
        pose = str(camera.get("pose_quality") or "unknown")
        pose_quality[pose] = pose_quality.get(pose, 0) + 1
        source = str(camera.get("fov_provenance") or "unknown")
        provenance[source] = provenance.get(source, 0) + 1
    blocked = [row for row in cameras if not row["ray_projection_ready"]]
    return {
        "schema_version": "b8s-camera-parameter-gaps:1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "url": f"{base_url.rstrip('/')}/api/v1/spatial/cameras",
            "spatial_revision": payload.get("spatial_revision"),
            "counts": payload.get("counts") or {},
        },
        "policy": (
            "缺失参数不当 0、不猜 FOV：补齐前该镜头只能出镜头位置点（L0），"
            "不能出射线投影（L1）。补录来源见 design/77 §12.17。"
        ),
        "summary": {
            "cameras": len(cameras),
            "ray_projection_ready": len(cameras) - len(blocked),
            "blocked": len(blocked),
            "missing_counts": missing_counts,
            "pose_quality": pose_quality,
            "fov_provenance": provenance,
            "server_flag_mismatch": [row["map_camera_id"] for row in cameras if row["server_flag_mismatch"]],
        },
        "cameras": cameras,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成镜头参数缺口报告")
    parser.add_argument("--url", default="http://127.0.0.1:8010", help="主系统 api 地址")
    parser.add_argument("--out", default=str(BACKEND_ROOT / "data/metrics/camera_parameter_gaps.json"))
    args = parser.parse_args(argv)

    payload = fetch_snapshot(args.url)
    report = build_report(payload, base_url=args.url)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(f"cameras={summary['cameras']} ray_ready={summary['ray_projection_ready']} "
          f"blocked={summary['blocked']}")
    print("missing=" + json.dumps(summary["missing_counts"], ensure_ascii=False))
    if summary["server_flag_mismatch"]:
        print("server_flag_mismatch=" + ",".join(summary["server_flag_mismatch"]))
    print(f"written={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())