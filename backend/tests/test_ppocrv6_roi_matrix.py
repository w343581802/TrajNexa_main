"""Integration-shape tests with a fake predictor; no real OCR model used."""
from pathlib import Path
import cv2
import numpy as np
from scripts.benchmark_ppocrv6_roi_matrix import PRESETS, summarize, run_matrix


def test_presets_narrower_than_prior_wide():
    assert PRESETS['tight'][1] == PRESETS['compact'][1] == 0
    assert PRESETS['tight'][3] < PRESETS['wide'][3]
    assert PRESETS['compact'][2] < PRESETS['wide'][2]


def test_conflicts_not_promoted_to_fact():
    records = [dict(roi_preset='wide',parsed_clock_unverified='2026-10-03T15:17:19'),
               dict(roi_preset='tight',parsed_clock_unverified='2026-10-23T15:17:19')]
    summary=summarize(records)
    assert summary['status']=='conflicting_candidates_require_review'
    assert summary['confirmed_capture_time'] is None


def test_one_model_cached_across_all_roi_variants(tmp_path):
    path=tmp_path/'synthetic.png'
    cv2.imwrite(str(path), np.full((1080,1920,3),75,np.uint8))
    init=[]
    class Fake:
        def predict(self,input,batch_size):
            return [{'res':{'rec_text':'2026-10-23 15:17:19','rec_score':.88}}]
    def factory(name):
        init.append(name)
        return Fake()
    report=run_matrix(path,predictor_factory=factory,expected='2026-10-23T15:17:19',
                      preview_dir=tmp_path/'preview')
    assert init==['PP-OCRv6_medium_rec']
    assert len(report['results'])==12
    assert report['summary']['status']=='single_candidate_unverified'
    assert report['summary']['manual_truth_match_count']==12
    assert report['summary']['confirmed_capture_time'] is None
    assert (tmp_path/'preview/tight.png').exists()
    assert report['production_ocr_not_modified']
