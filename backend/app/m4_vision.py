"""M4d: local vision-language proposals and ROI attributes (Qwen3-VL via Ollama).

用途是给「添加要素」一个**看得见**的自动框选：模型直接在画面上给出候选框，
人工点选后才成为观测。这里只产出待核实建议，不确认身份、不写库、不碰原图。

离线约束：不下载模型、不联网；只访问本机 Ollama（默认 host.docker.internal:11434）。
失败一律抛 VisionError，带稳定错误码，由接口层翻译成 503 / 422。
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

import cv2
import numpy as np

VERSION = 'm4d_vlm_proposals_v1'
# 与角标评测（design/34）同口径：缩到 1280 宽又快又准，1920 原图只会更慢且更爱读错
MAX_WIDTH = 1280
MAX_BOXES = 8
MIN_BOX_SIDE_RATIO = 0.02

DETECT_PROMPT = (
    "找出画面里最主要的目标（人、轿车、货车、摩托车、自行车），最多 5 个，每个目标都必须给出边界框 box。\n"
    '只输出 JSON：{"objects":[{"box":[100,200,400,600],"label":"黑色轿车","category":"vehicle"}]}\n'
    "box 是 [左上x, 左上y, 右下x, 右下y]，相对整幅图 0-1000 的整数坐标；"
    "category 只能 person / vehicle / other。\n"
    '没有目标就输出 {"objects":[]}。只输出 JSON，不解释，不重复字段。'
)

FEATURE_PROMPT = (
    "这张图是监控画面里一个目标的裁剪。只输出一行 JSON，描述能看见的**外观线索**：\n"
    '{"features":["深色上衣","浅色裤子","戴口罩"]}\n'
    "3 到 5 个短语，只写颜色、衣着、姿态、携带物；不要猜身份、性别、年龄或姓名；不要解释。"
)

# Distinct task prompts. The final runtime prompt can be overridden for a single
# image experiment; persistence/versioning belong to the M5 Prompt Workbench.
VEHICLE_FEATURE_PROMPT = (
    "这是监控截图裁剪出的一个车辆候选区域，可能含背景或骑行者。"
    "仅描述画面中确实可见的车辆自身特征，输出JSON："
    '{"features":["深色车身","两轮车","后部有货箱"]}。'
    "优先车身颜色、轮数/大类、车体外形、可见载物与遮挡；"
    "看不清的车型、车标、型号、牌照必须写未知或不提，不要猜测品牌和身份；"
    "不要把旁边行人的衣服误作车辆特征。"
)
OTHER_FEATURE_PROMPT = (
    '这是监控截图的目标裁剪，只描述能直接看到的形状、颜色和遮挡。'
    '输出JSON：{"features":["深色物体","有明显遮挡"]}。'
    "看不清的属性不要猜测，不要推断身份或来源。"
)


def effective_feature_prompt(object_class: str = "person",
                             prompt_override: str | None = None) -> str:
    if prompt_override is not None:
        if not isinstance(prompt_override, str) or len(prompt_override) > 2400:
            raise VisionError("m4d_invalid_prompt")
        prompt = prompt_override.strip()
        if prompt:
            return prompt
    if object_class == "vehicle":
        return VEHICLE_FEATURE_PROMPT
    if object_class == "other":
        return OTHER_FEATURE_PROMPT
    return FEATURE_PROMPT


class VisionError(ValueError):
    """稳定错误码，不含文件路径或密钥。"""


def _normalized_image(image: np.ndarray) -> np.ndarray:
    """BGR → 缩到 MAX_WIDTH 以内的副本，避免把原图整幅塞进模型。"""
    if image is None or image.ndim != 3:
        raise VisionError('m4d_source_decode_failed')
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        raise VisionError('m4d_invalid_frame_size')
    if width <= MAX_WIDTH:
        return image
    scale = MAX_WIDTH / width
    return cv2.resize(image, (MAX_WIDTH, max(1, int(round(height * scale)))),
                      interpolation=cv2.INTER_AREA)


def _encode_jpeg(image: np.ndarray, quality: int = 86) -> bytes:
    ok, buffer = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise VisionError('m4d_encode_failed')
    return buffer.tobytes()


def _chat(base_url: str, model: str, prompt: str, jpeg: bytes, timeout: float) -> str:
    payload = {
        'model': model,
        'stream': False,
        'messages': [{'role': 'user', 'content': prompt,
                      'images': [base64.b64encode(jpeg).decode('ascii')]}],
        # 温度 0：同一张图重复识别要给出同一组框，否则演示时每次都不一样。
        # num_predict 必须设上限：2B 模型不给上限时会陷入重复字段，1 分钟也停不下来；
        # repeat_penalty 一起用，才稳定停在完整 JSON 上（实测 ~1.5s）。
        'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 400,
                    'repeat_penalty': 1.1},
    }
    request = urllib.request.Request(
        base_url.rstrip('/') + '/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:200]
        if 'context size' in detail or 'too long' in detail:
            raise VisionError('m4d_prompt_too_long') from None
        raise VisionError('m4d_model_http_error') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise VisionError('m4d_model_unavailable') from None
    except json.JSONDecodeError:
        raise VisionError('m4d_model_bad_response') from None
    message = body.get('message') or {}
    content = (message.get('content') or '').strip()
    if not content:
        # qwen3-vl:2b 这类 thinking 变体会把 token 全花在推理上、content 为空：
        # 明确报错并提示换 *-instruct 变体，而不是让人猜模型是不是挂了
        if (message.get('thinking') or '').strip():
            raise VisionError('m4d_model_thinking_only')
        raise VisionError('m4d_model_empty_response')
    return content


def extract_json(text: str) -> dict:
    """模型常带解释或代码块：容忍 ```json 围栏，取第一个平衡的花括号对象。"""
    cleaned = re.sub(r'```(?:json)?', '', text).strip()
    start = cleaned.find('{')
    if start < 0:
        raise VisionError('m4d_model_bad_response')
    depth = 0
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start:index + 1])
                except json.JSONDecodeError:
                    raise VisionError('m4d_model_bad_response') from None
                if not isinstance(parsed, dict):
                    raise VisionError('m4d_model_bad_response')
                return parsed
    raise VisionError('m4d_model_bad_response')


def _clean_box(raw, width: int, height: int) -> list[float] | None:
    """0-1000 相对坐标 → 0..1 归一化框；坏框直接丢弃而不是抛错。"""
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        values = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if any(not np.isfinite(v) for v in values):
        return None
    # 有些模型会直接给像素坐标；超过 1000 的一律按像素处理
    if max(values) > 1000:
        values = [values[0] / width * 1000, values[1] / height * 1000,
                  values[2] / width * 1000, values[3] / height * 1000]
    left, top, right, bottom = [min(1000.0, max(0.0, v)) / 1000 for v in values]
    if right - left < MIN_BOX_SIDE_RATIO or bottom - top < MIN_BOX_SIDE_RATIO:
        return None
    return [round(left, 6), round(top, 6), round(right, 6), round(bottom, 6)]


def _dedupe(items, limit: int = 6) -> list[str]:
    cleaned: list[str] = []
    for item in items:
        text = str(item).strip().strip('，,。.;；')[:24]
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:limit]


def _fragments(text: str) -> list[str]:
    """模型没给 JSON 时退一步：把这句话按标点切成短语，仍然是「能看见的线索」。"""
    parts = re.split(r'[，,。；;、\n]+', re.sub(r'```(?:json)?', '', text))
    return _clean_features(parts)


CATEGORY_HINTS = (
    ('person', ('人', '行人', '男子', '女子', '男', '女', 'person', 'man', 'woman',
                'pedestrian', 'child', 'worker')),
    ('vehicle', ('车', '轿车', '汽车', '货车', '卡车', '面包车', '公交', '摩托', '自行车',
                 '电动', '三轮', 'car', 'truck', 'bus', 'van', 'suv', 'bike',
                 'motorcycle', 'taxi')),
)


def infer_category(label: str) -> str:
    """模型经常只给 label 不给 category：按中文/英文关键词兜底，别一律塞 other。"""
    text = (label or '').lower()
    for category, hints in CATEGORY_HINTS:
        if any(hint in text for hint in hints):
            return category
    return 'other'


def _iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _is_duplicate(a: list[float], b: list[float], same_class: bool = True) -> bool:
    overlap = _iou(a, b)
    if overlap > (0.5 if same_class else 0.75):
        return True
    if not same_class:
        return False
    wa, ha = a[2] - a[0], a[3] - a[1]
    wb, hb = b[2] - b[0], b[3] - b[1]
    if min(wa, ha, wb, hb) <= 0:
        return False
    distance = ((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2) ** 2 + \
               ((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) ** 2
    if distance > (0.6 * max(wa, ha, wb, hb)) ** 2:
        return False
    ratio = (wa * ha) / (wb * hb)
    return 0.25 <= ratio <= 4


def _dedupe_boxes(candidates: list[dict]) -> list[dict]:
    """模型常对同一辆车给好几个几乎一样的框：去重，避免演示时满地重复框。"""
    kept: list[dict] = []
    for item in candidates:
        box = item['bbox_normalized']
        if any(_is_duplicate(box, other['bbox_normalized'],
                             item['object_class'] == other['object_class'])
               for other in kept):
            continue
        kept.append({**item, 'proposal_index': len(kept)})
    return kept


def detect_objects(image: np.ndarray, *, base_url: str, model: str,
                   timeout: float = 180) -> list[dict]:
    """调用本地视觉模型，返回待核实的候选框（不含身份结论）。"""
    prepared = _normalized_image(image)
    height, width = prepared.shape[:2]
    content = _chat(base_url, model, DETECT_PROMPT, _encode_jpeg(prepared), timeout)
    parsed = extract_json(content)
    objects = parsed.get('objects')
    if not isinstance(objects, list):
        raise VisionError('m4d_model_bad_response')
    candidates: list[dict] = []
    for index, item in enumerate(objects[:MAX_BOXES]):
        if not isinstance(item, dict):
            continue
        box = _clean_box(item.get('box'), width, height)
        if box is None:
            continue
        label = str(item.get('label') or '').strip()[:40]
        category = str(item.get('category') or '').strip().lower()
        if category not in ('person', 'vehicle', 'other'):
            category = infer_category(label)
        label = label or category
        candidates.append({
            'proposal_index': index,
            'object_class': category,
            'detector_label': label,
            # 视觉模型不给分数：confidence 保持 None，界面按「未评分建议」展示
            'confidence': None,
            'bbox_normalized': box,
            'status': 'unverified_vlm_proposal',
        })
    return _dedupe_boxes(candidates)


def _clean_features(values) -> list[str]:
    """把模型的各种写法统一成短语列表：['"[\\"白色\\""'] 这类嵌套 JSON 也要拆开。"""
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if text[0] in '[{"':
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                out.extend(_clean_features(parsed))
                continue
            if isinstance(parsed, dict):
                out.extend(_clean_features(parsed.values()))
                continue
            if isinstance(parsed, str):
                text = parsed
        text = text.strip(' "\'[]{}，,。;；')
        if text and text not in out:
            out.append(text)
    return out


def describe_roi(image: np.ndarray, box: list[float], *, base_url: str, model: str,
                 timeout: float = 120, object_class: str = "person",
                 prompt_override: str | None = None) -> list[str]:
    """Crop from ORIGINAL pixels before resizing only the ROI.

    Whole-frame downsizing previously destroyed small vehicle details; ROI-first
    keeps them until the selected crop itself exceeds the VLM input width cap.
    """
    if image is None or image.ndim != 3:
        raise VisionError("m4d_source_decode_failed")
    height, width = image.shape[:2]
    pad_x, pad_y = (box[2] - box[0]) * 0.06, (box[3] - box[1]) * 0.06
    left = max(0, int((box[0] - pad_x) * width))
    top = max(0, int((box[1] - pad_y) * height))
    right = min(width, int(np.ceil((box[2] + pad_x) * width)))
    bottom = min(height, int(np.ceil((box[3] + pad_y) * height)))
    if right - left < 16 or bottom - top < 16:
        raise VisionError('m4d_roi_too_small')
    crop = _normalized_image(image[top:bottom, left:right])
    prompt = effective_feature_prompt(object_class, prompt_override)
    content = _chat(base_url, model, prompt, _encode_jpeg(crop, 90), timeout)
    try:
        parsed = extract_json(content)
    except VisionError:
        # 2B 模型经常无视 schema 直接写一句话：切成短语照样能用
        fragments = _fragments(content)
        if fragments:
            return fragments
        raise
    features = parsed.get('features')
    if isinstance(features, list):
        cleaned = _clean_features(features)
        if cleaned:
            return cleaned
    # 模型自造字段（color / clothing / posture ...）也收下，只要值是人能读的文本
    values = [v for v in parsed.values() if isinstance(v, str)]
    flattened = _clean_features(values)
    if flattened:
        return flattened
    raise VisionError('m4d_model_bad_response')
