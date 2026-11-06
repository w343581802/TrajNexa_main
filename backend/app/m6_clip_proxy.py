"""TB4：把 HEVC 原片按偏移转成浏览器可播的 H.264 小片段（代理片段，非原片）。

背景：试点片段是 hevc/yuvj420p、25 fps、容器 PTS 起点约 131410 s，
浏览器（尤其非硬件 HEVC 环境）无法直接播放；且 PTS 起点不为 0，
`ffmpeg -ss <offset> -i file` 会落在首帧之前。因此这里先用 ffprobe 取容器的
start_time，再用 (start_time + offset) 做输入侧定位。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.config import settings

PROXY_MAX_SECONDS = 120.0
PROXY_DIR_NAME = "tb4_proxy"


class ProxyError(RuntimeError):
    """Stable sanitized error code."""


def probe_start_time(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=start_time", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise ProxyError("ffprobe_failed")
    try:
        payload = json.loads(result.stdout or "{}")
        value = payload["streams"][0]["start_time"]
        return float(value)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0.0


def proxy_dir() -> Path:
    directory = Path(settings.storage_root) / PROXY_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def build_proxy(path: Path, *, clip_id: str, start: float, duration: float) -> Path:
    """按 (start, duration) 生成并缓存 H.264 代理片段；不修改原片。"""
    start = max(0.0, float(start))
    duration = min(PROXY_MAX_SECONDS, max(1.0, float(duration)))
    target = proxy_dir() / f"{clip_id}__{start:.2f}__{duration:.2f}.mp4"
    if target.exists() and target.stat().st_size > 1024:
        return target
    origin = probe_start_time(path)
    absolute = origin + start
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{absolute:.3f}", "-i", str(path),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-vf", "scale=-2:720", "-an", "-movflags", "+faststart",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not target.exists() or target.stat().st_size <= 1024:
        if target.exists():
            target.unlink(missing_ok=True)
        raise ProxyError("proxy_transcode_failed")
    return target
