"""TB4：受案件约束的录像片段读取（支持 HTTP Range），供播放器跳转到指定偏移。

原片可能是 HEVC（浏览器多不能解码），因此另有 /proxy-video：按偏移转成 H.264
代理片段再下发。两种响应都带时间语义头，说明 PTS 未经 OSD 独立核验。
"""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import m4_video, m6_clip_proxy
from app.db import get_db
from app.m4_video_api import enabled, fail

router = APIRouter(prefix="/api/v1", tags=["tb4-clip-stream"])

CHUNK = 1024 * 256
MAX_OPEN_RANGE = 4 * 1024 * 1024  # 无上限的 Range 只发一段，浏览器会继续请求，避免单次 600MB 卡住
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _video_mime(path: Path) -> str:
    return "video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream"


def _serve(path: Path, request: Request, semantics: str) -> Response:
    """按 Range 下发本地文件；只回字节与语义头，绝不返回服务器路径。"""
    size = path.stat().st_size
    mime = _video_mime(path)
    header = request.headers.get("range") or ""
    match = RANGE_RE.match(header)
    if not match:
        return Response(path.read_bytes(), media_type=mime, headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "X-Clip-Time-Semantics": semantics,
        })
    start_raw, end_raw = match.group(1), match.group(2)
    start = int(start_raw) if start_raw else 0
    if end_raw:
        end = int(end_raw)
    else:
        end = min(start + MAX_OPEN_RANGE - 1, size - 1)
    end = min(end, size - 1)
    if start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})

    def chunks():
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                block = handle.read(min(CHUNK, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block

    return StreamingResponse(chunks(), status_code=206, media_type=mime, headers={
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(end - start + 1),
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "X-Clip-Time-Semantics": semantics,
    })


def _clip_path(db: Session, case_id: str, clip_id: str) -> Path:
    try:
        clip, path = m4_video.resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
    except m4_video.KeyframeError as exc:
        fail(404, "clip-not-found", str(exc))
    if not path.exists():
        fail(404, "clip-file-missing", "该片段文件不存在。")
    return path


@router.get("/cases/{case_id}/recordings/clips/{clip_id}/video")
def stream_clip_video(case_id: str, clip_id: str, request: Request,
                      db: Session = Depends(get_db)):
    """原始片段（可能是 HEVC）：只按案件+片段标识读字节，绝不返回本地路径。"""
    enabled()
    return _serve(_clip_path(db, case_id, clip_id), request,
                  "container-pts-not-verified-against-osd")


@router.get("/cases/{case_id}/recordings/clips/{clip_id}/proxy-video")
def stream_proxy_video(case_id: str, clip_id: str, request: Request,
                       start: float = Query(0.0, ge=0.0),
                       duration: float = Query(20.0, gt=0.0),
                       db: Session = Depends(get_db)):
    """H.264 代理片段：源片 HEVC 时供浏览器播放。

    start/duration 是相对片段起点的秒数；代理是重编码副本，不是原片，
    时间语义头明确写 proxy-h264-reencoded，PTS 仍未与 OSD 独立核验。
    """
    enabled()
    source = _clip_path(db, case_id, clip_id)
    try:
        proxy = m6_clip_proxy.build_proxy(source, clip_id=clip_id, start=start, duration=duration)
    except m6_clip_proxy.ProxyError as exc:
        fail(503, str(exc).replace("_", "-"),
             "代理片段生成失败：源片段可能无法解码，或 ffmpeg 不可用。")
    response = _serve(proxy, request, "proxy-h264-reencoded-not-verified-against-osd")
    response.headers["X-Proxy-Start-Seconds"] = f"{max(0.0, float(start)):.2f}"
    return response
