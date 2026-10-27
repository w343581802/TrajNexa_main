"""M1c+: 摄像头名称的相似度候选（只做建议，不自动绑定）。

OCR 把角标名称认错一两个字符是常态（`0`/`O`、`1`/`I`/`l`、缺笔画、多空格）。
这里拿目录里的 display_name / aliases / 索引编码做兜底相似度比较：
  - 先做规范化精确匹配（与原逻辑一致，交给调用方）
  - 再做同形字折叠后的编辑相似度，按分数排序给出前几条
任何结果都只是“可能是这个名字”，绝不写 camera_uuid、不改变 match_status。
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# 只折叠最常混的一批：数字与字母互认
_HOMOGLYPHS = str.maketrans({
    'o': '0', 'i': '1', 'l': '1', 's': '5', 'b': '8', 'z': '2', 'g': '6',
})
_SEPARATORS = re.compile(r'[\s\-_—–·•|/\\,，。:：;；()（）\[\]【】<>《》"\']+')

# 低于这个分数不给建议；宁可少给，也不要把无关镜头塞给用户
MIN_SCORE = 0.78
MAX_SUGGESTIONS = 3


def normalize_name(raw: str) -> str:
    """去分隔符 + NFKC + casefold，用于精确比较。"""
    value = unicodedata.normalize('NFKC', raw or '').casefold()
    return _SEPARATORS.sub('', value)


def fold_homoglyphs(text: str) -> str:
    return text.translate(_HOMOGLYPHS)


def name_score(left: str, right: str) -> float:
    """0~1 的相似度：规范化相同为 1；否则比同形字折叠后的字符串相似度。"""
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, fold_homoglyphs(a), fold_homoglyphs(b)).ratio()


def suggest_names(raw_names, catalog_cameras, *, min_score: float = MIN_SCORE,
                  limit: int = MAX_SUGGESTIONS) -> list[dict]:
    """给 OCR 名称找目录里的相似项。只读、只建议，返回按分数倒序的前 N 条。

    raw_names: 本次 OCR 得到的名称候选（可为多帧结果）
    catalog_cameras: [{'camera_uuid','display_name','aliases',...}]
    """
    candidates = [x.strip() for x in raw_names if isinstance(x, str) and x.strip()]
    if not candidates:
        return []
    best: dict[str, dict] = {}
    for camera in catalog_cameras:
        if not isinstance(camera, dict) or camera.get('active') is False:
            continue
        values = [camera.get('display_name') or '']
        values.extend(camera.get('aliases') or [])
        # 故意不拿 index_code / external_index_code 做相似度：实测台账里大量镜头共用同一个编号，
        # 放进来会让几十台镜头同时 1.0 分，等于制造误报。精确匹配编号由调用方的精确逻辑负责。
        for value in values:
            if not value:
                continue
            for raw in candidates:
                score = name_score(raw, value)
                if score < min_score:
                    continue
                current = best.get(camera['camera_uuid'])
                if current and current['score'] >= score:
                    continue
                best[camera['camera_uuid']] = {
                    'camera_uuid': camera['camera_uuid'],
                    'display_name': camera.get('display_name') or value,
                    'matched_value': value,
                    'ocr_text': raw,
                    'score': round(score, 3),
                    'method': 'homoglyph_folded_ratio',
                    'map_camera_id': camera.get('map_camera_id'),
                    # 台账是否人工审核过：没审核过的名称只是“台账写法”，不构成核实结论
                    'reviewed': bool(camera.get('reviewed_at')),
                    'requires_human_confirmation': True,
                }
    ordered = sorted(best.values(), key=lambda item: item['score'], reverse=True)
    return ordered[:limit]


def nearest_name(raw_names, catalog_cameras) -> dict | None:
    """最高分的那一条（可能低于阈值），用于解释“为什么没有相近建议”。"""
    hits = suggest_names(raw_names, catalog_cameras, min_score=0.01, limit=1)
    return hits[0] if hits else None
