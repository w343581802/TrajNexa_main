"""角标读取评测：在同一批抽帧上比较 PP-OCRv6 与 Qwen3-VL 的名称 / 时间识别。

为什么要有这个脚本：主系统的「录像资源」靠画面角标拿到摄像头名称和时间，这件事一直
只有 PP-OCRv6 一条路。想判断换成 / 补上视觉大模型值不值，必须先在同一批真实帧上量
出可比的数字，而不是凭感觉。

用法（三步都只读，不改录像、不改库）：

1. 生成清单（要 api 在跑，默认本机 8010）：

       python backend/scripts/overlay_eval.py manifest --api http://127.0.0.1:8010 \\
           --out storage/validation/overlay_manifest.json

2. 跑 PP-OCRv6（必须在装了 paddle 的镜像里，例如 trajnexa_main-worker）：

       docker compose run --rm --no-deps \\
           -v "<repo>/backend:/srv" -v "<repo>/storage/recording-frames:/frames" \\
           worker python scripts/overlay_eval.py run --engine ppocr \\
           --manifest /srv/storage/validation/overlay_manifest.json \\
           --out /srv/storage/validation/ppocr_eval.json

3. 跑 Qwen3-VL（宿主机跑，Ollama 默认 127.0.0.1:11434）：

       python backend/scripts/overlay_eval.py run --engine qwen3vl \\
           --model qwen3-vl:2b --scale 1280 --roi bottom \\
           --out storage/validation/qwen3vl_eval.json

结果 JSON 里每帧带 raw 文本、判定（exact/near/wrong）、耗时；末尾打印汇总，两个引擎的
汇总可以直接相减。
"""
from __future__ import annotations

import argparse
import base64
import difflib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_API = os.environ.get("TRAJNEXA_API", "http://127.0.0.1:8010")
DEFAULT_OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

NAME_PROMPT = (
    "这是监控录像的截图，画面上叠加了摄像头名称，格式形如「编号-地点-类型」，"
    "例如「YD-桂康路中仁路T口2-1」。\n"
    "规则：\n"
    "1. 只输出这一串摄像头名称，不要任何解释、标点或引号；\n"
    "2. 忽略画面边缘竖排或半透明的省市水印（如「广西桂林临桂」）；\n"
    "3. 忽略日期时间、通道号、厂商信息等其他文字；\n"
    "4. 看不出来就只输出 unknown。"
)

TIME_PROMPT = (
    "这是监控录像的截图，画面上叠加了日期时间（形如 2026-10-23 14:59:55）。\n"
    "只输出这串日期时间本身，不要解释。看不到就只输出 unknown。"
)


# ---------------------------------------------------------------- 通用工具


def http_json(url: str, payload: dict | None = None, timeout: int = 600) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc


def norm(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"[\s\"'`「」『』：:，,。.、]+", "", str(text)).lower()


def name_verdict(expected: str | None, got: str | None) -> tuple[str, float]:
    """exact / near(>=0.9 相似) / wrong / empty。"""
    exp, g = norm(expected), norm(got)
    if not g:
        return "empty", 0.0
    if g == exp:
        return "exact", 1.0
    ratio = difflib.SequenceMatcher(None, g, exp).ratio()
    return ("near" if ratio >= 0.9 else "wrong"), round(ratio, 3)


def time_verdict(expected: str | None, got: str | None) -> tuple[bool, float | None]:
    try:
        exp = datetime.fromisoformat(str(expected).replace("Z", ""))
    except Exception:
        return False, None
    flat = str(got or "").replace("年", "-").replace("月", "-").replace("日", " ").replace("/", "-")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T]+(\d{1,2}):(\d{2}):(\d{2})", flat)
    if not m:
        return False, None
    delta = abs((datetime(*(int(x) for x in m.groups())) - exp).total_seconds())
    return delta <= 2, delta


def load_manifest(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["frames"] if isinstance(data, dict) else data


# ---------------------------------------------------------------- 清单


def build_manifest(api: str, frames_dir: Path, out: Path) -> int:
    cases = http_json(f"{api}/api/v1/cases", timeout=30)
    if isinstance(cases, dict):
        cases = cases.get("items") or cases.get("cases") or []
    rows: list[dict] = []
    for case in cases:
        got = http_json(f"{api}/api/v1/recordings/clips?case_id={case['id']}", timeout=60)
        if isinstance(got, dict):
            got = got.get("items") or got.get("clips") or []
        for clip in got:
            frame = None
            for suffix in ("-v2.jpg", ".jpg", "-v2.png", ".png"):
                candidate = frames_dir / f"{clip['id']}{suffix}"
                if candidate.exists():
                    frame = candidate
                    break
            if frame is None:
                continue
            rows.append({
                "clip_id": clip["id"],
                # 只存文件名：清单要能在宿主机和容器里都能用（容器里帧目录挂在 /frames）
                "frame": frame.name,
                "file_name": clip.get("file_name"),
                "expected_name": clip.get("camera_name"),
                "expected_time": clip.get("start_local"),
                "bytes": frame.stat().st_size,
            })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"frames": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {len(rows)} frames -> {out}")
    return 0


# ---------------------------------------------------------------- 图像预处理


def prepare_image(path: Path, roi: str, scale: int) -> bytes:
    """按需裁角标条带 / 缩放，统一成 JPEG 字节。"""
    raw = path.read_bytes()
    if roi == "none" and not scale:
        return raw
    from PIL import Image

    im = Image.open(io.BytesIO(raw)).convert("RGB")
    if roi == "bottom":
        # 名称/时间条带基本都在画面下缘；裁掉上半部分既能去掉大部分无关画面，
        # 也能把块数（token）压下来
        im = im.crop((0, int(im.height * 0.78), im.width, im.height))
    elif roi == "bottom-right":
        im = im.crop((int(im.width * 0.45), int(im.height * 0.78), im.width, im.height))
    if scale and im.width > scale:
        im = im.resize((scale, int(im.height * scale / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ---------------------------------------------------------------- 引擎


def run_qwen3vl(row: dict, model: str, roi: str, scale: int, num_ctx: int,
                ollama: str) -> dict:
    item: dict[str, Any] = {}
    image = base64.b64encode(prepare_image(Path(row["frame"]), roi, scale)).decode("ascii")
    for key, prompt in (("name", NAME_PROMPT), ("time", TIME_PROMPT)):
        payload = {
            "model": model, "stream": False,
            "messages": [{"role": "user", "content": prompt, "images": [image]}],
            "options": {"temperature": 0, "num_ctx": num_ctx},
        }
        started = time.time()
        out = http_json(f"{ollama}/api/chat", payload)
        item[f"{key}_seconds"] = round(time.time() - started, 2)
        item[f"{key}_raw"] = ((out.get("message") or {}).get("content") or "").strip()
    return item


def run_ppocr(row: dict, roi: str) -> dict:
    """跑主系统自己的角标读取（PP-OCRv6）。只能在装了 paddle 的镜像里用。"""
    import cv2  # noqa: PLC0415  OCR 镜像里才有
    import numpy as np  # noqa: PLC0415
    from app import overlay_ocr  # noqa: PLC0415

    raw = prepare_image(Path(row["frame"]), roi, 0)
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    started = time.time()
    rows = overlay_ocr.read_overlay_texts(image)
    parsed = overlay_ocr.parse_overlay_rows(rows)
    return {
        "name_raw": parsed.get("name") or "",
        "time_raw": parsed.get("time_text") or parsed.get("local_time") or "",
        "name_seconds": round(time.time() - started, 2),
        "time_seconds": None,
        "rows": [r.get("text") for r in rows],
    }


# ---------------------------------------------------------------- 主流程


def run_eval(args) -> int:
    frames = load_manifest(Path(args.manifest))
    frames_dir = Path(args.frames_dir)
    for row in frames:
        candidate = Path(row["frame"])
        row["frame"] = str(candidate if candidate.is_absolute() else frames_dir / candidate)
    if args.limit:
        frames = frames[: args.limit]
    print(f"engine={args.engine} model={args.model} roi={args.roi} scale={args.scale} "
          f"frames={len(frames)}", flush=True)

    results = []
    for i, row in enumerate(frames, 1):
        item = dict(row)
        try:
            got = (run_ppocr(row, args.roi) if args.engine == "ppocr"
                   else run_qwen3vl(row, args.model, args.roi, args.scale,
                                    args.num_ctx, args.ollama))
        except Exception as exc:  # 单帧失败不该毁掉整轮评测
            item["error"] = f"{type(exc).__name__}: {exc}"
            print(f"[{i:2}/{len(frames)}] ERROR {item['error']}", flush=True)
            results.append(item)
            continue
        item.update(got)
        verdict, ratio = name_verdict(item.get("expected_name"), item.get("name_raw"))
        time_ok, delta = time_verdict(item.get("expected_time"), item.get("time_raw"))
        item.update(verdict=verdict, similarity=ratio, time_hit=time_ok, time_delta=delta)
        print(f"[{i:2}/{len(frames)}] name={verdict:5} (sim={ratio:.2f}) "
              f"exp={item.get('expected_name')!r} got={item.get('name_raw')!r} "
              f"time={'ok' if time_ok else 'MISS'} d={delta}", flush=True)
        results.append(item)

    scored = [r for r in results if "verdict" in r]
    exact = sum(1 for r in scored if r["verdict"] == "exact")
    near = sum(1 for r in scored if r["verdict"] == "near")
    time_hits = sum(1 for r in scored if r.get("time_hit"))
    name_secs = sorted(r["name_seconds"] for r in scored if r.get("name_seconds"))
    summary = {
        "engine": args.engine,
        "model": args.model if args.engine != "ppocr" else "PP-OCRv6",
        "roi": args.roi,
        "scale": args.scale,
        "frames": len(results),
        "scored": len(scored),
        "errors": len(results) - len(scored),
        "name_exact": exact,
        "name_near": near,
        "name_wrong": len(scored) - exact - near,
        "time_hit": time_hits,
        "name_seconds_median": name_secs[len(name_secs) // 2] if name_secs else None,
    }
    payload = {"summary": summary, "results": results}
    out = Path(args.out) if args.out else Path(
        f"overlay_eval_{args.engine}_{time.strftime('%Y%m%d_%H%M%S')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== 汇总 ==")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"saved {out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    man = sub.add_parser("manifest", help="从 api + 抽帧目录生成评测清单")
    man.add_argument("--api", default=DEFAULT_API)
    man.add_argument("--frames", default="storage/recording-frames")
    man.add_argument("--out", default="storage/validation/overlay_manifest.json")

    run = sub.add_parser("run", help="在同一份清单上跑某个引擎")
    run.add_argument("--engine", choices=["ppocr", "qwen3vl"], required=True)
    run.add_argument("--manifest", required=True)
    run.add_argument("--frames-dir", default="storage/recording-frames",
                     help="抽帧所在目录（容器里传 /frames）")
    run.add_argument("--model", default=os.environ.get("MODEL", "qwen3-vl:2b"))
    run.add_argument("--ollama", default=DEFAULT_OLLAMA)
    run.add_argument("--roi", choices=["none", "bottom", "bottom-right"], default="none")
    run.add_argument("--scale", type=int, default=0, help="按宽度缩放（0=原图）")
    run.add_argument("--num-ctx", type=int, default=16384)
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--out", default="")

    args = ap.parse_args()
    if args.cmd == "manifest":
        return build_manifest(args.api, Path(args.frames), Path(args.out))
    return run_eval(args)


if __name__ == "__main__":
    sys.exit(main())
