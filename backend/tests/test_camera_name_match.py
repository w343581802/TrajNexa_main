"""名称相似建议：只读、只建议，不绑定 UUID；用固定目录，不连数据库。"""
from app.camera_catalog import CameraCatalog, annotate_camera_report
from app.camera_name_match import MIN_SCORE, name_score, normalize_name, suggest_names

CATALOG = [
    {'camera_uuid': 'u-1', 'display_name': '3-DX-金水路40号恒利花园门口-向东',
     'aliases': [], 'active': True, 'map_camera_id': 9000000000000023},
    {'camera_uuid': 'u-2', 'display_name': 'CAM-10 学校围墙',
     'aliases': ['学校围墙10'], 'active': True, 'map_camera_id': 9000000000000024},
    {'camera_uuid': 'u-3', 'display_name': 'BTS-临桂花生唐红绿灯北口',
     'aliases': [], 'active': False},
]


def test_normalize_ignores_separators_and_width():
    assert normalize_name('ＣＡＭ－１０  学校 围墙') == normalize_name('CAM-10 学校围墙') == 'cam10学校围墙'


def test_homoglyph_typo_scores_as_exact():
    assert name_score('CAM-1O 学校围墙', 'CAM-10 学校围墙') == 1.0
    assert name_score('3-DX-金水路4O号恒利花园门口-向东', '3-DX-金水路40号恒利花园门口-向东') == 1.0


def test_single_character_typo_still_suggests_right_camera():
    suggestions = suggest_names(['3-DX-金水路40号恒利花园门口-向乐'], CATALOG)
    assert suggestions and suggestions[0]['camera_uuid'] == 'u-1'
    assert suggestions[0]['score'] > MIN_SCORE
    assert suggestions[0]['requires_human_confirmation'] is True
    assert suggestions[0]['method'] == 'homoglyph_folded_ratio'


def test_alias_is_searchable():
    suggestions = suggest_names(['学校围墙1O'], CATALOG)
    assert suggestions and suggestions[0]['camera_uuid'] == 'u-2'


def test_unrelated_name_returns_nothing():
    assert suggest_names(['摄像头1号门口'], CATALOG) == []
    assert suggest_names(['CAM-99 UNKNOWN GATE'], CATALOG) == []


def test_inactive_camera_is_never_suggested():
    assert suggest_names(['BTS-临桂花生唐红绿灯北口'], CATALOG) == []


def test_empty_input_is_safe():
    assert suggest_names([], CATALOG) == []
    assert suggest_names([None, '   '], CATALOG) == []


def _report(**camera):
    return {'camera': {'overlay_name_raw': None, 'camera_candidates': [],
                       'match_status': 'labelled_candidate_unbound', **camera}}


def test_annotate_adds_suggestions_without_binding_uuid():
    catalog = CameraCatalog('rev-1', 'sha-1', CATALOG)
    report = _report(overlay_name_raw='CAM-1O 学校围墙', camera_candidates=['CAM-1O 学校围墙'])
    annotate_camera_report(report, catalog)
    camera = report['camera']
    assert camera['camera_uuid'] is None
    assert camera['match_status'] == 'labelled_candidate_unbound'
    assert camera['directory_candidates'] == []
    assert camera['name_suggestions'][0]['camera_uuid'] == 'u-2'
    assert camera['name_suggestions'][0]['display_name'] == 'CAM-10 学校围墙'
