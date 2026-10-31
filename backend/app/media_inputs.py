"""本地媒体输入：前端只传路径，后端直接读本地文件 / 目录。

录像资源、视频命名、添加要素三处共用同一套口径，避免每个功能各写一遍路径翻译：

* 宿主机路径按 MEDIA_INPUT_PATH_MAP（留空则沿用 VIDEO_NAMING_PATH_MAP）映射到容器里
  真正挂载的目录，例如 G:\测试数据\视频 → /data/video-library；调用方可以传
  container_root 把同一个宿主机目录映射到它自己那个挂载点（录像资源用只读的
  /data/videos，命名工具用可写的 /data/video-library）；
* 相对路径按 default_root（缺省 MEDIA_INPUT_ROOT / VIDEO_NAMING_ROOT）解析；
* 传**文件**就只处理这一个文件，传**目录**就按 recursive 展开；隐藏项一律跳过；
* MEDIA_INPUT_ROOTS（留空则沿用 VIDEO_NAMING_ROOTS）配了就只能访问这些根目录；
* 顺序稳定、结果去重、带上限，避免前端一次误选整块磁盘。

这个模块只做「解析路径 + 枚举文件」，不打开视频、不写任何文件。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from app.config import settings

VIDEO_SUFFIXES = frozenset({
    '.mp4', '.mkv', '.avi', '.mov', '.m4v', '.webm',
    '.ts', '.mpg', '.mpeg', '.flv', '.wmv', '.dav',
})
IMAGE_SUFFIXES = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.bmp'})
# 路径映射表里多条规则的分隔符：G:\测试数据\视频=/data/video-library;/mnt/x=/data/y
MAP_SEPARATOR = ';'
_DRIVE_LETTER = re.compile(r'^[A-Za-z]:')


class InputPathError(ValueError):
    """路径不可用。code 供各接口映射成自己的错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CollectedInputs:
    """一批输入路径展开后的结果：待处理文件 + 它们的根目录。"""

    files: list[Path]
    roots: list[Path]

    def __len__(self) -> int:
        return len(self.files)


def _clean(text: str | Path) -> str:
    """统一分隔符并去掉尾部斜杠，便于前缀比较。"""
    value = str(text).replace('\\', '/').strip()
    return value.rstrip('/') or value


def _path_map_pairs() -> list[tuple[str, str]]:
    raw = (settings.media_input_path_map or settings.video_naming_path_map or '').strip()
    pairs: list[tuple[str, str]] = []
    for pair in raw.split(MAP_SEPARATOR):
        if '=' not in pair:
            continue
        host, container = pair.split('=', 1)
        host = _clean(host)
        container = str(container).strip()
        if host and container:
            pairs.append((host, container))
    return pairs


def translate_host_path(text: str, *, container_root: str | Path | None = None) -> str:
    """把宿主机路径（G:\测试数据\视频\正常）换成容器内的挂载路径。

    container_root 用来把宿主机目录映射到调用方自己的挂载点：录像资源传只读挂载，
    命名工具不传（用映射表里的可写挂载）。
    """
    normalized = _clean(text)
    for host, container in _path_map_pairs():
        # Windows 盘符路径大小写不敏感，POSIX 路径保持敏感
        fold = bool(_DRIVE_LETTER.match(host))
        left, right = (normalized.lower(), host.lower()) if fold else (normalized, host)
        if left == right:
            return str(container_root if container_root is not None else container)
        if left.startswith(right + '/'):
            rest = normalized[len(host):].lstrip('/')
            base = str(container_root if container_root is not None else container)
            return str(Path(base) / Path(rest)) if rest else base
    return text


def _split_roots(raw: str) -> list[str]:
    """根目录列表：Windows 风格用分号分隔（盘符里的冒号不能当分隔符）。"""
    return [piece.strip() for piece in raw.split(';') if piece.strip()]


def allowed_roots() -> list[Path]:
    """MEDIA_INPUT_ROOTS 配了才限制；本地 demo 留空表示不额外限制。"""
    raw = (settings.media_input_roots or settings.video_naming_roots or '').strip()
    roots: list[Path] = []
    for piece in _split_roots(raw):
        try:
            roots.append(Path(piece).expanduser().resolve())
        except OSError:
            continue
    return roots


def default_root() -> Path:
    return Path(settings.media_input_root or settings.video_naming_root)


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_SUFFIXES


def resolve_input(raw: str, *, default_root: Path | None = None,
                  roots: Sequence[Path] | None = None,
                  container_root: str | Path | None = None) -> Path:
    """把前端传来的路径变成容器内可读的绝对路径（文件或目录都行）。"""
    text = (raw or '').strip().strip('"').strip("'")
    if not text:
        raise InputPathError('empty_path', '路径不能为空')
    text = translate_host_path(text, container_root=container_root)
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = default_root if default_root is not None else globals()['default_root']()
        path = Path(base) / path
    try:
        path = path.resolve()
    except OSError:
        raise InputPathError('source_not_found', '路径无法解析：' + text)
    if not path.exists():
        raise InputPathError('source_not_found', '路径不存在：' + text)
    allowed = list(roots) if roots is not None else allowed_roots()
    if allowed and not any(path.is_relative_to(root) for root in allowed):
        raise InputPathError('path_not_allowed', '路径不在允许目录内：' + text)
    return path


def scan_videos(target: Path, *, recursive: bool = True,
                suffixes: Iterable[str] = VIDEO_SUFFIXES,
                limit: int | None = None) -> list[Path]:
    """枚举目录里的视频文件：跳过隐藏项、顺序稳定、可带上限。"""
    wanted = {str(suffix).lower() for suffix in suffixes}
    iterator = target.rglob('*') if recursive else target.glob('*')
    found: list[Path] = []
    for item in iterator:
        try:
            if not item.is_file() or item.suffix.lower() not in wanted:
                continue
        except OSError:
            continue
        rel_parts = item.relative_to(target).parts
        if any(part.startswith('.') for part in rel_parts):
            continue
        found.append(item)
        if limit is not None and len(found) >= limit:
            break
    return sorted(found, key=lambda item: str(item.relative_to(target)).lower())


def collect_inputs(raw_paths: Sequence[str], *, recursive: bool = True,
                   limit: int | None = None,
                   suffixes: Iterable[str] = VIDEO_SUFFIXES,
                   default_root: Path | None = None,
                   roots: Sequence[Path] | None = None,
                   container_root: str | Path | None = None,
                   empty_message: str = '目录里没有可处理的视频文件') -> CollectedInputs:
    """展开一批「文件或目录」输入：去重、校验扩展名、校验数量上限。"""
    wanted = {str(suffix).lower() for suffix in suffixes}
    files: list[Path] = []
    found_roots: list[Path] = []
    for raw in raw_paths:
        resolved = resolve_input(raw, default_root=default_root, roots=roots,
                                 container_root=container_root)
        if resolved.is_dir():
            found_roots.append(resolved)
            # 多扫一个就知道有没有超上限，避免先扫完整个目录再拒绝
            files.extend(scan_videos(resolved, recursive=recursive, suffixes=wanted,
                                     limit=(limit + 1) if limit is not None else None))
        elif resolved.suffix.lower() in wanted:
            found_roots.append(resolved.parent)
            files.append(resolved)
        else:
            raise InputPathError('unsupported_video', '不是可处理的视频文件：' + resolved.name)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in files:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    if not unique:
        raise InputPathError('no_videos', empty_message)
    if limit is not None and len(unique) > limit:
        raise InputPathError('too_many_files', '一次最多处理 ' + str(limit) + ' 个视频')
    unique_roots: list[Path] = []
    for root in found_roots:
        if root not in unique_roots:
            unique_roots.append(root)
    return CollectedInputs(files=unique, roots=unique_roots)
