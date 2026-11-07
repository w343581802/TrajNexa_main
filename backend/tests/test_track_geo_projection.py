"""B8-SA5：逐帧样本 → 实验空间轨迹段（读取时现算，不写正式观测）。"""
from __future__ import annotations

from types import SimpleNamespace

from app import m6_track_geo_projection as geo


def _camera(**overrides) -> dict:
    camera = {
        'camera_uuid': 'u-track',
        'display_name': '3-DX-测试镜头',
        'location': {'lon': 110.2103, 'lat': 25.2500, 'crs': 'EPSG:4326'},
        'bearing_deg': 355.0,
        'tilt_deg': 25.0,
        'horizontal_fov_deg': 64.0,
        'mount_height_m': 4.0,
        'ray_projection_ready': True,
    }
    camera.update(overrides)
    return camera


def _row(t, box=(900.0, 500.0, 1020.0, 700.0), frame=(1920, 1080), track='w0:t1'):
    return SimpleNamespace(timestamp_seconds=t, frame_id=int(t * 5),
                           bbox_json=[float(v) for v in box], track_id=track,
                           frame_width=frame[0], frame_height=frame[1])


def test_samples_project_to_ground_points_but_stay_unpersisted():
    rows = [_row(t) for t in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]
    result = geo.build_segments(_camera(), rows)
    assert result['persisted'] is False
    assert result['layer'] == 'experimental_estimate'
    assert result['camera_ready'] is True
    assert len(result['segments']) == 1
    segment = result['segments'][0]
    assert segment['break_reason'] == 'end_of_track'
    assert len(segment['points']) == 6
    for point in segment['points']:
        assert point['spatial_level'] == 'L2-candidate'
        assert point['ground_point']['lat'] > 25.2500
        assert point['ground_point']['crs'] == 'EPSG:4326'
        # 像素框必须保留：没有框就无法回看关键帧，也无法复核。
        assert point['box'] == [900.0, 500.0, 1020.0, 700.0]
    assert result['summary']['projected'] == 6
    assert result['summary']['degraded'] == 0


def test_gap_splits_segments_without_extrapolating():
    rows = [_row(0.0), _row(0.2), _row(5.0), _row(5.2)]
    result = geo.build_segments(_camera(), rows)
    assert len(result['segments']) == 2
    assert result['segments'][0]['break_reason'] == 'gap_exceeded'
    assert result['segments'][1]['start_seconds'] == 5.0
    assert result['summary']['points'] == 4


def test_missing_frame_size_degrades_instead_of_guessing():
    rows = [_row(0.0, frame=(0, 0)), _row(0.2, frame=(0, 0))]
    result = geo.build_segments(_camera(), rows)
    assert result['camera_ready'] is False
    assert result['degrade_reason'] == 'frame_size_missing'
    assert len(result['segments']) == 1
    for point in result['segments'][0]['points']:
        assert point['spatial_level'] == 'L0'
        assert point['degrade_reason'] == 'frame_size_missing'
        assert 'ground_point' not in point
    assert result['summary']['degraded'] == 2


def test_unready_camera_keeps_pixel_track_without_coordinates():
    rows = [_row(0.0), _row(0.2)]
    result = geo.build_segments(_camera(ray_projection_ready=False), rows)
    assert result['camera_ready'] is False
    assert result['degrade_reason'] == 'camera_parameters_incomplete'
    assert result['segments'][0]['quality'] == 'camera_only'
    assert result['segments'][0]['points'][0]['degrade_reason'] == 'camera_parameters_incomplete'


def test_short_segments_are_dropped_not_padded():
    result = geo.build_segments(_camera(), [_row(0.0)])
    assert result['segments'] == []
    assert result['summary']['dropped_short_segments'] == 1


def test_camera_params_reports_missing_height():
    params, reason = geo.camera_params(_camera(mount_height_m=None), [1920, 1080])
    assert params is None
    assert reason == 'camera_parameters_incomplete'


def test_camera_params_reports_unresolved_camera():
    params, reason = geo.camera_params(None, [1920, 1080])
    assert params is None
    assert reason == 'camera_not_resolved'


def test_camera_params_requires_positive_frame_size():
    params, reason = geo.camera_params(_camera(), [0, 0])
    assert params is None
    assert reason == 'frame_size_missing'


def test_no_samples_returns_empty_envelope():
    result = geo.build_segments(_camera(), [])
    assert result['segments'] == []
    assert result['degrade_reason'] == 'no_samples'
    assert result['persisted'] is False