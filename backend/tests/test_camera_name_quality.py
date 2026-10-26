from scripts.camera_osd_name_quality import describe, summarize_name_rows


def rows(names):
    return [dict(text=s,preset='right_bottom' if i % 2 == 0 else 'right_bottom_tight') for i,s in enumerate(names)]


def test_two_real_world_ocr_variant_groups_are_not_two_devices():
    samples = [
        ['3-DX-中仁路98号-球','3-DX-中仁98号-球','3-DX-中1路8号-球'],
        ['3-DX-中仁路孔明园E41栋 问北','3-DX-中仁路孔明园E41栋 向北','3-DX-中仁路孔明园E41栋'],
    ]
    for raw in samples:
        result=summarize_name_rows(rows(raw))
        assert result['status']=='ocr_variants_require_review'
        assert len(result['candidates'])==1
        assert len(result['candidates'][0]['raw_variants'])==3
        assert result['camera_uuid'] is None
    assert 'numeric_tokens_differ_across_ocr_variants' in summarize_name_rows(rows(samples[0]))['warnings']


def test_single_garbled_candidate_must_not_be_accepted():
    s='DX电仁路孔L明园E40东庭向北工'
    assert describe(s)['quality']=='malformed_name'
    result=summarize_name_rows(rows([s]))
    assert result['status']=='low_quality_name_requires_review'
    assert result['candidates'][0]['name_candidate_unverified'] is None


def test_left_clipped_candidate_never_disappears_without_warning():
    good='3-DX-中仁路孔明园E40栋-向西'
    bad='-DX-中二路孔明园E40栋一同四'
    result=summarize_name_rows(rows([good,bad]))
    assert result['status']=='candidate_with_roi_warning'
    assert 'roi_left_edge_clipping' in result['warnings']
    assert any(x['text']==bad for x in result['raw_suspect_observations'])
    assert result['camera_uuid'] is None


def test_equipment_suffix_or_building_code_difference_is_not_ocr_variant():
    for a,b in [
        ('3-DX-中仁路孔明园E40栋-向北','3-DX-中仁路孔明园E41栋-向北'),
        ('3-DX-中仁路孔明园E40栋-向北','3-DX-中仁路孔明园E40栋-向南'),
        ('3-DX-中仁路98号-球','3-DX-中仁路98号-向北'),
    ]:
        result=summarize_name_rows(rows([a,b]))
        assert result['status']=='conflicting_name_candidates_review'
        assert len(result['candidates'])==2


def test_scene_text_does_not_create_camera_name():
    out=summarize_name_rows(rows(['C-D80119','C-WF598','无量寿经 无病而']))
    assert out['status']=='no_structured_name_hint'
    assert not out['candidates']


def test_valid_name_remains_unverified():
    out=summarize_name_rows(rows(['3-DX-中仁路孔明园E40栋-向南']))
    assert out['status']=='candidate_unverified'
    assert out['no_auto_binding'] is True
    assert out['candidates'][0]['requires_human_confirmation'] is True
