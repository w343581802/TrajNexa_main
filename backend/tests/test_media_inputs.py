"""本地媒体输入口径：宿主机路径翻译 + 文件/目录展开（录像资源 / 命名 / 添加要素共用）。"""
from pathlib import Path

import pytest

from app import media_inputs
from app.config import settings


@pytest.fixture()
def host_map(monkeypatch, tmp_path: Path):
    """把宿主机目录映射到临时目录，避免测试依赖真实挂载点。"""
    monkeypatch.setattr(settings, 'media_input_path_map', r'G:\测试数据\视频=/data/video-library')
    monkeypatch.setattr(settings, 'media_input_roots', '')
    monkeypatch.setattr(settings, 'video_naming_roots', '')
    monkeypatch.setattr(settings, 'media_input_root', tmp_path)
    return tmp_path


def test_translate_host_path_maps_directory_and_children(host_map):
    assert media_inputs.translate_host_path(r'G:\测试数据\视频') == '/data/video-library'
    assert media_inputs.translate_host_path(r'G:\测试数据\视频\正常') == '/data/video-library/正常'
    # 前端从浏览器/输入框复制过来的正斜杠写法也要认
    assert media_inputs.translate_host_path('G:/测试数据/视频/正常/子目录') == \
        '/data/video-library/正常/子目录'
    # 盘符大小写不影响
    assert media_inputs.translate_host_path(r'g:\测试数据\视频\正常') == '/data/video-library/正常'


def test_translate_host_path_can_retarget_to_callers_mount(host_map):
    """录像资源只读挂载在 /data/videos，同一个宿主机目录要能映射到它自己的挂载点。"""
    assert media_inputs.translate_host_path(
        r'G:\测试数据\视频\正常', container_root='/data/videos') == '/data/videos/正常'


def test_translate_host_path_leaves_unmapped_path_untouched(host_map):
    assert media_inputs.translate_host_path('/data/videos/正常') == '/data/videos/正常'
    assert media_inputs.translate_host_path(r'D:\别的盘\视频') == r'D:\别的盘\视频'
    # 只是前缀相同但不是同一层目录，不能误吞
    assert media_inputs.translate_host_path(r'G:\测试数据\视频备份') == r'G:\测试数据\视频备份'


def test_resolve_input_handles_quotes_relative_and_missing(host_map):
    target = host_map / '正常'
    target.mkdir()
    assert media_inputs.resolve_input('"正常"') == target.resolve()
    assert media_inputs.resolve_input(str(target)) == target.resolve()
    with pytest.raises(media_inputs.InputPathError) as empty:
        media_inputs.resolve_input('   ')
    assert empty.value.code == 'empty_path'
    with pytest.raises(media_inputs.InputPathError) as missing:
        media_inputs.resolve_input('不存在的目录')
    assert missing.value.code == 'source_not_found'


def test_resolve_input_rejects_paths_outside_allowed_roots(host_map, tmp_path: Path):
    inside = host_map / 'ok'
    inside.mkdir()
    outside = tmp_path.parent / 'outside-of-root'
    outside.mkdir(exist_ok=True)
    assert media_inputs.resolve_input(str(inside), roots=[host_map]) == inside.resolve()
    with pytest.raises(media_inputs.InputPathError) as blocked:
        media_inputs.resolve_input(str(outside), roots=[host_map])
    assert blocked.value.code == 'path_not_allowed'


def test_scan_videos_skips_hidden_and_sorts(host_map):
    root = host_map / '库'
    (root / '.隐藏').mkdir(parents=True)
    (root / 'b.mp4').write_bytes(b'x')
    (root / 'a.MP4').write_bytes(b'x')
    (root / 'c.txt').write_bytes(b'x')
    (root / '.隐藏' / 'd.mp4').write_bytes(b'x')
    (root / '子').mkdir()
    (root / '子' / 'e.mkv').write_bytes(b'x')

    recursive = [p.name for p in media_inputs.scan_videos(root)]
    assert recursive == ['a.MP4', 'b.mp4', 'e.mkv']
    shallow = [p.name for p in media_inputs.scan_videos(root, recursive=False)]
    assert shallow == ['a.MP4', 'b.mp4']


def test_collect_inputs_accepts_directory_and_single_file(host_map):
    root = host_map / '库'
    root.mkdir()
    (root / 'a.mp4').write_bytes(b'x')
    (root / '子').mkdir()
    (root / '子' / 'b.mp4').write_bytes(b'x')

    collected = media_inputs.collect_inputs([str(root)], default_root=host_map)
    assert [p.name for p in collected.files] == ['a.mp4', 'b.mp4']
    assert collected.roots == [root.resolve()]

    single = media_inputs.collect_inputs([str(root / 'a.mp4')], default_root=host_map)
    assert [p.name for p in single.files] == ['a.mp4']
    assert single.roots == [root.resolve()]


def test_collect_inputs_dedupes_and_reports_errors(host_map):
    root = host_map / '库'
    root.mkdir()
    (root / 'a.mp4').write_bytes(b'x')
    dup = media_inputs.collect_inputs([str(root), str(root / 'a.mp4')], default_root=host_map)
    assert [p.name for p in dup.files] == ['a.mp4']

    empty = host_map / '空'
    empty.mkdir()
    with pytest.raises(media_inputs.InputPathError) as no_video:
        media_inputs.collect_inputs([str(empty)], default_root=host_map)
    assert no_video.value.code == 'no_videos'

    (root / 'b.mp4').write_bytes(b'x')
    with pytest.raises(media_inputs.InputPathError) as too_many:
        media_inputs.collect_inputs([str(root)], default_root=host_map, limit=1)
    assert too_many.value.code == 'too_many_files'

    other = host_map / 'x.txt'
    other.write_bytes(b'x')
    with pytest.raises(media_inputs.InputPathError) as unsupported:
        media_inputs.collect_inputs([str(other)], default_root=host_map)
    assert unsupported.value.code == 'unsupported_video'
