"""录像 / 截图角标识别：统一产出「拍摄时间 + 摄像头名称」这一份结论。

三处共用同一套实现，不再各自写一遍「抽帧 + OCR + 解析」：

* 录像资源扫描（recordings.inspect_first_seconds）→ 扫描结果表；
* 视频命名工具（video_naming.process_media）→ 改名依据；
* 添加要素 / 其它预检（inspector.inspect_media、identity_from_report）→ 截图识别候选。

口径固定，与 M1b / M1c 一致，绝不猜身份：

* 只读**开头几秒**的画面角标：0.5s / 2.0s / 4.0s，读到「时间 + 名称」就提前结束；
* 时间优先角标；角标读不出才退回文件名里的 14 位时间戳，并标 time_source='filename'；
* 摄像头名称只是**候选**：不写 camera_uuid、不宣称身份，台账匹配交给 recordings.match_camera；
* ffmpeg 的灰占位帧由 overlay_ocr.read_frame 过滤成 None，一帧都拿不到就是 no_frame；
* 任何一步失败都显式降级并写进 warnings，绝不把失败报成「识别成功」。
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app import overlay_ocr
from app.config import settings

# 识别用的取帧时刻：读到「时间 + 名称」就提前结束
IDENTITY_OFFSETS = (0.5, 2.0, 4.0)
# 证据截图 / 界面预览用的取帧时刻：开头可能是黑场或切换画面，多试几个点
EVIDENCE_OFFSETS = (0.5, 2.0, 4.0, 8.0, 15.0)
# 人工核对弹窗的截图：DVR 导出的录像开头常有几秒到十几秒花屏，
# 只按 EVIDENCE_OFFSETS 取会全是坏图，所以要往后多试到 1 分钟。
PREVIEW_OFFSETS = (0.5, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0)

# 文件名里的开始时间：兼容 20261023145957 与 20261023_145957 两种写法。
# 长录像导出常用后者（摄像头名称_YYYYMMDD_HHMMSS.mp4），只认连续 14 位会漏斗时间。
_FILENAME_START = re.compile(
    r'(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})[ _-]?(\d{2})(\d{2})(\d{2})(?!\d)')


@dataclass
class OverlayIdentity:
    """一次识别的全部结论：读到什么、从哪儿读到的、还缺什么。"""

    capture_time: str | None = None
    time_source: str = ''
    camera_name: str | None = None
    name_status: str = ''
    name_source: str = 'none'
    names: list[str] = field(default_factory=list)
    ocr_status: str = 'not_checked'
    duration_seconds: float | None = None
    frame_offset: float | None = None
    rows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_time(self) -> bool:
        return bool(self.capture_time)

    @property
    def has_name(self) -> bool:
        return bool(self.camera_name)

    @property
    def complete(self) -> bool:
        """角标上时间和名称都拿到了（文件名兜底的时间不算）。"""
        return bool(self.capture_time and self.camera_name and self.time_source == 'ocr')

    def as_dict(self) -> dict:
        return {
            'capture_time': self.capture_time,
            'time_source': self.time_source,
            'camera_name': self.camera_name,
            'name_status': self.name_status,
            'name_source': self.name_source,
            'names': list(self.names),
            'ocr_status': self.ocr_status,
            'duration_seconds': self.duration_seconds,
            'frame_offset': self.frame_offset,
            'warnings': list(self.warnings),
        }


def probe_duration(path: Path) -> float | None:
    """取可解码的录像时长（秒），失败返回 None；不做任何转码。

    部分 DVR/导出工具会把容器时长（format.duration 连视频流的 duration 一起）写成
    整个录制会话的时间跨度（例如 30 分钟视频被写成 27 小时，格式还完全一致）。
    只靠「容器 vs 视频流相差 1.5 倍」判不坏这种数据，所以坏判定扩成三条：
    时长 >= 24 小时、任一时长缺失、两值相差 1.5 倍以上，都做一次 -count_packets
    全包扫描，用「包数 / 帧率」算真实可解码时长；正常文件仍只跑一次轻量元数据。
    """
    meta = _ffprobe(path, count_packets=False)
    if meta is None:
        return None
    stream_duration = meta['stream_duration']
    format_duration = meta['format_duration']
    fps = meta['fps']
    base = stream_duration or format_duration

    DAY = 24 * 60 * 60
    suspicious = (base is None or base >= DAY
                  or (stream_duration and format_duration
                      and max(stream_duration, format_duration)
                      / min(stream_duration, format_duration) > 1.5))
    if suspicious and fps:
        span = _packet_span(path, fps)
        if span is not None and 0 < span < DAY:
            return span

    if base is not None and 0 < base < DAY:
        # 两条都可信时取较小的那个：format 偶尔比视频流多写一点尾巴
        if stream_duration and format_duration:
            return min(stream_duration, format_duration)
        return base
    return None


def filename_duration(name: str) -> float | None:
    """文件名「开始至结束」时间戳算时长（秒）：只认「至」字前后各一个 14 位时间戳。

    只当 ffprobe 完全读不出时长时才兜底（例如帧率/时长/包数全坏的导出文件），
    结果来自导出方自己的命名，取到也由上层标成 estimate。
    """
    text = name or ''
    if '至' not in text:
        return None
    before, after = text.split('至', 1)
    start_match = _FILENAME_START.search(before)
    end_match = _FILENAME_START.search(after)
    if not start_match or not end_match:
        return None
    try:
        start_dt = datetime(*(int(part) for part in start_match.groups()))
        end_dt = datetime(*(int(part) for part in end_match.groups()))
    except ValueError:
        return None
    delta = (end_dt - start_dt).total_seconds()
    return delta if 0 < delta < 24 * 60 * 60 else None


def _ffprobe(path: Path, *, count_packets: bool) -> dict | None:
    """跑一次 ffprobe 拿时长要素；count_packets=True 时额外数视频包。"""
    args = ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=duration,avg_frame_rate,nb_read_packets',
            '-show_entries', 'format=duration', '-of', 'json', str(path)]
    if count_packets:
        args.insert(args.index('-v'), '-count_packets')
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return None
    stream = (doc.get('streams') or [{}])[0]
    return {
        'stream_duration': _number(stream.get('duration')),
        'format_duration': _number((doc.get('format') or {}).get('duration')),
        'fps': _parse_rate(stream.get('avg_frame_rate')),
        'packets': _number(stream.get('nb_read_packets')),
    }


def _packet_span(path: Path, fps: float) -> float | None:
    """用「视频包数 / 帧率」算真实时长，只在这种文件上多扫一次包。"""
    if fps <= 0:
        return None
    meta = _ffprobe(path, count_packets=True)
    if meta is None or meta['packets'] is None:
        return None
    return meta['packets'] / fps


def _parse_rate(value: str | None) -> float | None:
    if not value:
        return None
    try:
        left, right = str(value).split('/')
        top, bottom = float(left), float(right)
    except (TypeError, ValueError):
        return None
    return top / bottom if top > 0 and bottom > 0 else None


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def filename_start(name: str) -> str | None:
    """文件名里 14 位时间戳作为开始时间的兜底来源（取第一段，忽略「至结束时间」）。"""
    for match in _FILENAME_START.finditer(name or ''):
        try:
            return datetime(*(int(part) for part in match.groups())).strftime('%Y-%m-%dT%H:%M:%S')
        except ValueError:
            continue
    return None


def _apply_rows(identity: OverlayIdentity, rows: list[dict], offset: float | None) -> None:
    """把一帧的角标文本行并进结论：时间取第一处读到的，名称收集全部候选。"""
    parsed = overlay_ocr.parse_overlay_rows(rows)
    if parsed['local_time'] and not identity.capture_time:
        identity.capture_time = parsed['local_time']
        identity.time_source = 'ocr'
        identity.frame_offset = offset
        identity.rows = rows
    name = parsed['name']
    if name and name not in identity.names:
        identity.names.append(name)
        if not identity.camera_name:
            identity.camera_name = name
            identity.name_status = parsed['name_status']
            identity.name_source = 'ocr'


def _apply_filename_fallback(identity: OverlayIdentity, name: str | None) -> None:
    if identity.capture_time or not name:
        return
    value = filename_start(name)
    if value:
        identity.capture_time = value
        identity.time_source = 'filename'

def filename_camera_label(name: str) -> str | None:
    """文件名里位于第一个 14 位时间戳之前的「摄像头标签」段，用作 OCR 读不到名称时的兜底候选。

    例：3-DX-中仁路98号-球_20261023_145955.mp4 -> 3-DX-中仁路98号-球
        东门球机A_20261025180000.mp4 -> 东门球机A
        3-DX-中仁路孔明园E40栋-向北_20261023145957至... -> 3-DX-中仁路孔明园E40栋-向北
    只提取、不改变 OCR 结论：读得到角标时不用它，读不到才作为候选让用户确认。
    """
    raw = Path(name or '').name
    match = _FILENAME_START.search(raw)
    if not match:
        return None
    label = raw[:match.start()].strip(' _-')
    return label if label and len(label) <= 60 else None


def _apply_filename_name_fallback(identity: OverlayIdentity, name: str | None) -> None:
    """OCR/角标读到名称就不动；没读到才用文件名标签兜底并标记来源。

    OCR 引擎未启用（ocr_status == 'disabled'）不算「读不到」，保持名称空，
    由上层标 ocr_disabled，避免把文件名当成识别结论。
    """
    if identity.camera_name or identity.ocr_status == 'disabled' or not name:
        return
    label = filename_camera_label(name)
    if not label:
        return
    identity.camera_name = label
    identity.names.append(label)
    identity.name_status = 'filename_hint'
    identity.name_source = 'filename'


def _finalize(identity: OverlayIdentity, *, media_label: str) -> OverlayIdentity:
    """补齐缺什么、为什么缺，供界面与 CSV 直接显示。"""
    if identity.ocr_status == 'no_frame':
        identity.warnings.append('开头几秒取不到可用画面（ffmpeg 只给出灰占位帧）')
    if identity.ocr_status == 'disabled':
        identity.warnings.append('OCR 未启用，未读取画面角标')
    if identity.ocr_status == 'unavailable':
        identity.warnings.append('OCR 引擎不可用：worker 里缺少 PaddleOCR/PaddlePaddle')
    if identity.ocr_status == 'error':
        identity.warnings.append('OCR 执行出错')
    if not identity.camera_name:
        identity.warnings.append('未从' + media_label + '识别到摄像头名称')
    elif identity.name_source == 'filename':
        identity.warnings.append('摄像头名称来自文件名兜底，未经角标/人工确认')
    if not identity.capture_time:
        identity.warnings.append('未识别到完整拍摄时间')
    elif identity.time_source == 'filename':
        identity.warnings.append('拍摄时间来自文件名兜底，未由角标确认')
    return identity


def _plan_offsets(duration: float | None, offsets) -> list[float]:
    """按录像时长裁掉超出片尾的取帧时刻；全被裁掉就退回 0 秒。"""
    planned = [float(offset) for offset in offsets
               if duration is None or float(offset) <= max(0.0, duration - 0.2)]
    return planned or [0.0]


def identify_video(path: Path, *, offsets=None,
                   duration_seconds: float | None = None) -> OverlayIdentity:
    """读录像开头几秒：拍摄时间（角标优先、文件名兜底）+ 摄像头名称候选。"""
    path = Path(path)
    identity = OverlayIdentity(
        duration_seconds=duration_seconds if duration_seconds is not None
        else probe_duration(path) or filename_duration(path.name))
    if not settings.ocr_enabled:
        identity.ocr_status = 'disabled'
    else:
        identity.ocr_status = 'pending'
        frames_read = 0
        for offset in _plan_offsets(identity.duration_seconds, offsets or IDENTITY_OFFSETS):
            frame = overlay_ocr.read_frame(path, offset)
            if frame is None:
                # 灰占位帧 / 解码失败由 overlay_ocr 统一过滤成 None，继续试下一个时刻
                continue
            frames_read += 1
            try:
                rows = overlay_ocr.read_overlay_texts(frame)
            except (ImportError, ModuleNotFoundError):
                identity.ocr_status = 'unavailable'
                break
            except Exception:
                identity.ocr_status = 'error'
                break
            _apply_rows(identity, rows, offset)
            if identity.capture_time and identity.names:
                break
        if identity.ocr_status == 'pending':
            identity.ocr_status = 'ran' if frames_read else 'no_frame'
    _apply_filename_fallback(identity, path.name)
    _apply_filename_name_fallback(identity, path.name)
    return _finalize(identity, media_label='画面角标')


def _load_image(source) -> 'object | None':
    import cv2
    import numpy as np

    if isinstance(source, (str, Path)):
        try:
            buffer = np.fromfile(str(source), dtype=np.uint8)
        except OSError:
            return None
        return cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return source


def identify_image(source, *, name: str | None = None) -> OverlayIdentity:
    """读一张截图（添加要素用）：源可以是路径，也可以是已解码的 ndarray。"""
    identity = OverlayIdentity()
    frame = _load_image(source)
    if frame is None:
        identity.ocr_status = 'no_frame'
        return _finalize(identity, media_label='截图')
    if not settings.ocr_enabled:
        identity.ocr_status = 'disabled'
    else:
        try:
            rows = overlay_ocr.read_overlay_texts(frame)
        except (ImportError, ModuleNotFoundError):
            identity.ocr_status = 'unavailable'
            rows = []
        except Exception:
            identity.ocr_status = 'error'
            rows = []
        else:
            identity.ocr_status = 'ran'
            _apply_rows(identity, rows, None)
    file_name = name or (source.name if isinstance(source, (str, Path)) else None)
    _apply_filename_fallback(identity, file_name)
    return _finalize(identity, media_label='截图')


def identity_from_report(result: dict) -> dict:
    """把一份已有的预检报告（M1b）归一成同一份结论，避免二次 OCR。"""
    result = result or {}
    time = result.get('time') or {}
    camera = result.get('camera') or {}
    samples = time.get('overlay_time_samples') or []
    capture_time = next((s.get('local_time') for s in samples if s.get('local_time')), None)
    named = next((s for s in samples if s.get('name_candidate')), None)
    identity = OverlayIdentity(
        capture_time=capture_time,
        time_source='ocr' if capture_time else '',
        camera_name=(named or {}).get('name_candidate') or camera.get('overlay_name_raw'),
        name_status=(named or {}).get('name_status') or '',
        names=[s['name_candidate'] for s in samples if s.get('name_candidate')],
    )
    engine = time.get('ocr_engine_status') or 'not_checked'
    if engine in ('disabled', 'unavailable', 'error'):
        identity.ocr_status = engine
    elif not samples or time.get('time_status') == 'frame_unavailable':
        identity.ocr_status = 'no_frame'
    else:
        identity.ocr_status = 'ran'
    return _finalize(identity, media_label='画面角标').as_dict()


def _frame_usable(frame) -> bool:
    """占位帧（测试桩返回的非图像对象）不做图像判定，交给上层用。"""
    if frame is None:
        return False
    if not hasattr(frame, "ndim"):
        return True
    return not overlay_ocr.frame_is_blank(frame) and not overlay_ocr.frame_is_broken(frame)


def grab_evidence_frame(path: Path, *, offsets=None):
    """取一张能看清角标的画面当证据截图；取不到返回 None（不产出假截图）。

    灰占位帧和花屏帧都跳过——它们是解码失败的产物，拿去当证据只会让人误判。
    """
    for offset in offsets or EVIDENCE_OFFSETS:
        frame = overlay_ocr.read_frame(Path(path), offset)
        if _frame_usable(frame):
            return frame
    return None
