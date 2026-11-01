"""真实录像跨帧 ReID 验证：抽帧 → 检测 person → 建库 → 指定 query 排序。

用法（在 api 镜像里跑，录像与抽帧目录挂到 /work）：

    ffmpeg -v error -i /data/vid/clip.mp4 -ss 88 -t 122 -vf fps=1/4 -q:v 2 /work/frames/t_%03d.jpg
    docker run --rm -v /dev/shm/work:/work -w /srv/app trajnexa_main-api \
        python scripts/reid_cross_frame_eval.py --frames /work/frames \
        --query-frame t_001 --query-index 4 --out /work/eval

真值：--static-tolerance 给定时，把与 query 框中心相差该像素数以内的其它裁图视为
同一目标（只对位置基本不动的目标成立，用于算 Top-1/Top-5 与噪声地板）。
不带该参数时只输出排序与拼图，供人工认人。
"""
import argparse, glob, json, os, sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import detectors  # noqa: E402
from app.engines.reid_onnx_engine import ReidOnnxEngine  # noqa: E402


def tile(path, label, height=200):
    img = cv2.imread(path)
    if img is None:
        return np.zeros((height + 24, 150, 3), np.uint8)
    width = max(48, int(img.shape[1] * height / max(1, img.shape[0])))
    canvas = np.zeros((height + 24, max(width, 150), 3), np.uint8)
    canvas[0:height, 0:width] = cv2.resize(img, (width, height))
    cv2.putText(canvas, label, (2, height + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (255, 255, 255), 1)
    return canvas


def build(args, engine):
    records = []
    for path in sorted(glob.glob(os.path.join(args.frames, "*.jpg"))):
        name = os.path.basename(path)[:-4]
        img = cv2.imread(path)
        if img is None:
            continue
        flat = detectors.detect_flat(img, [args.detector], args.conf)
        props = detectors.proposals(flat["candidates"], (img.shape[1], img.shape[0]))
        height, width = img.shape[:2]
        tiles = []
        for i, item in enumerate(p for p in props if p["object_class"] == "person"):
            x1, y1, x2, y2 = item["bbox_normalized"]
            box = [int(x1 * width), int(y1 * height), int(x2 * width), int(y2 * height)]
            if box[2] - box[0] < 10 or box[3] - box[1] < 20:
                continue
            idx, rows = engine.extract_embeddings(img, [box])
            if not idx:
                continue
            folder = os.path.join(args.out, "crops", name)
            os.makedirs(folder, exist_ok=True)
            rel = f"crops/{name}/{i:02d}.jpg"
            cv2.imwrite(os.path.join(args.out, rel),
                        img[box[1]:box[3], box[0]:box[2]], [cv2.IMWRITE_JPEG_QUALITY, 95])
            records.append({"frame": name, "i": i, "box": box, "h": box[3] - box[1],
                            "conf": round(float(item["confidence"]), 4),
                            "crop": rel, "vec": [float(v) for v in rows[0]]})
            tiles.append(tile(os.path.join(args.out, rel), f"{name}#{i}"))
        print(name, len(tiles), flush=True)
    json.dump(records, open(os.path.join(args.out, "gallery.json"), "w"))
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", default="/work/frames")
    ap.add_argument("--out", default="/work/eval")
    ap.add_argument("--model", default="/srv/models/reid/osnet_x0_25_msmt17_b1.onnx")
    ap.add_argument("--detector", default="dfine-n")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--query-frame", default="t_001")
    ap.add_argument("--query-index", type=int, default=0)
    ap.add_argument("--static-tolerance", type=int, default=0)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    engine = ReidOnnxEngine(model_file=args.model, input_size=(128, 256), device="cpu")
    if not engine.load():
        raise SystemExit("reid engine load failed")
    records = build(args, engine)
    key = f"{args.query_frame}#{args.query_index}"
    query = next((r for r in records if f"{r['frame']}#{r['i']}" == key), None)
    if query is None:
        raise SystemExit(f"query {key} not found (crops={len(records)})")
    qv = np.array(query["vec"], np.float32)
    scored = sorted(((float(np.dot(qv, np.array(r["vec"], np.float32))), r) for r in records),
                    key=lambda x: -x[0])
    print(f"query {key} | crops {len(records)}")
    for rank, (sim, row) in enumerate(scored[:args.top]):
        print(f"  #{rank+1} {sim:.3f} {row['frame']}#{row['i']} h={row['h']}")

    if args.static_tolerance:
        qx, qy = (query["box"][0] + query["box"][2]) / 2, (query["box"][1] + query["box"][3]) / 2
        for row in records:
            cx, cy = (row["box"][0] + row["box"][2]) / 2, (row["box"][1] + row["box"][3]) / 2
            row["positive"] = (row is not query and abs(cx - qx) <= args.static_tolerance
                               and abs(cy - qy) <= args.static_tolerance)
        hit1 = hit5 = frames = 0
        floor = 0.0
        for frame in sorted({r["frame"] for r in records}):
            if frame == args.query_frame:
                continue
            cands = [r for r in records if r["frame"] == frame]
            ranked = sorted(((float(np.dot(qv, np.array(r["vec"], np.float32))), r)
                             for r in cands), key=lambda x: -x[0])
            if not any(r["positive"] for r in cands):
                floor = max(floor, ranked[0][0])
                continue
            frames += 1
            flags = [r["positive"] for _s, r in ranked]
            hit1 += int(flags[0])
            hit5 += int(any(flags[:5]))
        positives = [s for s, r in scored if r.get("positive")]
        print(f"frames with target {frames} | Top-1 {hit1}/{frames} | Top-5 {hit5}/{frames}")
        print(f"positive sims min={min(positives):.3f} mean={np.mean(positives):.3f}")
        print(f"no-target floor (max sim where target absent)={floor:.3f}")

    tiles = [tile(os.path.join(args.out, query["crop"]), "QUERY " + key)]
    for rank, (sim, row) in enumerate(scored[:args.top - 1]):
        tiles.append(tile(os.path.join(args.out, row["crop"]),
                          f"#{rank+1} {sim:.2f} {row['frame']}#{row['i']}"))
    while len(tiles) % 6:
        tiles.append(np.zeros((224, 150, 3), np.uint8))
    rows = [np.hstack(tiles[i:i + 6]) for i in range(0, len(tiles), 6)]
    width = max(r.shape[1] for r in rows)
    sheet = np.vstack([np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0))) for r in rows])
    out = os.path.join(args.out, f"sheet_{args.query_frame}_{args.query_index}.jpg")
    cv2.imwrite(out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print("sheet", out)


if __name__ == "__main__":
    main()
