"""No real PaddleOCR model; boundary tests use injected observations."""
import cv2
import numpy as np
from scripts.benchmark_camera_name_roi import code_hint, canonical, rows_from_prediction, summary, crop, run

def test_structured_name_vs_scene_text():
    assert code_hint('3-DX-中仁路98号-球')
    assert code_hint('3 DX 中仁路孔明园E40栋庭院向北')
    assert code_hint('DX-中仁路孔明园E40栋向北')
    for s in ['C-D80119','C-WF598','桂林临桂','2026-10-23 15:00:00','3-DX-']:
        assert not code_hint(s)

def test_unicode_and_json_boxes():
    assert canonical('３－ＤＸ－中仁路')==canonical('3-DX-中仁路')
    rows=rows_from_prediction([{'res':{'rec_texts':['C-D80119','3-DX-中仁路98号-球'],
        'rec_scores':[.999,.85],'rec_polys':[[[0,0],[30,0],[30,10],[0,10]],[[0,25],[60,25],[60,40],[0,40]]]}}])
    assert len(rows)==2 and not rows[0]['camera_code_hint']
    assert rows[1]['polygon_roi'][0]==[0,25]

def test_conflicting_names_never_bind():
    rows=[dict(text='3-DX-中仁路98号-球',preset='right_bottom',camera_code_hint=True),
          dict(text='3-DX-中仁路98号-向北',preset='right_bottom_tight',camera_code_hint=True)]
    x=summary(rows)
    assert x['status']=='conflicting_name_candidates_review'
    assert x['camera_uuid'] is None

def test_background_only_does_not_name_camera():
    x=summary([dict(text='3-DX-中仁路98号-球',preset='bottom_context',camera_code_hint=True)])
    assert x['status']=='no_structured_name_hint'

def test_injected_pipeline(tmp_path):
    img=np.zeros((1080,1920,3),np.uint8)
    patch,rect=crop(img,(.54,.90,1.,1.))
    assert patch.shape[0]>=100 and rect[3]==1080
    path=tmp_path/'demo.png';cv2.imwrite(str(path),img)
    class Fake:
        def predict(self, _):
            return [{'res':{'rec_texts':['3-DX-中仁路98号-球','C-D80119'],
                            'rec_scores':[.85,.99],'rec_polys':[]}}]
    report=run(path,predictor=Fake())
    assert report['summary']['status']=='candidate_unverified'
    assert len(report['raw_ocr_rows'])==12
    assert report['production_ocr_not_modified']
