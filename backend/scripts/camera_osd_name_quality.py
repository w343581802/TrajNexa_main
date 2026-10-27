"""Conservative CCTV on-screen camera label analysis.

Format checks and OCR similarity are not proof of a device identity.
Raw observations (including clipped and garbled text) must be preserved.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata

PREFIX = re.compile(r'^(?:\d{1,4}-)?(?:DX|HIK|CAM|IPC|HK|DH|CH|CHN|NVR)-', re.I)
END = re.compile(r'(?:-?(?:向[东南西北]|球机?|枪机?|半球|固定))$', re.I)
PLACE = re.compile(r'[路街巷园号栋楼区门口院]')
CODE_LIKE = re.compile(r'(?:DX|HIK|CAM|IPC|HK|DH|CH|CHN|NVR)', re.I)
E_BUILDING = re.compile(r'E\d+(?:栋|楼)', re.I)
DIRECTION = re.compile(r'向[东西南北]')


def normalize(raw: str) -> str:
    s = unicodedata.normalize('NFKC', str(raw)).strip()
    s = re.sub(r'[—－–_]+', '-', s)
    s = re.sub(r'\s*[-]\s*', '-', s)
    s = re.sub(r'^(\d{1,4})\s+(?=[A-Za-z]{2,6}\b)', r'\1-', s)
    s = re.sub(r'^((?:\d{1,4}-)?(?:DX|HIK|CAM|IPC|HK|DH|CH|CHN|NVR))\s+(?=[\u4e00-\u9fff])', r'\1-', s, flags=re.I)
    return s.strip()


def comparable(raw: str) -> str:
    return re.sub(r'[\s\-:：·|]+', '', normalize(raw).casefold())


def describe(raw: str) -> dict:
    value = normalize(raw)
    if not value:
        return {'quality': 'scene_text', 'reason': 'empty'}
    if re.match(r'^-(?:DX|HIK|CAM|IPC|HK|DH|CH|CHN|NVR)-', value, re.I):
        return {'quality': 'clipped_left', 'reason': 'camera_prefix_missing_left_edge'}
    m = PREFIX.match(value)
    if not m:
        return {'quality': 'malformed_name' if CODE_LIKE.search(value) else 'scene_text',
                'reason': 'invalid_device_prefix'}
    body = value[m.end():]
    if len(body) < 4 or len(body) > 96 or len(re.findall(r'[\u4e00-\u9fff]',body)) < 2:
        return {'quality': 'malformed_name', 'reason': 'insufficient_location_content'}
    if not END.search(body):
        return {'quality': 'incomplete_name', 'reason': 'missing_or_corrupted_camera_suffix'}
    if not PLACE.search(body):
        return {'quality': 'incomplete_name', 'reason': 'location_anchor_missing'}
    if re.search(r'[\u4e00-\u9fff]\d+路', body):
        return {'quality': 'suspicious_name', 'reason': 'digits_embedded_in_street_name'}
    return {'quality': 'structured_candidate', 'reason': 'syntax_only_no_catalog_confirmation'}


def _protected_conflict(a: str,b: str) -> bool:
    # Distinct camera orientations and E40/E41 building codes must not be
    # collapsed based on edit distance alone.
    x,y = normalize(a), normalize(b)
    for pat in (DIRECTION,E_BUILDING):
        s,t = pat.findall(x),pat.findall(y)
        if s and t and [i.casefold() for i in s] != [i.casefold() for i in t]:
            return True
    # Two full camera suffixes of different types are also distinct hints.
    sa,sb = END.search(x), END.search(y)
    if sa and sb and sa.group(0).lstrip('-') != sb.group(0).lstrip('-'):
        return True
    return False


def _similar(a: str,b: str) -> bool:
    if _protected_conflict(a,b):
        return False
    x,y = comparable(a),comparable(b)
    return bool(x and y and SequenceMatcher(None,x,y,autojunk=False).ratio()>=0.73)


def summarize_name_rows(rows: list[dict]) -> dict:
    seen: dict[str,dict] = {}
    for row in rows:
        if row.get('preset') == 'bottom_context':
            continue
        raw = str(row.get('text','')).strip()
        if not raw:
            continue
        result = describe(raw)
        if result['quality'] == 'scene_text':
            continue
        key = comparable(raw)
        if not key:
            continue
        item = seen.setdefault(key,dict(text=raw,quality=result['quality'],reason=result['reason'],
                                        observed_presets=set(),ocr_variants=[]))
        item['observed_presets'].add(row.get('preset','unknown'))
        if raw not in item['ocr_variants']:
            item['ocr_variants'].append(raw)
        # If same comparable name has good and clipped variants, don't drop diagnostic.
        if item['quality'] != 'structured_candidate' and result['quality'] == 'structured_candidate':
            item['quality'] = 'structured_candidate'
            item['reason'] = result['reason']
    observations = list(seen.values())
    strong = [x for x in observations if x['quality'] == 'structured_candidate']
    weak = [x for x in observations if x['quality'] != 'structured_candidate']
    # Connect close variants only if camera-type, direction and building-code
    # anchors are not contradictory. Do NOT synthesize a corrected name.
    groups: list[list[dict]] = []
    for item in strong + weak:
        matching = [group for group in groups if any(_similar(item['text'], other['text']) for other in group)]
        if matching:
            target=matching[0]
            target.append(item)
            for group in matching[1:]:
                target.extend(group)
                groups.remove(group)
        else:
            groups.append([item])
    group_output=[]
    warnings=[]
    for group in groups:
        accepted=[i for i in group if i['quality']=='structured_candidate']
        # Never infer that a malformed/partial observation has the same true name.
        name = accepted[0]['text'] if accepted else None
        g={
            'name_candidate_unverified': name,
            'raw_variants': [x['text'] for x in group],
            'qualities': sorted(set(x['quality'] for x in group)),
            'roi_presets': sorted(set(p for x in group for p in x['observed_presets'])),
            'requires_human_confirmation': True,
        }
        group_output.append(g)
        if len(group)>1:
            warnings.append('ocr_spelling_variations_unverified')
        if len({tuple(re.findall(r'\d+',x['text'])) for x in group})>1:
            warnings.append('numeric_tokens_differ_across_ocr_variants')
    if any(x['quality']=='clipped_left' for x in weak):
        warnings.append('roi_left_edge_clipping')
    if any(x['quality'] in ('malformed_name','suspicious_name','incomplete_name') for x in weak):
        warnings.append('incomplete_or_low_quality_name_text')
    if not strong:
        status = 'low_quality_name_requires_review' if weak else 'no_structured_name_hint'
    elif len([g for g in groups if any(x['quality']=='structured_candidate' for x in g)]) > 1:
        status = 'conflicting_name_candidates_review'
    elif len(groups)>1 and weak:
        status = 'candidate_with_roi_warning'
    elif any(x['quality']=='clipped_left' for x in weak):
        status = 'candidate_with_roi_warning'
    elif len(observations)>1:
        status = 'ocr_variants_require_review'
    elif weak:
        status = 'candidate_with_roi_warning'
    else:
        status = 'candidate_unverified'
    return {
        'status': status,
        'candidates': group_output,
        'warnings': sorted(set(warnings)),
        'raw_suspect_observations': [{'text':x['text'],'quality':x['quality'],
                                     'reason':x['reason']} for x in weak],
        'camera_uuid':None,
        'no_auto_binding':True,
        'verification': 'format_and_similarity_only_not_equipment_identity',
    }
