"""B8-P1 单元测试：镜头解析、视域中心、输入校验。

不碰数据库与 Map 库：这些用例保护「不猜」的边界——
名称歧义要报错、视域缺失要降级、像素/帧尺寸非法要拒绝。
"""
import pytest

from app import element_anchor as anchor


def _cameras():
    return [
        {'camera_uuid': 'u-1', 'display_name': '1-LT-桂康路松山巷-向北',
         'location': {'lon': 110.2100, 'lat': 25.2500},
         'range_m': 30.0, 'pose_quality': 'calibrated',
         'fov_levels': [{'level': 'observation', 'geometry': {
             'type': 'MultiPolygon',
             'coordinates': {'value': [[[
                 [110.2100, 25.2500], [110.2099, 25.2497],
                 [110.2101, 25.2497], [110.2100, 25.2500]]]]}}}]},
        {'camera_uuid': 'u-2', 'display_name': '重名镜头',
         'location': {'lon': 110.2110, 'lat': 25.2510}, 'range_m': None,
         'fov_levels': []},
        {'camera_uuid': 'u-3', 'display_name': '重名镜头',
         'location': {'lon': 110.2120, 'lat': 25.2520}, 'range_m': 20.0,
         'fov_levels': []},
        {'camera_uuid': 'u-4', 'display_name': '无坐标镜头',
         'location': None, 'fov_levels': []},
    ]


def test_resolve_camera_by_uuid_wins_over_name():
    camera, source = anchor.resolve_camera({'cameras': _cameras()}, 'u-1', '重名镜头')
    assert camera['camera_uuid'] == 'u-1' and source == 'catalog'


def test_resolve_camera_by_unique_name():
    camera, source = anchor.resolve_camera({'cameras': _cameras()}, None,
                                           '1-LT-桂康路松山巷-向北')
    assert camera['camera_uuid'] == 'u-1' and source == 'name_match'


def test_resolve_camera_ambiguous_name_must_not_guess():
    with pytest.raises(anchor.AnchorError) as excinfo:
        anchor.resolve_camera({'cameras': _cameras()}, None, '重名镜头')
    assert excinfo.value.code == 'camera_name_ambiguous'


def test_resolve_camera_unknown_name_returns_none():
    camera, source = anchor.resolve_camera({'cameras': _cameras()}, None, '不存在的镜头')
    assert camera is None and source == 'none'


def test_fov_point_is_inside_polygon_and_bounded():
    camera = _cameras()[0]
    found = anchor._fov_point(camera)
    assert found['projection_level'] == 'L1'
    assert found['quality'] == 'fov_centroid_unverified'
    point = found['ground_point']
    assert point['crs'] == 'EPSG:4326'
    assert 110.2099 <= point['lon'] <= 110.2101
    assert 25.2497 <= point['lat'] <= 25.2500
    assert 5.0 <= found['uncertainty_radius_m'] <= anchor.MAX_RADIUS_M


def test_fov_point_without_geometry_returns_none():
    assert anchor._fov_point({'fov_levels': []}) is None


def test_camera_fallback_uses_range_then_default():
    camera = {'location': {'lon': 110.21, 'lat': 25.25}, 'range_m': 30.0}
    found = anchor._camera_fallback(camera)
    assert found['projection_level'] == 'L0' and found['uncertainty_radius_m'] == 30.0
    assert anchor._camera_fallback({'location': {'lon': 110.21, 'lat': 25.25}})['uncertainty_radius_m'] == 40.0


def test_camera_fallback_needs_position():
    assert anchor._camera_fallback({'location': None}) is None


def test_locate_degrades_instead_of_inventing():
    with pytest.raises(anchor.AnchorError) as excinfo:
        anchor.locate({'cameras': []}, _cameras()[3], None, None)
    assert excinfo.value.code == 'camera_position_missing'


def test_capture_local_normalises_and_rejects():
    assert anchor._capture('2026-10-23T15:00') == '2026-10-23T15:00:00'
    assert anchor._capture('') is None
    with pytest.raises(anchor.AnchorError):
        anchor._capture('2026-10-23 15:00:00')


def test_pixel_and_frame_size_validation():
    assert anchor._pixel([10, 20.5]) == [10.0, 20.5]
    with pytest.raises(anchor.AnchorError):
        anchor._pixel([1])
    with pytest.raises(anchor.AnchorError):
        anchor._pixel([True, 2])
    assert anchor._frame_size([2560, 1440]) == [2560.0, 1440.0]
    with pytest.raises(anchor.AnchorError):
        anchor._frame_size([0, 1440])