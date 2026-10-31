"""M1b overlay text inspection (time and camera-label hints, never camera identity).

PaddleOCR is an OPTIONAL CPU dependency. The ingestion path works without it.
No captured time or device identity is asserted from overlay characters alone.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import unicodedata

import cv2
import numpy as np

from app.config import settings
from app.camera_name_inspection import inspect_frame, apply_quality_to_camera

# Require a full calendar date and a clock: accepting HH:MM alone would invent date.
_DATE_TIME = re.compile(
    r'(?<!\d)((?:19|20)\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})\s*(?:日|T|/|:)?\s*'
    r'(\d{1,2})[:：](\d{2})[:：](\d{2})(?!\d)'
)
_NAME_PREFIX = re.compile(r'(?i)(?:摄像头|监控点|监控|通道|点位|camera|channel|cam)\s*[:：#_\-]?\s*(.+)')
# Conservative bottom-OSD code hint, not verified camera identity.
_DEVICE_CODE = re.compile(
    r'(?i)^(?:\d{1,4}\s*[-—－]\s*)?[A-Z]{1,6}\s*[-—－]\s*'
    r'[\u4e00-\u9fff\w ]{2,60}\s*[-—－]\s*'
    r'[\u4e00-\u9fffA-Z0-9]{1,20}$'
)
# 角标名称牌并不总带结尾段：YD-中仁路都市丽景正门1、YD-桂康路一巷中仁路居民区19栋东。
# 仍要求「设备前缀 + 中文地名」，且以数字 / 朝向 / 机型 / 门口收尾，
# 这样 3-DX-电仁路孔明园E40东庭街北工 这种整段糊掉的识别结果不会被当成名称。
_DEVICE_PLATE = re.compile(
    r'(?i)^(?:\d{1,4}\s*[-—－]\s*)?[A-Z]{1,6}\s*[-—－]\s*'
    r'[\u4e00-\u9fff][\u4e00-\u9fff\w \-—－]{1,58}'
    r'(?:\d|[东南西北]|球|枪|机|门|口)$'
)
_TIMESTAMP_TOLERANCE_SECONDS = 3.0

# 显式钉住识别模型版本：PaddleOCR 3.x 的默认值随版本变化（3.2/3.3 默认 PP-OCRv5，
# 3.7 起默认 PP-OCRv6）。不写死就会在重建镜像时悄悄换模型，报告里也说不清跑的哪一版。
OCR_VERSION = 'PP-OCRv6'


@lru_cache(maxsize=1)
def _get_ocr_engine():
    # v3+ API. Import only in the worker process, not in the FastAPI HTTP handler.
    from paddleocr import PaddleOCR
    return PaddleOCR(use_doc_orientation_classify=False,
                     use_doc_unwarping=False,
                     use_textline_orientation=False,
                     ocr_version=OCR_VERSION,
                     engine='paddle')


@lru_cache(maxsize=1)
def ocr_engine_info() -> dict:
    """写进报告的引擎版本信息，用来核对“这次到底跑的是哪一版 PP-OCR”。"""
    info = {'library': 'paddleocr', 'ocr_version': OCR_VERSION, 'library_version': None}
    try:
        import paddleocr
        info['library_version'] = getattr(paddleocr, '__version__', None)
    except ImportError:
        pass
    return info


def _recognize(image: np.ndarray) -> list[dict]:
    """Convert PaddleOCR 3.x predict() result to small JSON-safe text records."""
    engine = _get_ocr_engine()
    rows = []
    for page in engine.predict(image):
        data = getattr(page, 'json', None)
        if callable(data):
            data = data()
        if not isinstance(data, dict):
            continue
        body = data.get('res', data)
        if not isinstance(body, dict):
            continue
        texts = body.get('rec_texts') or []
        scores = body.get('rec_scores') or []
        for value, score in zip(texts, scores):
            s = str(value).strip()
            if s:
                rows.append({'text': s[:160], 'confidence': round(float(score), 4)})
    return rows[:40]


def _read_overlay_strips(image: np.ndarray, recognizer=None) -> list[dict]:
    """Top/bottom strips reduce scene-text noise; mid-screen overlays need later ROI config."""
    recognizer = recognizer or _recognize
    h, w = image.shape[:2]
    if not h or not w:
        return []
    if w > 1920:
        image = cv2.resize(image, (1920, max(1, round(h * 1920 / w))))
        h, w = image.shape[:2]
    band = max(1, int(h * 0.22))
    rows = []
    for label, strip in [('top', image[:band]), ('bottom', image[-band:])]:
        for item in recognizer(strip):
            text = str(item.get('text', '')).strip()[:160]
            try:
                score = float(item.get('confidence', 0))
            except (ValueError, TypeError):
                score = 0.0
            if text and score >= 0.3:
                rows.append({'text': text, 'confidence': round(min(max(score, 0), 1), 4), 'region': label})
    return rows[:60]


def _normalize_time_text(raw: str) -> str:
    """Normalize OCR punctuation only, never guess missing digits."""
    value = unicodedata.normalize('NFKC', raw)
    value = value.replace('–', '-').replace('—', '-').replace('−', '-').replace('∶', ':').replace('，', ' ').replace(',', ' ')
    return re.sub(r'\s*([:/\-.年月日])\s*', r'\1', value)


def _parse_time(rows: list[dict]) -> tuple[str | None, str | None]:
    """Only parse complete valid calendar timestamps; timezone remains unknown."""
    for label in ('top_right', 'top', 'bottom'):
        parts = [x['text'] for x in rows if x['region'] == label]
        for raw in parts + [' '.join(parts)]:
            match = _DATE_TIME.search(_normalize_time_text(raw))
            if not match:
                continue
            try:
                dt = datetime(*map(int, match.groups()))
                return dt.strftime('%Y-%m-%dT%H:%M:%S'), match.group(0)
            except ValueError:
                continue
    return None, None


def _time_fallback_rows(image: np.ndarray, recognizer) -> list[dict]:
    """On time parse failure, retry OCR only for the upper-right OSD area."""
    h, w = image.shape[:2]
    if h < 40 or w < 80:
        return []
    roi = image[:max(24, round(h * 0.13)), round(w * 0.50):]
    if roi.size == 0:
        return []
    if roi.shape[1] > 1000:
        scale = 1000.0 / roi.shape[1]
        roi = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    enlarged = cv2.resize(roi, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, high = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)
    modes = [('upscaled', enlarged),
             ('clahe', cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)),
             ('white_text', cv2.cvtColor(high, cv2.COLOR_GRAY2BGR))]
    rows = []
    for mode, frame in modes:
        padded = cv2.copyMakeBorder(frame, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        for item in recognizer(padded):
            raw = str(item.get('text', '')).strip()[:160]
            try:
                confidence = float(item.get('confidence', 0.0))
            except (ValueError, TypeError):
                continue
            if raw and confidence >= 0.3:
                rows.append({'text': raw, 'confidence': round(min(confidence, 1), 4),
                             'region': 'top_right', 'preprocess': mode})
        if _parse_time(rows)[0] is not None:
            break
    return rows[:36]


def _parse_name(rows: list[dict]) -> tuple[str | None, str]:
    for row in rows:
        candidate = _DATE_TIME.sub('', row['text']).strip(' :-_—|')
        m = _NAME_PREFIX.search(candidate)
        if m and m.group(1).strip():
            return m.group(1).strip()[:96], 'labelled_candidate'
    bottom = [row['text'].strip() for row in rows if row['region'] == 'bottom']
    options = bottom + [bottom[i].rstrip() + bottom[i+1].lstrip()
                        for i in range(len(bottom)-1)]
    for raw in options:
        candidate = re.sub(r'\s*[-—－]\s*', '-', raw).strip()
        if ((_DEVICE_CODE.fullmatch(candidate) or _DEVICE_PLATE.fullmatch(candidate))
                and not _DATE_TIME.search(candidate)):
            return candidate[:96], 'structured_osd_candidate'
    return None, 'no_camera_name_pattern'


def _sample_offsets(duration_raw) -> list[float]:
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        return [0.0, 1.0, 2.0]
    if not (0 < duration < 60 * 60 * 24):
        return [0.0, 1.0, 2.0]
    return sorted(set(round(max(0.0, min(duration - 0.05, t)), 3) for t in
                      (min(0.5, duration * 0.1), duration / 2, max(0.0, duration - 0.5))))


_FRAME_TIMEOUT_SECONDS = 20
# 快速 seek（-ss 放在 -i 前面）只跳关键帧：长 HEVC 录像上 ffmpeg 可能找不到参考帧
# （"Could not find ref with POC"），它不报错，而是输出一张 #808080 的全灰占位图。
# 这种帧 OCR 一个字都读不出来，却会被上层当成「已取到帧」：于是既没有名称/时间，
# 证据截图也是灰的，界面上还显示「已识别」。这里把它显式判成坏帧并退回精确 seek。
FRAME_BLANK_STD = 6.0
# 只看标准差不够：实测有灰占位图 std 恰好 6.0 卡在阈值上（3040x2160 全 #808080）。
# 更稳的特征是「几乎所有像素挤在同一个灰阶窗口里」——实测灰占位图 96% 的像素落在
# 众数 ±10 内，而真实监控画面最高只有 40%。用集中度作主判据、标准差兜底。
FRAME_BLANK_WINDOW = 10
FRAME_BLANK_FLAT = 0.9


def frame_is_blank(frame) -> bool:
    """近纯色帧（典型是 ffmpeg 的灰占位图）对 OCR 没有任何价值。"""
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        return True
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    if float(gray.std()) < FRAME_BLANK_STD:
        return True
    hist = np.bincount(gray.ravel(), minlength=256)
    mode = int(hist.argmax())
    near = hist[max(0, mode - FRAME_BLANK_WINDOW):mode + FRAME_BLANK_WINDOW + 1].sum()
    return near / float(gray.size) >= FRAME_BLANK_FLAT


# 花屏帧（解码缺参考帧）与「灰占位帧」不是一回事：它标准差正常、画面有内容，
# 但整片由大块纯色三角形拼接、且普遍丢色度（彩色通道几乎为 0）。这种帧既不能当
# 证据截图，也会让 OCR 读出乱码名称，所以单独判一类，命中就换下一个时刻。
FRAME_BROKEN_FLAT = 0.45
FRAME_BROKEN_FLAT_HARD = 0.60
FRAME_BROKEN_SAT = 8.0
FRAME_BLOCK_SIZE = 16


def frame_is_broken(frame) -> bool:
    """花屏/解码损坏帧：大片 16×16 纯色块，且多半同时丢了色度。

    判据来自真实录像实测（2560×1440 / 3840×2160 的 HEVC 导出）：
    健康画面「纯色块占比」≤ 0.27、饱和度 35~71；花屏帧块占比 0.45~0.95、饱和度 0~0.7。
    """
    if not isinstance(frame, np.ndarray) or frame.size == 0:
        return True
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    height, width = gray.shape[:2]
    size = FRAME_BLOCK_SIZE
    rows, cols = height // size, width // size
    if rows < 1 or cols < 1:
        return False
    grid = gray[: rows * size, : cols * size].astype(np.float32)
    blocks = grid.reshape(rows, size, cols, size).transpose(0, 2, 1, 3).reshape(-1, size * size)
    flat = float((blocks.std(axis=1) < 2.0).mean())
    if flat >= FRAME_BROKEN_FLAT_HARD:
        return True
    if flat < FRAME_BROKEN_FLAT or frame.ndim != 3:
        return False
    saturation = float(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
    return saturation < FRAME_BROKEN_SAT

def _ffmpeg_frame(path: Path, offset: float, accurate: bool = False) -> np.ndarray | None:
    moment = f'{max(0.0, float(offset)):.3f}'
    if accurate:
        # -ss 放在 -i 之后是精确 seek：从头解码到目标时刻，参考帧齐全（慢但可靠）
        cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-ss', moment]
    else:
        # -ss 放在 -i 之前是快速 seek：只跳关键帧，长录像上快得多
        cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-ss', moment, '-i', str(path)]
    cmd += ['-map', '0:v:0', '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-']
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              timeout=_FRAME_TIMEOUT_SECONDS, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout or len(proc.stdout) > 24 * 1024 * 1024:
        return None
    return cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR)


def _video_frame(path: Path, offset: float) -> np.ndarray | None:
    """读指定时刻的单帧：快速 seek 拿到灰占位帧/花屏帧就退回精确 seek，仍不对就算没取到。"""
    frame = _ffmpeg_frame(path, offset)
    if frame is not None and not frame_is_blank(frame) and not frame_is_broken(frame):
        return frame
    frame = _ffmpeg_frame(path, offset, accurate=True)
    if frame is not None and not frame_is_blank(frame) and not frame_is_broken(frame):
        return frame
    return None


def analyze_overlay(media, path: Path, result: dict, recognizer=None, frame_reader=None, name_roi_recognizer=None) -> None:
    time = result['time']
    camera = result['camera']
    time.update(overlay_time_samples=[], time_status='not_checked', timezone=None,
                metadata_time_is_capture_time=False)
    camera.update(overlay_name_raw=None, camera_candidates=[], camera_uuid=None,
                  match_status='not_checked')
    if not settings.ocr_enabled and recognizer is None:
        time['ocr_engine_status'] = 'disabled'
        camera['ocr_engine_status'] = 'disabled'
        return
    time['ocr_engine_status'] = camera['ocr_engine_status'] = 'pending'
    if result['quality']['decode_status'] in ('failed', 'not_checked'):
        time['time_status'] = 'media_unusable'
        camera['match_status'] = 'media_unusable'
        return
    recognizer = recognizer or _recognize
    sample_frames = []
    if media.kind == 'image':
        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            sample_frames = [(None, image)]
    elif media.kind == 'video':
        duration = result['media']['technical_metadata'].get('duration_seconds')
        reader = frame_reader or _video_frame
        sample_frames = [(offset, reader(path, offset)) for offset in _sample_offsets(duration)]
    successful = []
    name_roi_result = None
    do_name_roi = settings.name_roi_enabled and (recognizer is None or name_roi_recognizer is not None)
    for offset, frame in sample_frames:
        if frame is None:
            continue
        try:
            rows = _read_overlay_strips(frame, recognizer)
        except (ImportError, ModuleNotFoundError):
            time['time_status'] = 'ocr_engine_unavailable'
            camera['match_status'] = 'ocr_engine_unavailable'
            time['ocr_engine_status'] = camera['ocr_engine_status'] = 'unavailable'
            return
        except Exception:
            time['time_status'] = 'ocr_engine_error'
            camera['match_status'] = 'ocr_engine_error'
            time['ocr_engine_status'] = camera['ocr_engine_status'] = 'error'
            return
        parsed_time, time_raw = _parse_time(rows)
        name, name_status = _parse_name(rows)
        if do_name_roi and name_roi_result is None:
            try:
                name_roi_result = inspect_frame(frame, name_roi_recognizer or recognizer or _recognize)
            except Exception:
                name_roi_result = {'status': 'name_roi_error_requires_review',
                    'candidates': [], 'warnings': ['roi_ocr_error'], 'raw_ocr_rows': [],
                    'camera_uuid': None, 'no_auto_binding': True}
        fallback_attempted = False
        if parsed_time is None and (media.kind == 'image' or not successful):
            fallback_attempted = True
            try:
                extra = _time_fallback_rows(frame, recognizer)
                rows = (rows + extra)[:96]
                parsed_time, time_raw = _parse_time(rows)
            except Exception:
                pass
        successful.append({'offset_seconds': offset, 'time_text': time_raw,
                           'local_time': parsed_time, 'name_candidate': name,
                           'name_status': name_status, 'ocr_rows': rows,
                           'time_roi_fallback_attempted': fallback_attempted})
    time['overlay_time_samples'] = successful
    time['ocr_time_diagnostics'] = {
        'source': 'ocr_text_unverified',
        'parsed_sample_count': sum(bool(s['local_time']) for s in successful),
        'top_raw_texts': [
            {'text': r['text'], 'confidence': r['confidence'],
             'region': r['region'], 'preprocess': r.get('preprocess', 'original')}
            for s in successful for r in s['ocr_rows']
            if r.get('region') in ('top', 'top_right')
        ][:30],
        'roi_fallback_attempted': any(s['time_roi_fallback_attempted'] for s in successful),
    }
    time['ocr_engine_status'] = camera['ocr_engine_status'] = 'ran'
    time['ocr_engine'] = ocr_engine_info()
    if not successful:
        time['time_status'] = 'frame_unavailable'
        camera['match_status'] = 'frame_unavailable'
        return
    times = [(s['offset_seconds'], s['local_time']) for s in successful if s['local_time']]
    if not times:
        time['time_status'] = 'unreadable'
    elif len(times) == 1:
        time['time_status'] = 'observed_unverified' if media.kind == 'image' else 'insufficient_samples'
    else:
        # OCR clock delta should follow media time offsets; off-by-one second allowed.
        first_offset, first_time = times[0]
        base = datetime.fromisoformat(first_time)
        consistent = all(abs((datetime.fromisoformat(t) - base).total_seconds() -
                             (float(o) - float(first_offset))) <= _TIMESTAMP_TOLERANCE_SECONDS
                         for o, t in times[1:])
        time['time_status'] = 'consistent_unverified' if consistent else 'inconsistent'
    names = [s['name_candidate'] for s in successful if s['name_candidate']]
    methods = sorted({s['name_status'] for s in successful if s['name_candidate']})
    camera['overlay_candidate_methods'] = methods
    if not names:
        camera['match_status'] = 'no_labelled_name'
    else:
        camera['camera_candidates'] = sorted(set(names))[:10]
        if len(set(names)) == 1:
            camera['overlay_name_raw'] = names[0]
            camera['match_status'] = ('structured_osd_candidate_unbound'
                if methods == ['structured_osd_candidate'] else 'labelled_candidate_unbound')
        else:
            camera['match_status'] = 'conflicting_names'

    if name_roi_result is not None:
        apply_quality_to_camera(camera, name_roi_result)



# ---------------------------------------------------------------------------
# 录像资源扫描复用接口（只读画面，不写任何文件）
# ---------------------------------------------------------------------------

def read_frame(path: Path, offset: float):
    """读取视频指定时刻的单帧；失败返回 None。"""
    return _video_frame(path, offset)


def read_overlay_texts(frame, recognizer=None) -> list[dict]:
    """读取画面上下条带的角标文本行（时间条/设备名条）。"""
    return _read_overlay_strips(frame, recognizer or _recognize)


def parse_overlay_rows(rows: list[dict]) -> dict:
    """把角标文本行解析成「时间候选 + 名称候选」，只解析、不判定身份。"""
    local_time, time_text = _parse_time(rows)
    name, name_status = _parse_name(rows)
    return {'local_time': local_time, 'time_text': time_text,
            'name': name, 'name_status': name_status}
