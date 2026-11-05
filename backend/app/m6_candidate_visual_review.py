"""B8-C1：跨镜头候选的大模型视觉复核（observe_only）。

对同一个候选，把「起点目标裁剪 + 起点上下文」与「候选裁剪 + 候选上下文」一起交给
Qwen3-VL，让它逐维度给出 一致 / 冲突 / 未知，而不是只读之前抽出的属性文本。

约束：
- 只输出可核查的外观证据，永不输出身份结论；identity_effect 恒为 none。
- 未知不是冲突：看不清只能记 unknown，不能据此否定候选。
- 综合结论由服务端按规则重算，不直接采信模型的总体判断。
"""
from __future__ import annotations

import json
import re
import time
from hashlib import sha256

from app import m4_vision, m6_attributes
from app.config import settings

PROMPT_VERSION = 'm6-candidate-review/1'
DIMENSIONS = (
    ('garment_color', '衣着颜色'),
    ('garment_type', '衣着类型'),
    ('head_appearance', '头部外观'),
    ('carried_item', '随身携带物'),
    ('vehicle_appearance', '车辆外观'),
    ('person_vehicle_relation', '人车关系'),
)
STATUSES = ('consistent', 'conflict', 'unknown')
OVERALL = ('supports', 'inconclusive', 'conflicts_present', 'insufficient_evidence')

PROMPT = (
    '这是同一监控目标在两个不同时间/地点的画面。图 1 是起点目标的人体裁剪，'
    '图 2 是起点的人车上下文；图 3 是候选目标的人体裁剪，图 4 是候选的人车上下文。'
    '请只比较画面中确实可见的外观，逐项判断「一致 / 冲突 / 未知」，并指出依据来自哪组图。'
    '不要让衣着相似直接证明身份；看不清、被遮挡、只看到局部、或候选画面里没有车辆，都必须记为 unknown。'
    '输出一行 JSON：'
    '{"image_quality":"sufficient|limited|insufficient",'
    '"dimensions":{"garment_color":{"status":"consistent|conflict|unknown","evidence":"简短依据","region":"A|B|A,B"},'
    '...同样结构覆盖 garment_type, head_appearance, carried_item, vehicle_appearance, person_vehicle_relation},'
    '"summary":"不超过 60 字的证据摘要"}。'
    '只描述可见外观，不要猜姓名、性别、年龄、身份，也不要解释。'
)


class ReviewError(ValueError):
    """稳定错误码，不含路径。"""


def _entry() -> dict | None:
    from app import model_capabilities
    try:
        plan = model_capabilities.plan_models('attribute_extraction', 'large_only',
                                              'm6.candidate_review')
    except Exception:  # noqa: BLE001 - 配置缺失不应中断追踪
        return None
    return plan[0] if plan else None


def _chat_multi(entry: dict, prompt: str, jpegs: list[bytes]) -> str:
    if entry.get('provider') != 'ollama':
        raise ReviewError('candidate_review_provider_unsupported')
    import base64
    payload = {
        'model': entry.get('version') or entry.get('model'),
        'stream': False,
        'messages': [{'role': 'user', 'content': prompt,
                      'images': [base64.b64encode(jpeg).decode('ascii') for jpeg in jpegs]}],
        'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 700,
                    'repeat_penalty': 1.1},
    }
    import urllib.request
    request = urllib.request.Request(
        settings.vision_base_url.rstrip('/') + '/api/chat',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(
                request, timeout=float(getattr(settings, 'vision_timeout_seconds', 120))) as response:
            body = json.loads(response.read().decode('utf-8'))
    except Exception as exc:  # noqa: BLE001
        raise ReviewError('candidate_review_transport_error') from exc
    message = (body.get('message') or {})
    return str(message.get('content') or '')


def _normalize_status(value) -> str:
    text = str(value or '').strip().lower()
    return text if text in STATUSES else 'unknown'


def parse_review(text: str) -> dict:
    match = re.search(r'\{.*\}', text or '', re.S)
    if not match:
        raise ReviewError('candidate_review_invalid_json')
    try:
        payload = json.loads(match.group(0))
    except (ValueError, TypeError):
        raise ReviewError('candidate_review_invalid_json') from None
    if not isinstance(payload, dict):
        raise ReviewError('candidate_review_invalid_json')
    raw_dimensions = payload.get('dimensions')
    if not isinstance(raw_dimensions, dict):
        raw_dimensions = {}
    dimensions = {}
    for key, label in DIMENSIONS:
        item = raw_dimensions.get(key)
        if not isinstance(item, dict):
            item = {}
        status = _normalize_status(item.get('status'))
        evidence = str(item.get('evidence') or '').strip()[:160]
        region = str(item.get('region') or '').strip()[:8]
        if status == 'consistent' and not evidence:
            status = 'unknown'
        dimensions[key] = {'label': label, 'status': status,
                           'evidence': evidence, 'region': region}
    quality = str(payload.get('image_quality') or '').strip().lower()
    if quality not in ('sufficient', 'limited', 'insufficient'):
        quality = 'unknown'
    return {'image_quality': quality,
            'dimensions': dimensions,
            'summary': str(payload.get('summary') or '').strip()[:200]}


def summarize(dimensions: dict, quality: str) -> str:
    statuses = [dimensions[key]['status'] for key, _ in DIMENSIONS]
    conflicts = statuses.count('conflict')
    consistent = statuses.count('consistent')
    if quality == 'insufficient' or (consistent == 0 and conflicts == 0):
        return 'insufficient_evidence'
    if conflicts:
        return 'conflicts_present'
    if consistent >= 3:
        return 'supports'
    return 'inconclusive'


def review_pair(anchor_image, anchor_box, candidate_image, candidate_box,
                entry: dict | None = None) -> dict:
    """双图复核；返回结构化证据，不改变任何身份状态。"""
    started = time.time()
    if anchor_image is None or candidate_image is None:
        raise ReviewError('candidate_review_image_unreadable')
    shots = []
    try:
        for scope, image, box in (
                ('anchor_person', anchor_image, anchor_box),
                ('anchor_context', anchor_image, anchor_box),
                ('candidate_person', candidate_image, candidate_box),
                ('candidate_context', candidate_image, candidate_box)):
            region = (m6_attributes._person_region(image, box) if scope.endswith('person')
                      else m6_attributes._context_region(image, box))
            shots.append(m4_vision._encode_jpeg(
                m6_attributes._crop(image, region, 'person' if scope.endswith('person') else 'context'), 88))
    except m6_attributes.AttributeError_ as exc:
        raise ReviewError(str(exc)) from exc
    chosen = entry or _entry()
    if not chosen:
        raise ReviewError('candidate_review_model_unavailable')
    prompt = PROMPT
    content = _chat_multi(chosen, prompt, shots)
    parsed = parse_review(content)
    parsed['overall'] = summarize(parsed['dimensions'], parsed['image_quality'])
    parsed.update({
        'schema': 'b8-candidate-visual-review/1',
        'prompt_version': PROMPT_VERSION,
        'prompt_sha256': sha256(prompt.encode('utf-8')).hexdigest(),
        'model': chosen.get('version') or chosen.get('model'),
        'provider': chosen.get('provider'),
        'identity_effect': 'none',
        'cost_ms': round((time.time() - started) * 1000.0, 1),
        'note': 'AI 复核只补充证据，不改变候选排名，也不写入人工确认。',
    })
    return parsed
