"""M4b: optional *local* official D-FINE ONNX candidate detection.

Never downloads models; no face recognition, tracking, identity matching or
automatic evidence creation. Official exported model contract:
images [1,3,640,640], orig_target_sizes [1,2], outputs labels/boxes/scores.
"""
from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import math
import re

import cv2
import numpy as np

from app.m4_features import MAX_PIXELS


class DetectionError(ValueError):
    """Sanitized stable status codes; no file paths or secrets in errors."""


MODEL_SIZE = 640
COCO_CLASSES = {
    0: ('person', 'person'),
    1: ('vehicle', 'bicycle'),
    2: ('vehicle', 'car'),
    3: ('vehicle', 'motorcycle'),
    5: ('vehicle', 'bus'),
    7: ('vehicle', 'truck'),
}
MAX_CANDIDATES = 30


def verified_image(path: Path, expected_hash: str) -> np.ndarray:
    try:
        content = path.read_bytes()
    except OSError:
        raise DetectionError('m4b_source_unavailable') from None
    if sha256(content).hexdigest() != expected_hash:
        raise DetectionError('m4b_source_hash_mismatch')
    img = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise DetectionError('m4b_source_decode_failed')
    if img.shape[0] * img.shape[1] > MAX_PIXELS:
        raise DetectionError('m4b_source_image_too_large')
    return img


def to_onnx_input(image: np.ndarray):
    """D-FINE official ONNX example: 640 square letterbox and canvas size."""
    h, w = image.shape[:2]
    if min(h, w) < 24 or max(h, w) > 20_000:
        raise DetectionError('m4b_invalid_frame_size')
    ratio = min(MODEL_SIZE / w, MODEL_SIZE / h)
    rw, rh = int(w * ratio), int(h * ratio)
    rw, rh = max(rw, 1), max(rh, 1)
    pad_x, pad_y = (MODEL_SIZE - rw) // 2, (MODEL_SIZE - rh) // 2
    canvas = np.zeros((MODEL_SIZE, MODEL_SIZE, 3), dtype=np.uint8)
    resized = cv2.resize(image, (rw, rh), interpolation=cv2.INTER_LINEAR)
    canvas[pad_y:pad_y+rh, pad_x:pad_x+rw] = resized
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
    sizes = np.array([[MODEL_SIZE, MODEL_SIZE]], dtype=np.int64)
    return tensor, sizes, (ratio, pad_x, pad_y)


def parse_detections(outputs, source_shape, geometry, threshold: float = 0.4):
    """Suppress malformed/degenerate boxes; return independent candidate ROIs."""
    if len(outputs) != 3:
        raise DetectionError('m4b_bad_model_outputs')
    try:
        labels = np.asarray(outputs[0]).reshape(-1)
        boxes = np.asarray(outputs[1])
        scores = np.asarray(outputs[2]).reshape(-1)
    except (TypeError, ValueError):
        raise DetectionError('m4b_bad_model_outputs') from None
    if boxes.ndim == 3 and boxes.shape[0] == 1:
        boxes = boxes[0]
    if boxes.ndim != 2 or boxes.shape[1] != 4 or not (
        len(labels) == len(boxes) == len(scores) and len(labels) <= 2000
    ):
        raise DetectionError('m4b_bad_model_outputs')
    if not .2 <= threshold <= 1 or not math.isfinite(threshold):
        raise DetectionError('m4b_bad_threshold')
    h, w = source_shape[:2]
    ratio, pad_x, pad_y = geometry
    candidates = []
    for i, (label, box, score) in enumerate(zip(labels, boxes, scores)):
        try:
            label_num = int(label)
            confidence = float(score)
            valid_label = math.isfinite(float(label)) and float(label) == label_num
        except (ValueError, TypeError, OverflowError):
            continue
        if not valid_label or label_num not in COCO_CLASSES or not math.isfinite(confidence):
            continue
        if confidence < threshold or confidence > 1:
            continue
        try:
            xyxy = [float(v) for v in box]
        except (ValueError, TypeError):
            continue
        if not all(math.isfinite(v) for v in xyxy):
            continue
        left = max(0.0, min(float(w), (xyxy[0] - pad_x) / ratio))
        top = max(0.0, min(float(h), (xyxy[1] - pad_y) / ratio))
        right = max(0.0, min(float(w), (xyxy[2] - pad_x) / ratio))
        bottom = max(0.0, min(float(h), (xyxy[3] - pad_y) / ratio))
        if right - left < 24 or bottom - top < 24:
            continue
        category, name = COCO_CLASSES[label_num]
        candidates.append({
            'proposal_index': i,
            'object_class': category,
            'detector_label': name,
            'confidence': round(confidence, 4),
            'bbox_normalized': [
                round(left / w, 6), round(top / h, 6),
                round(right / w, 6), round(bottom / h, 6),
            ],
            'status': 'unverified_detection_proposal',
        })
    candidates.sort(key=lambda p: (-p['confidence'], p['proposal_index']))
    return _nms(candidates)[:MAX_CANDIDATES]


def _overlap(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _is_duplicate(a: list[float], b: list[float]) -> bool:
    """重叠 + 近邻两种重复：小目标框尺寸略有差异时 IoU 会很低，只看 IoU 不够。"""
    if _overlap(a, b) > 0.5:
        return True
    wa, ha = a[2] - a[0], a[3] - a[1]
    wb, hb = b[2] - b[0], b[3] - b[1]
    if min(wa, ha, wb, hb) <= 0:
        return False
    distance = math.hypot((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2,
                          (a[1] + a[3]) / 2 - (b[1] + b[3]) / 2)
    if distance > 0.5 * max(wa, ha, wb, hb):
        return False
    ratio = (wa * ha) / (wb * hb)
    return 0.4 <= ratio <= 2.5


def _nms(candidates: list[dict]) -> list[dict]:
    """同一个目标会给出多个重叠框：按分数保留，重复的丢掉，演示时画面才干净。"""
    kept: list[dict] = []
    for item in candidates:
        box = item['bbox_normalized']
        if any(_is_duplicate(box, other['bbox_normalized']) for other in kept):
            continue
        kept.append(item)
    return kept


def _validate_digest(digest: str):
    if not isinstance(digest, str) or re.fullmatch(r'[a-fA-F0-9]{64}', digest) is None:
        raise DetectionError('m4b_model_sha256_required')


@lru_cache(maxsize=2)
def _session(path_str: str, expected_hash: str):
    """Load local read-only model only after digest verification."""
    _validate_digest(expected_hash)
    model = Path(path_str).resolve()
    if not model.is_file() or model.suffix.lower() != '.onnx':
        raise DetectionError('m4b_model_unavailable')
    try:
        hasher = sha256()
        with model.open('rb') as handle:
            while chunk := handle.read(1024 * 1024):
                hasher.update(chunk)
    except OSError:
        raise DetectionError('m4b_model_unavailable') from None
    if hasher.hexdigest().lower() != expected_hash.lower():
        raise DetectionError('m4b_model_digest_mismatch')
    try:
        import onnxruntime as ort
    except ImportError:
        raise DetectionError('m4b_runtime_unavailable') from None
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        sess = ort.InferenceSession(str(model), sess_options=options,
                                    providers=['CPUExecutionProvider'])
        inputs = {item.name: item for item in sess.get_inputs()}
        outputs = {item.name for item in sess.get_outputs()}
        # 官方 deploy/onnx 导出：images + orig_target_sizes → labels/boxes/scores
        if set(inputs) == {'images', 'orig_target_sizes'} and {
                'labels', 'boxes', 'scores'}.issubset(outputs):
            return sess, 'official'
        # transformers / onnx-community 导出：pixel_values → logits + pred_boxes
        if set(inputs) == {'pixel_values'} and {
                'logits', 'pred_boxes'}.issubset(outputs):
            return sess, 'hf'
        raise DetectionError('m4b_incompatible_model')
    except DetectionError:
        raise
    except Exception:
        raise DetectionError('m4b_model_load_failed') from None


def parse_hf_outputs(logits, pred_boxes):
    """HF/onnx-community 导出 → 官方三件套（labels / xyxy 像素 / scores）。

    logits 是 [1,300,80] 未过 sigmoid 的分类分；pred_boxes 是 [1,300,4] 的
    cx,cy,w,h 且已按输入画布归一化，这里换算成 640 画布下的 xyxy 像素。
    """
    try:
        logits = np.asarray(logits, dtype=np.float64)
        boxes = np.asarray(pred_boxes, dtype=np.float64)
    except (TypeError, ValueError):
        raise DetectionError('m4b_bad_model_outputs') from None
    if logits.ndim == 2:
        logits = logits[None]
    if boxes.ndim == 2:
        boxes = boxes[None]
    if logits.ndim != 3 or boxes.ndim != 3 or boxes.shape[-1] != 4 or \
            logits.shape[:2] != boxes.shape[:2] or logits.shape[0] != 1:
        raise DetectionError('m4b_bad_model_outputs')
    probs = 1.0 / (1.0 + np.exp(-logits))
    best = probs.argmax(axis=-1)                       # [1, N]
    labels = best.reshape(-1)
    scores = np.take_along_axis(probs, best[..., None], axis=-1).reshape(-1)
    flat = boxes.reshape(-1, 4) * MODEL_SIZE
    cx, cy, bw, bh = flat[:, 0], flat[:, 1], flat[:, 2], flat[:, 3]
    xyxy = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
    return labels, xyxy, scores


def detect_image(image: np.ndarray, *, model_path: Path, model_sha256: str,
                 source_sha256: str, threshold: float = 0.4) -> dict:
    _validate_digest(model_sha256)
    session, layout = _session(str(model_path), model_sha256.lower())
    input_tensor, original_size, geometry = to_onnx_input(image)
    try:
        if layout == 'hf':
            logits, pred_boxes = session.run(['logits', 'pred_boxes'],
                                             {'pixel_values': input_tensor})
            output = parse_hf_outputs(logits, pred_boxes)
        else:
            output = session.run(['labels', 'boxes', 'scores'],
                                 {'images': input_tensor,
                                  'orig_target_sizes': original_size})
    except DetectionError:
        raise
    except Exception:
        raise DetectionError('m4b_inference_failed') from None
    proposals = parse_detections(output, image.shape, geometry, threshold)
    return {
        'schema_version': '1.0',
        'status': 'candidates_unverified',
        'model_family': 'D-FINE',
        'model_format': 'onnx',
        'model_sha256': model_sha256.lower(),
        'input_size': MODEL_SIZE,
        'source_sha256': source_sha256,
        'image_size': [image.shape[1], image.shape[0]],
        'candidate_count': len(proposals),
        'candidates': proposals,
        'note': 'Detector boxes are proposals only; no identity, camera or location is established.',
    }
