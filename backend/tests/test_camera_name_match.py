"""名称相似建议：只读、只建议，不绑定 UUID；用固定目录，不连数据库。"""
from app.camera_catalog import CameraCatalog, annotate_camera_report
from app.camera_name_match import MIN_SCORE, name_score, nearest_name, normalize_name, suggest_names

CATALOG = [
    {'camera_uuid': 'u-1', 'display_name': '3-DX-金水路40号恒利花园门口-向东',
     'aliases': [], 'active': True, 'map_camera_id': 9000000000000023},
    {'camera_uuid': 'u-2', 'display_name': 'CAM-10 学校围墙',
     'aliases': ['学校围墙10'], 'active': True, 'map_camera_id': 9000000000000024},
    {'camera_uuid': 'u-3', 'display_name': 'BTS-临桂花生唐红绿灯北口',
     'aliases': [], 'active': False},
    {'camera_uuid': 'u-4', 'display_name': '3-DX-金水路40号恒利花园门口-向西',
     'aliases': [], 'active': True, 'map_camera_id': 9000000000000025,
     'reviewed_at': '2026-10-01T08:00:00+08:00', 'reviewed_by': 'operator'},
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


def test_suggestion_carries_map_review_state():
    """台账没审核过的名称必须标出来：它只是 Map 里的写法，不是核实结论。"""
    catalog = CameraCatalog('rev-1', 'sha-1', CATALOG, diagnostics={'reviewed_count': 1})
    report = _report(overlay_name_raw=None, camera_candidates=['3-DX-金水路40号恒利花园门口-向乐'])
    annotate_camera_report(report, catalog)
    camera = report['camera']
    by_uuid = {item['camera_uuid']: item for item in camera['name_suggestions']}
    assert by_uuid['u-1']['reviewed'] is False
    assert by_uuid['u-4']['reviewed'] is True
    assert camera['directory_total'] == 3          # u-3 停用，不计入
    assert camera['directory_reviewed'] == 1


def test_annotate_reports_nearest_when_nothing_is_close():
    catalog = CameraCatalog('rev-1', 'sha-1', CATALOG)
    report = _report(overlay_name_raw='XX-某路某号-北')
    annotate_camera_report(report, catalog)
    camera = report['camera']
    assert camera['name_suggestions'] == []
    nearest = camera['directory_nearest']
    assert nearest is not None and 0 < nearest['score'] < MIN_SCORE
    assert camera['camera_uuid'] is None


def test_nearest_name_returns_best_below_threshold():
    nearest = nearest_name(['3-DX-金水路40号恒利花园门口-北'], CATALOG)
    assert nearest and nearest['camera_uuid'] in {'u-1', 'u-4'}
    assert nearest['score'] < 1.0
