"""TB4：目标绑定与单镜头空间轨迹接口（实验估计）。"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app import m6_target_binding
from app.b8_gates import require_experimental_geo_display

router = APIRouter(prefix="/api/v1", tags=["tb4-target-track"],
                   dependencies=[Depends(require_experimental_geo_display)])

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "tb_samples" / "pilot_tb4_target.json"


@router.get("/target-track/sample")
def target_track_sample(variant: str = Query("bound")) -> dict:
    """从定向追踪候选片段里，按 anchor 绑定一条目标轨迹并投影为实验估计点。"""
    if not SAMPLE_PATH.exists():
        raise HTTPException(status_code=404, detail="target_sample_unavailable")
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    synthetic = False
    if variant == "ambiguity-probe":
        # 只为演示歧义呈现：把同一条轨迹复制一份并微移 1px，IoU 几乎相同。
        # 这不是真实候选，响应里标 synthetic=true，不得用于任何结论。
        probe = json.loads(json.dumps(payload["tracklets"][0]))
        probe["track_id"] = probe["track_id"] + "-probe"
        for frame in probe["frames"]:
            frame["box"] = [v + 1.0 for v in frame["box"]]
        payload["tracklets"] = payload["tracklets"] + [probe]
        synthetic = True
    report = m6_target_binding.build_track(
        payload["anchor"], payload["tracklets"], payload["camera"], kind="person",
        evidence={"clip": payload["clip"], "fps": payload["fps"],
                  "method": payload["method"], "window_start": payload["window_start"]},
        playback={"clip_id": payload.get("clip_id"), "case_id": payload.get("case_id"),
                  "clip_start_local": payload.get("clip_start_local"),
                  "start_of_day_seconds": payload.get("start_of_day_seconds"),
                  "time_basis": payload.get("time_basis"),
                  "time_quality": payload.get("time_quality"),
                  "time_note": payload.get("time_note")},
    )
    report["sample"] = {k: payload.get(k) for k in
                        ("clip", "fps", "width", "height", "window_start", "window_end", "method",
                         "clip_id", "case_id", "clip_start_local")}
    report["anchor"] = payload["anchor"]
    report["synthetic"] = synthetic
    report["variant"] = variant
    return report
