"""离线冒烟：对一张本地截图跑 D-FINE 或 Qwen3-VL，打印候选框。

容器内用法（不用启服务，改完代码就能验）：
  docker compose run --rm --no-deps \
    -v "<repo>/backend:/srv" -v "<repo>/models:/models:ro" \
    -v "<repo>/storage:/data/storage" \
    api python /srv/scripts/detect_smoke.py /data/storage/xxx.jpg --engine dfine
"""
import argparse
import os
import sys
from hashlib import sha256
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, '/srv')

from app import m4_detection, m4_vision  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    parser.add_argument('--engine', choices=('dfine', 'qwen3vl'), default='dfine')
    parser.add_argument('--threshold', type=float, default=0.35)
    args = parser.parse_args()

    path = Path(args.image)
    content = path.read_bytes()
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        print('cannot decode', path)
        return 2

    if args.engine == 'dfine':
        result = m4_detection.detect_image(
            image, model_path=Path(os.environ.get('M4B_MODEL_PATH', '/models/dfine.onnx')),
            model_sha256=os.environ.get('M4B_MODEL_SHA256', ''),
            source_sha256=sha256(content).hexdigest(), threshold=args.threshold)
    else:
        result = {'candidates': m4_vision.detect_objects(
            image,
            base_url=os.environ.get('VISION_BASE_URL', 'http://host.docker.internal:11434'),
            model=os.environ.get('VISION_MODEL', 'qwen3-vl:2b-instruct'),
            timeout=float(os.environ.get('VISION_TIMEOUT_SECONDS', 180)))}

    print(f"{args.engine}: {len(result['candidates'])} candidates")
    for item in result['candidates']:
        print(' ', item['detector_label'], item['object_class'],
              item['confidence'], item['bbox_normalized'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
