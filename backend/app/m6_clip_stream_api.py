"""TB4：受案件约束的录像片段读取（支持 HTTP Range），供播放器跳转到指定偏移。"""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import m4_video
from app.db import get_db
from app.m4_video_api import enabled, fail

router = APIRouter(prefix="/api/v1", tags=["tb4-clip-stream"])

CHUNK = 1024 * 256
MAX_OPEN_RANGE = 4 * 1024 * 1024  # 无上限的 Range 只发一段，浏览器会继续请求，避免单次 600MB 卡住
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _video_mime(path: Path) -> str:
    return "video/mp4" if path.suffix.lower() == ".mp4" else "application/octet-stream"


@router.get("/cases/{case_id}/recordings/clips/{clip_id}/video")
def stream_clip_video(case_id: str, clip_id: str, request: Request,
                      db: Session = Depends(get_db)):
    """只按案件+片段标识读字节，绝不返回服务器本地路径。"""
    enabled()
    try:
        clip, path = m4_video.resolve_case_clip(db, case_id=case_id, clip_id=clip_id)
    except m4_video.KeyframeError as exc:
        fail(404, "clip-not-found", str(exc))
    if not path.exists():
        fail(404, "clip-file-missing", "该片段文件不存在。")
    size = path.stat().st_size
    mime = _video_mime(path)
    header = request.headers.get("range") or ""
    match = RANGE_RE.match(header)
    if not match:
        return Response(path.read_bytes(), media_type=mime, headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-store",
            "X-Clip-Time-Semantics": "container-pts-not-verified-against-osd",
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
        "X-Clip-Time-Semantics": "container-pts-not-verified-against-osd",
    })
