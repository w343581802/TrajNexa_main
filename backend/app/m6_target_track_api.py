"""TB4：目标绑定与单镜头空间轨迹接口（实验估计）。"""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app import m6_target_binding

router = APIRouter(prefix="/api/v1", tags=["tb4-target-track"])

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "data" / "tb_samples" / "pilot_tb4_target.json"


@router.get("/target-track/sample")
def target_track_sample() -> dict:
    """从定向追踪候选片段里，按 anchor 绑定一条目标轨迹并投影为实验估计点。"""
    if not SAMPLE_PATH.exists():
        raise HTTPException(status_code=404, detail="target_sample_unavailable")
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    report = m6_target_binding.build_track(
        payload["anchor"], payload["tracklets"], payload["camera"], kind="person",
        evidence={"clip": payload["clip"], "fps": payload["fps"],
                  "method": payload["method"], "window_start": payload["window_start"]},
    )
    report["sample"] = {k: payload.get(k) for k in
                        ("clip", "fps", "width", "height", "window_start", "window_end", "method")}
    report["anchor"] = payload["anchor"]
    return report
