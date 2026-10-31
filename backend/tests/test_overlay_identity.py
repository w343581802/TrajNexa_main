"""角标识别口径：时间 + 摄像头名称这一份结论要三处一致（扫描 / 命名 / 添加要素）。"""
from pathlib import Path

import numpy as np
import pytest

from app import overlay_identity
from app.config import settings


def fake_ocr(monkeypatch, rows_by_offset):
    """按取帧时刻给不同的角标文本行；帧本身用一张有纹理的图。"""
    frame = np.random.default_rng(3).integers(0, 255, (90, 160, 3), dtype=np.uint8)
    state = {'offset': None}

    def read_frame(path, offset):
        state['offset'] = float(offset)
        return frame if state['offset'] in rows_by_offset else None

    def read_rows(image, recognizer=None):
        return rows_by_offset[state['offset']]

    monkeypatch.setattr(overlay_identity.overlay_ocr, 'read_frame', read_frame)
    monkeypatch.setattr(overlay_identity.overlay_ocr, 'read_overlay_texts', read_rows)
    return state


def test_identify_video_returns_time_and_name(monkeypatch):
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    fake_ocr(monkeypatch, {
        0.5: [{'text': '2026-10-23 14:59:57', 'region': 'top', 'confidence': 0.9},
              {'text': 'YD-中仁路都市丽景正门1', 'region': 'bottom', 'confidence': 0.9}],
    })
    identity = overlay_identity.identify_video(Path('/x/a.mp4'), duration_seconds=1800.0)

    assert identity.capture_time == '2026-10-23T14:59:57'
    assert identity.time_source == 'ocr'
    assert identity.camera_name == 'YD-中仁路都市丽景正门1'
    assert identity.names == ['YD-中仁路都市丽景正门1']
    assert identity.ocr_status == 'ran'
    assert identity.complete is True
    assert identity.warnings == []


def test_identify_video_falls_back_to_filename_time(monkeypatch):
    """角标时间读不出来时用文件名兜底，但必须标明来源、不能算「已确认」。"""
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    fake_ocr(monkeypatch, {
        0.5: [{'text': 'YD-金水路中仁路口1', 'region': 'bottom', 'confidence': 0.9}],
    })
    identity = overlay_identity.identify_video(
        Path('/x/YD-金水路中仁路口1_20261023_145955.mp4'), duration_seconds=1800.0)

    assert identity.capture_time == '2026-10-23T14:59:55'
    assert identity.time_source == 'filename'
    assert identity.camera_name == 'YD-金水路中仁路口1'
    assert identity.complete is False
    assert any('文件名兜底' in w for w in identity.warnings)


def test_identify_video_reports_no_frame_when_every_frame_is_blank(monkeypatch):
    """ffmpeg 只给灰占位帧时必须报 no_frame，不能假装「已识别」。"""
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    monkeypatch.setattr(overlay_identity.overlay_ocr, 'read_frame', lambda *a, **k: None)
    identity = overlay_identity.identify_video(Path('/x/没有时间戳.mp4'), duration_seconds=1800.0)

    assert identity.ocr_status == 'no_frame'
    assert identity.capture_time is None and identity.camera_name is None
    assert any('取不到可用画面' in w for w in identity.warnings)


def test_identify_video_stops_as_soon_as_time_and_name_are_read(monkeypatch):
    """读到「时间 + 名称」就停：批量扫描几千段录像时，多读一帧就是多一次 ffmpeg seek。"""
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    state = fake_ocr(monkeypatch, {
        0.5: [{'text': '2026-10-23 14:59:57', 'region': 'top', 'confidence': 0.9}],
        2.0: [{'text': 'YD-桂康路与中仁路1', 'region': 'bottom', 'confidence': 0.9}],
        4.0: [{'text': 'YD-桂康与中仁路2', 'region': 'bottom', 'confidence': 0.9}],
    })
    identity = overlay_identity.identify_video(Path('/x/a.mp4'), duration_seconds=1800.0)

    assert identity.capture_time == '2026-10-23T14:59:57'
    assert identity.names == ['YD-桂康路与中仁路1']
    assert identity.camera_name == 'YD-桂康路与中仁路1'
    # 4.0 秒那一帧没被读：2.0 秒已经凑齐时间和名称
    assert state['offset'] == 2.0


def test_identify_image_works_on_decoded_frame(monkeypatch):
    """添加要素传的是截图：直接吃 ndarray，不需要落盘。"""
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    monkeypatch.setattr(overlay_identity.overlay_ocr, 'read_overlay_texts',
                        lambda image, recognizer=None: [
                            {'text': '2026-10-23 14:59:57', 'region': 'top', 'confidence': 0.9},
                            {'text': '3-DX-中仁路98号-球', 'region': 'bottom', 'confidence': 0.9}])
    frame = np.random.default_rng(5).integers(0, 255, (90, 160, 3), dtype=np.uint8)
    identity = overlay_identity.identify_image(frame)

    assert identity.capture_time == '2026-10-23T14:59:57'
    assert identity.camera_name == '3-DX-中仁路98号-球'
    assert identity.ocr_status == 'ran'


def test_identify_image_marks_undecodable_source(monkeypatch):
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    identity = overlay_identity.identify_image(Path('/x/不存在.jpg'))
    assert identity.ocr_status == 'no_frame'


def test_ocr_disabled_is_reported_not_silently_empty(monkeypatch):
    monkeypatch.setattr(settings, 'ocr_enabled', False)
    identity = overlay_identity.identify_video(Path('/x/a_20261023_145955.mp4'), duration_seconds=1800.0)
    assert identity.ocr_status == 'disabled'
    assert identity.time_source == 'filename'
    assert any('OCR 未启用' in w for w in identity.warnings)


def test_identity_from_report_normalizes_existing_report():
    """已有预检报告也要能归一成同一份结论，避免二次 OCR。"""
    report = {
        'time': {'ocr_engine_status': 'ran', 'time_status': 'observed_unverified',
                 'overlay_time_samples': [
                     {'offset_seconds': None, 'local_time': '2026-10-23T14:59:57',
                      'name_candidate': '3-DX-中仁路98号-球', 'name_status': 'structured_osd_candidate'}]},
        'camera': {'overlay_name_raw': '3-DX-中仁路98号-球'},
    }
    identity = overlay_identity.identity_from_report(report)
    assert identity['capture_time'] == '2026-10-23T14:59:57'
    assert identity['camera_name'] == '3-DX-中仁路98号-球'
    assert identity['ocr_status'] == 'ran'
    assert identity['warnings'] == []


def test_identity_from_report_marks_frame_unavailable():
    report = {'time': {'ocr_engine_status': 'ran', 'time_status': 'frame_unavailable',
                       'overlay_time_samples': []},
              'camera': {'overlay_name_raw': None}}
    identity = overlay_identity.identity_from_report(report)
    assert identity['ocr_status'] == 'no_frame'
    assert any('取不到可用画面' in w for w in identity['warnings'])


def test_grab_evidence_frame_tries_later_offsets(monkeypatch):
    """证据截图要多试几个时刻，别只试 0.5/2.0 就放弃。"""
    tried = []
    sentinel = object()

    def read_frame(path, offset):
        tried.append(float(offset))
        return sentinel if float(offset) >= 8.0 else None

    monkeypatch.setattr(overlay_identity.overlay_ocr, 'read_frame', read_frame)
    assert overlay_identity.grab_evidence_frame(Path('/x/a.mp4')) is sentinel
    assert tried == [0.5, 2.0, 4.0, 8.0]


def test_grab_evidence_frame_returns_none_when_all_offsets_fail(monkeypatch):
    monkeypatch.setattr(overlay_identity.overlay_ocr, 'read_frame', lambda *a, **k: None)
    assert overlay_identity.grab_evidence_frame(Path('/x/a.mp4')) is None


def fake_ffprobe(monkeypatch, calls, outputs):
    """把 ffprobe 换成假输出；meta=轻量元数据，packets=数包扫盘。"""
    import subprocess

    class FakeProc:
        returncode = 0
        stderr = ''

        def __init__(self, stdout):
            self.stdout = stdout

    def run(args, **kwargs):
        calls['args'].append(list(args))
        key = 'packets' if '-count_packets' in args else 'meta'
        return FakeProc(outputs[key].pop(0))

    monkeypatch.setattr(overlay_identity.subprocess, 'run', run)


def test_probe_duration_prefers_video_stream_over_bloated_container(tmp_path: Path, monkeypatch):
    """容器 format.duration 写得比视频流长几倍时，时长以视频流为准。"""
    meta = '{"format":{"duration":"13792.378200"},"streams":[{"duration":"1862.0","avg_frame_rate":"25/1"}]}'
    probe = '{"format":{"duration":"13792.378200"},"streams":[{"duration":"1862.0","avg_frame_rate":"25/1","nb_read_packets":"46550"}]}'
    calls = {'args': []}
    fake_ffprobe(monkeypatch, calls, {'meta': [meta], 'packets': [probe]})

    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    assert overlay_identity.probe_duration(path) == pytest.approx(1862.0)
    assert any('-count_packets' in args for args in calls['args'])


def test_probe_duration_uses_packet_span_when_stream_metadata_mismatches(tmp_path: Path, monkeypatch):
    """视频流 duration 也偏大时，用包数/帧率算出来的更贴近可解码时长。"""
    meta = '{"format":{"duration":"13891.762200"},"streams":[{"duration":"1961.34","avg_frame_rate":"25/1"}]}'
    probe = '{"format":{"duration":"13891.762200"},"streams":[{"duration":"1961.34","avg_frame_rate":"25/1","nb_read_packets":"46850"}]}'
    calls = {'args': []}
    fake_ffprobe(monkeypatch, calls, {'meta': [meta], 'packets': [probe]})
    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    assert overlay_identity.probe_duration(path) == pytest.approx(46850 / 25)


def test_probe_duration_accepts_clean_container_without_packet_probe(tmp_path: Path, monkeypatch):
    meta = '{"format":{"duration":"1862.100000"},"streams":[{"duration":"1862.0","avg_frame_rate":"25/1"}]}'
    calls = {'args': []}
    fake_ffprobe(monkeypatch, calls, {'meta': [meta], 'packets': []})
    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    assert overlay_identity.probe_duration(path) == pytest.approx(1862.0)
    assert not any('-count_packets' in args for args in calls['args'])


def test_probe_duration_returns_none_on_unreadable_file(tmp_path: Path, monkeypatch):
    import subprocess

    class FakeProc:
        returncode = 1
        stdout = ''
        stderr = 'bad'

    monkeypatch.setattr(overlay_identity.subprocess, 'run',
                        lambda *a, **k: FakeProc())
    assert overlay_identity.probe_duration(tmp_path / 'x.mp4') is None


def test_probe_duration_refuses_equal_27h_metadata_and_uses_packets(tmp_path: Path, monkeypatch):
    """容器和视频流时长一样坏（都是 27h）时，包数算出真实 30 分钟。"""
    meta = '{"format":{"duration":"97276.981689"},"streams":[{"duration":"97276.981689","avg_frame_rate":"25/1"}]}'
    probe = '{"format":{"duration":"97276.981689"},"streams":[{"duration":"97276.981689","avg_frame_rate":"25/1","nb_read_packets":"45800"}]}'
    calls = {'args': []}
    fake_ffprobe(monkeypatch, calls, {'meta': [meta], 'packets': [probe]})
    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    assert overlay_identity.probe_duration(path) == pytest.approx(45800 / 25)
    assert any('-count_packets' in args for args in calls['args'])


def test_probe_duration_returns_none_when_frame_rate_unusable(tmp_path: Path, monkeypatch):
    """帧率 0/0、时长全缺的坏文件：probe 返回 None，交给文件名兜底。"""
    meta = '{"format":{},"streams":[{"avg_frame_rate":"0/0"}]}'
    calls = {'args': []}
    fake_ffprobe(monkeypatch, calls, {'meta': [meta], 'packets': []})
    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    assert overlay_identity.probe_duration(path) is None


def test_filename_duration_parses_to_interval(tmp_path: Path):
    assert overlay_identity.filename_duration(
        '1-LT-桂康路松山巷-向北_20261023145955至20261023153024.mp4') == pytest.approx(1830 - 1)
    assert overlay_identity.filename_duration(
        '3-DX-桂康菜市入口-向北_20261023152821至20261023153028.mp4') == pytest.approx(127)
    # 没有「至」、或格式不对：返回 None
    assert overlay_identity.filename_duration('没有时间的名字.mp4') is None
    assert overlay_identity.filename_duration('x_2026至y.mp4') is None


def test_identify_video_falls_back_to_filename_duration_for_bad_file(monkeypatch, tmp_path: Path):
    """ffprobe 全坏时 duration 用文件名「开始至结束」兜底，OCR 照常跑。"""
    monkeypatch.setattr(settings, 'ocr_enabled', True)
    monkeypatch.setattr(overlay_identity, 'probe_duration', lambda path: None)
    fake_ocr(monkeypatch, {
        0.5: [{'text': '2026-10-23 15:28:21', 'region': 'top', 'confidence': 0.9},
              {'text': 'Y-桂康菜市入口-向北', 'region': 'bottom', 'confidence': 0.9}],
    })
    identity = overlay_identity.identify_video(
        Path('/x/3-DX-桂康菜市入口-向北_20261023152821至20261023153028.mp4'))
    assert identity.duration_seconds == pytest.approx(127)
    assert identity.capture_time == '2026-10-23T15:28:21'


def test_frame_is_broken_flags_decoder_garbage_but_not_real_scenes():
    """花屏帧（大片纯色块 + 丢色度）要判坏；正常街景不能误判。"""
    rng = np.random.default_rng(7)
    # 正常画面：有纹理、有色彩
    healthy = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
    assert not overlay_identity.overlay_ocr.frame_is_broken(healthy)

    # 花屏：整片灰 + 大块纯白三角，几乎没有色度（实测都是 sat < 1）
    garbage = np.full((240, 320, 3), 128, dtype=np.uint8)
    garbage[0:80, 0:160] = 255
    garbage[120:240, 160:320] = 255
    assert overlay_identity.overlay_ocr.frame_is_broken(garbage)

    # 灰阶但真实的画面（夜间/红外）：块状少，不能误判
    gray_scene = rng.integers(0, 255, (240, 320), dtype=np.uint8)
    gray_scene = np.repeat(gray_scene[:, :, None], 3, axis=2)
    assert not overlay_identity.overlay_ocr.frame_is_broken(gray_scene)


def test_grab_evidence_frame_skips_broken_frames(monkeypatch):
    """开头花屏时，证据截图要往后跳到能看的帧，而不是把花屏当结果。"""
    rng = np.random.default_rng(11)
    healthy = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
    garbage = np.full((240, 320, 3), 128, dtype=np.uint8)
    garbage[0:80, 0:160] = 255

    def read_frame(path, offset):
        return garbage if offset in (0.5, 2.0) else healthy

    monkeypatch.setattr(overlay_identity.overlay_ocr, 'read_frame', read_frame)
    frame = overlay_identity.grab_evidence_frame(Path('/x/a.mp4'), offsets=(0.5, 2.0, 4.0))
    assert frame is not None and frame is healthy
