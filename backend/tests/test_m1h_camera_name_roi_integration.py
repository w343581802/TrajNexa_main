"""Deterministic name ROI safety checks. No PP-OCR model is invoked."""
from pathlib import Path
from types import SimpleNamespace
import cv2
import numpy as np
import pytest
from app.camera_name_inspection import inspect_frame, apply_quality_to_camera
from app.camera_catalog import CameraCatalog, annotate_camera_report
from app.overlay_ocr import analyze_overlay
from app.config import settings

def catalog():
    return CameraCatalog('rev-demo', 'local-fixture', [{
        'camera_uuid':'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa',
        'display_name':'3-DX-中仁路98号-球','aliases':[],'active':True}])

def test_valid_roi_with_two_regions_keeps_unbound_uuid():
    shape = []
    def predict(crop):
        shape.append(crop.shape[:2])
        return [{'text':'3-DX-中仁路98号-球','confidence':.97}]
    data = inspect_frame(np.zeros((1080,1920,3),np.uint8), predict)
    assert shape == [(173,1018),(108,883)]
    assert data['status'] == 'candidate_unverified'
    assert data['camera_uuid'] is None
    assert len(data['raw_ocr_rows'])==2

def test_directory_sees_only_clean_roi_not_raw_legacy_guess():
    cam = {'overlay_name_raw':'wrong', 'camera_candidates':['wrong'], 'camera_uuid':None}
    quality = {'status':'candidate_unverified','warnings':[],
               'candidates':[{'name_candidate_unverified':'3-DX-中仁路98号-球'}]}
    apply_quality_to_camera(cam,quality)
    result = annotate_camera_report({'camera':cam},catalog())['camera']
    assert result['legacy_overlay_name_raw']=='wrong'
    assert result['directory_status']=='single_candidate_unconfirmed'
    assert result['camera_uuid'] is None

@pytest.mark.parametrize('status', ['low_quality_name_requires_review',
                   'ocr_variants_require_review','conflicting_name_candidates_review',
                   'candidate_with_roi_warning','name_roi_error_requires_review'])
def test_low_quality_or_conflicting_roi_blocks_directory(status):
    cam = {'overlay_name_raw':'3-DX-中仁路98号-球',
           'camera_candidates':['3-DX-中仁路98号-球']}
    apply_quality_to_camera(cam, {'status':status,'warnings':['review'],
          'candidates':[{'name_candidate_unverified':'3-DX-中仁路98号-球'}]})
    result = annotate_camera_report({'camera':cam},catalog())['camera']
    assert result['directory_candidates']==[]
    assert result['directory_status']=='name_requires_review'
    assert result['camera_uuid'] is None

def test_unrelated_scene_text_is_not_promoted():
    roi=inspect_frame(np.zeros((1080,1920,3),np.uint8),
                      lambda x:[{'text':'C-D80119','confidence':.99}])
    assert roi['status']=='no_structured_name_hint'
    assert roi['raw_ocr_rows']

def test_injected_legacy_ocr_preserves_old_flow(tmp_path,monkeypatch):
    path=tmp_path/'demo.png'
    cv2.imwrite(str(path),np.full((100,200,3),110,np.uint8))
    monkeypatch.setattr(settings,'name_roi_enabled',True)
    report={'time':{},'camera':{},'quality':{'decode_status':'ok'},
            'media':{'technical_metadata':{}}}
    analyze_overlay(SimpleNamespace(kind='image'),path,report,
                    recognizer=lambda x:[{'text':'摄像头:样例', 'confidence':.9}])
    assert 'name_roi_analysis' not in report['camera']

def test_injected_name_roi_integration(tmp_path,monkeypatch):
    path=tmp_path/'demo.png'
    cv2.imwrite(str(path),np.full((1080,1920,3),110,np.uint8))
    monkeypatch.setattr(settings,'name_roi_enabled',True)
    report={'time':{},'camera':{},'quality':{'decode_status':'ok'},
            'media':{'technical_metadata':{}}}
    def recognize(x):return [{'text':'3-DX-中仁路98号-球','confidence':.9}]
    analyze_overlay(SimpleNamespace(kind='image'),path,report,
                    recognizer=recognize,name_roi_recognizer=recognize)
    assert report['camera']['match_status']=='name_roi_single_candidate_unbound'
    assert report['camera']['legacy_overlay_name_raw']=='3-DX-中仁路98号-球'
    assert report['camera']['camera_uuid'] is None
