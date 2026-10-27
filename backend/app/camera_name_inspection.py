"""M1 name ROI checks. All candidates remain unverified; never auto-bind UUID."""
from __future__ import annotations

from typing import Callable
import numpy as np
from scripts.camera_osd_name_quality import summarize_name_rows

ROI_PRESETS = {
    'right_bottom': (0.47, 0.84, 1.0, 1.0),
    'right_bottom_tight': (0.54, 0.90, 1.0, 1.0),
}

def inspect_frame(frame: np.ndarray, recognize: Callable) -> dict:
    if frame is None or frame.ndim != 3 or frame.shape[0] < 40 or frame.shape[1] < 160:
        return {'status': 'roi_frame_unavailable', 'camera_uuid': None,
                'no_auto_binding': True, 'candidates': [], 'warnings': ['invalid_roi_frame'],
                'raw_ocr_rows': []}
    h, w = frame.shape[:2]
    observations, all_rows = [], []
    for preset, (x1,y1,x2,y2) in ROI_PRESETS.items():
        left, top, right, bottom = round(x1*w), round(y1*h), round(x2*w), round(y2*h)
        crop = frame[top:bottom, left:right].copy()
        if not crop.size:
            continue
        for row in recognize(crop)[:30]:
            raw = str(row.get('text','')).strip()[:160]
            if not raw:
                continue
            try:
                score = float(row.get('confidence',0))
            except (TypeError, ValueError):
                score = 0.0
            if not 0 <= score <= 1:
                score = 0.0
            entry = {'text':raw, 'preset':preset, 'confidence':round(score,4),
                     'roi_pixels':[left,top,right,bottom]}
            if len(all_rows) < 45:
                all_rows.append(entry)
            if score >= .30:
                observations.append(entry)
    if observations:
        result = summarize_name_rows(observations)
    else:
        result = {'status': 'no_structured_name_hint', 'candidates': [],
                  'warnings': ['roi_ocr_empty_or_low_score'],
                  'raw_suspect_observations': [], 'camera_uuid': None,
                  'no_auto_binding': True}
    result['raw_ocr_rows'] = all_rows
    result['roi_presets'] = list(ROI_PRESETS)
    result['verification'] = 'ocr_name_hint_only_not_camera_identity'
    result['camera_uuid'] = None
    result['no_auto_binding'] = True
    return result

def apply_quality_to_camera(camera:dict, result:dict) -> None:
    camera['legacy_overlay_name_raw'] = camera.get('overlay_name_raw')
    camera['legacy_camera_candidates'] = list(camera.get('camera_candidates') or [])
    camera['name_roi_analysis'] = result
    camera['camera_candidates'] = []
    camera['name_candidates_for_directory'] = []
    if result.get('status') == 'candidate_unverified':
        hints = [i.get('name_candidate_unverified') for i in result.get('candidates',[])
                 if i.get('name_candidate_unverified')]
        if len(hints)==1 and not result.get('warnings'):
            camera['camera_candidates'] = hints
            camera['name_candidates_for_directory'] = hints
            camera['match_status'] = 'name_roi_single_candidate_unbound'
            return
    camera['match_status'] = 'name_roi_requires_review'
