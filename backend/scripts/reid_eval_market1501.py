"""Market-1501 single-query CMC for the vendored OSNet ONNX (inference only).

用途：给 M6 的人员 ReID 提供可复跑、可对照的检索基线，避免用「看起来差不多」
当验收。数据集不在仓库里，需要自己下载 Market-1501 并解压到 MARKET_ROOT。

    docker run --rm -v /path/Market-1501-v15.09.15:/data/mkt:ro \
        -v "$PWD/backend:/srv" -w /srv/app trajnexa_main-api \
        python scripts/reid_eval_market1501.py

输出：Top-1 / Top-5，按官方协议排除「同 id 同摄像头」的库图。junk(-1) 不计入。
"""
import argparse, os, re, sys, time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.engines.reid_onnx_engine import ReidOnnxEngine  # noqa: E402

PAT = re.compile(r"^(-?\d+)_(c\d+)s\d+_")


def parse(name):
    hit = PAT.match(name)
    return (int(hit.group(1)), hit.group(2)) if hit else (None, None)


def collect(root, sub):
    rows = []
    for name in sorted(os.listdir(os.path.join(root, sub))):
        pid, cam = parse(name)
        if pid is None or pid == -1:
            continue
        rows.append((name, pid, cam))
    return rows


def embed(engine, root, sub, items, dim):
    vecs = np.zeros((len(items), dim), np.float32)
    started = time.time()
    for i, (name, _pid, _cam) in enumerate(items):
        img = cv2.imread(os.path.join(root, sub, name), cv2.IMREAD_COLOR)
        if img is None:
            continue
        height, width = img.shape[:2]
        idx, rows = engine.extract_embeddings(img, [[0, 0, width, height]])
        if idx:
            vecs[i] = rows[0]
    print(f"{sub}: {len(items)} images in {time.time()-started:.0f}s", flush=True)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("MARKET_ROOT", "/data/mkt"))
    ap.add_argument("--model", default=os.environ.get("REID_ONNX",
                                                   "/srv/models/reid/osnet_x0_25_msmt17_b1.onnx"))
    args = ap.parse_args()
    engine = ReidOnnxEngine(model_file=args.model, input_size=(128, 256), device="cpu")
    if not engine.load():
        raise SystemExit("reid engine load failed")
    gallery = collect(args.root, "bounding_box_test")
    queries = collect(args.root, "query")
    print(f"gallery {len(gallery)} | queries {len(queries)}", flush=True)
    dim = int(getattr(engine, "_embedding_dim", 512))
    G = embed(engine, args.root, "bounding_box_test", gallery, dim)
    Q = embed(engine, args.root, "query", queries, dim)
    gid = np.array([pid for _n, pid, _c in gallery])
    gcam = np.array([cam for _n, _p, cam in gallery])
    top1 = top5 = valid = 0
    for qi, (_name, pid, cam) in enumerate(queries):
        keep = ~((gid == pid) & (gcam == cam))  # 官方协议：同 id 同摄像头不算命中
        if not (gid[keep] == pid).any():
            continue
        order = np.argsort(-(G[keep] @ Q[qi]))[:10]
        ids = gid[keep][order]
        valid += 1
        top1 += int(ids[0] == pid)
        top5 += int((ids[:5] == pid).any())
    if not valid:
        raise SystemExit("no valid query")
    print(f"valid queries: {valid}")
    print(f"Top-1: {top1/valid*100:.2f}%")
    print(f"Top-5: {top5/valid*100:.2f}%")


if __name__ == "__main__":
    main()
