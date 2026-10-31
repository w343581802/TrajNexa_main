"""检测召回评测：同一张截图，比较整图推理与切片（tiling）推理的候选数量。

用途：回答「D-FINE 为什么经常漏」——先量化，再决定换模型还是改推理方式。

容器内用法：
  docker compose run --rm --no-deps \
    -v "<repo>/backend:/srv" -v "<repo>/models:/models:ro" \
    -v "<截图画册>:/data/eval:ro" \
    api python /srv/scripts/detect_eval.py /data/eval --grid 1 --grid 2 --grid 3
"""
import argparse
import json
import sys
import time
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, '/srv')

from app import m4_detection  # noqa: E402


def infer(image, session, layout, threshold):
    tensor, sizes, geometry = m4_detection.to_onnx_input(image)
    if layout == 'hf':
        logits, boxes = session.run(['logits', 'pred_boxes'], {'pixel_values': tensor})
        out = m4_detection.parse_hf_outputs(logits, boxes)
    else:
        out = session.run(['labels', 'boxes', 'scores'],
                          {'images': tensor, 'orig_target_sizes': sizes})
    return m4_detection.parse_detections(out, image.shape, geometry, threshold)


def infer_tiled(image, session, layout, threshold, grid, overlap=0.2):
    """切 grid×grid 后分别推理，再把框映射回整图并跨块去重。"""
    height, width = image.shape[:2]
    if grid <= 1:
        return infer(image, session, layout, threshold)
    step_h, step_w = height / grid, width / grid
    pad_h, pad_w = step_h * overlap, step_w * overlap
    merged = []
    for row in range(grid):
        for col in range(grid):
            top = max(0, int(row * step_h - pad_h))
            left = max(0, int(col * step_w - pad_w))
            bottom = min(height, int((row + 1) * step_h + pad_h))
            right = min(width, int((col + 1) * step_w + pad_w))
            tile = image[top:bottom, left:right]
            if min(tile.shape[:2]) < 24:
                continue
            tile_h, tile_w = tile.shape[:2]
            for item in infer(tile, session, layout, threshold):
                x1, y1, x2, y2 = item['bbox_normalized']
                merged.append({
                    **item,
                    'bbox_normalized': [
                        round((left + x1 * tile_w) / width, 6),
                        round((top + y1 * tile_h) / height, 6),
                        round((left + x2 * tile_w) / width, 6),
                        round((top + y2 * tile_h) / height, 6),
                    ],
                })
    merged.sort(key=lambda p: -p['confidence'])
    kept = m4_detection._nms(merged)
    return [{**item, 'proposal_index': index} for index, item in enumerate(kept)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('--grid', type=int, action='append', default=None)
    parser.add_argument('--threshold', type=float, default=0.35)
    parser.add_argument('--models', default='/models/dfine.onnx')
    parser.add_argument('--sha256', default=None)
    args = parser.parse_args()
    grids = args.grid or [1, 2, 3]

    model = Path(args.models)
    digest = args.sha256 or sha256(model.read_bytes()).hexdigest()
    session, layout = m4_detection._session(str(model), digest.lower())

    folder = Path(args.folder)
    files = sorted(p for p in folder.iterdir() if p.is_file())
    report = []
    for path in files:
        content = path.read_bytes()
        image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            continue
        row = {'image': path.name, 'size': [image.shape[1], image.shape[0]], 'runs': {}}
        for grid in grids:
            started = time.time()
            found = infer_tiled(image, session, layout, args.threshold, grid)
            persons = sum(1 for item in found if item['object_class'] == 'person')
            vehicles = sum(1 for item in found if item['object_class'] == 'vehicle')
            row['runs'][f'grid{grid}'] = {
                'count': len(found), 'person': persons, 'vehicle': vehicles,
                'seconds': round(time.time() - started, 2),
                'labels': [f"{item['detector_label']}:{item['confidence']}" for item in found],
            }
        report.append(row)
        print(json.dumps(row, ensure_ascii=False))

    print('\n== 汇总 ==')
    for grid in grids:
        counts = [row['runs'][f'grid{grid}']['count'] for row in report]
        times = [row['runs'][f'grid{grid}']['seconds'] for row in report]
        print(f'grid{grid}: 平均 {np.mean(counts):.1f} 框 / 平均 {np.mean(times):.2f}s '
              f'/ 每图 {counts}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
