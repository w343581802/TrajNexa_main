"""B8-B：从已登记的真实录像按时间窗取帧（不依赖实验导出的帧目录）。

约束：
- 只读录像文件；取帧偏移是「请求的定位点」，不是已验证的 PTS/OSD；
- 首帧 PTS 与请求值不一致时如实记录，绝不假装对齐；
- 取不到帧就返回失败原因，不产出空窗口冒充分析结果。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import cv2


class RecordingFrameError(ValueError):
    """稳定错误码：调用方按 code 决定 4xx/5xx，不解析中文消息。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def seconds_of_day(value: str | None) -> float | None:
    """把录像扫描记录的开始时间转成「当日秒」。

    OSD 只有日期+时分秒，不含时区；这里只做同日比较，不宣称跨日绝对时间。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6
    except ValueError:
        pass
    clock = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', text)
    if clock:
        hh, mm, ss = (int(v) for v in clock.groups())
        if 0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60:
            return float(hh * 3600 + mm * 60 + ss)
    digits = re.findall(r'\d+', text)
    if len(digits) >= 6:
        hh, mm, ss = int(digits[-3]), int(digits[-2]), int(digits[-1])
        if 0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60:
            return hh * 3600 + mm * 60 + ss
    return None


def extract_window_frames(video_path, start_seconds, end_seconds, fps, out_dir,
                          max_frames=600):
    """按请求时间窗抽帧到 out_dir；返回帧数、实际首帧 PTS 与失败原因。"""
    start = float(start_seconds)
    end = float(end_seconds)
    if not (0 <= start < end):
        raise RecordingFrameError('invalid_window')
    if fps <= 0:
        raise RecordingFrameError('invalid_fps')
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RecordingFrameError('recording_unreadable')
    try:
        native_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if native_fps <= 0 or native_fps > 240:
            native_fps = float(fps)
        # 按帧号定位，比 MSEC 在部分容器/长录像上可靠；定位失败时下面的
        # 首帧校验会显式报错，绝不从 0 开始解码整段录像冒充窗口。
        start_frame = max(0, int(round(start * native_fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        interval = 1.0 / float(fps)
        next_sample = start
        first_pts = None
        saved = 0
        attempted = 0
        read_failed = 0
        reads = 0
        frame_width = 0
        frame_height = 0
        max_reads = int((end - start + 5.0) * native_fps) + 30
        while saved < max_frames:
            ok, frame = cap.read()
            if not ok or frame is None:
                read_failed += 1
                break
            reads += 1
            if reads > max_reads:
                raise RecordingFrameError('window_decode_budget_exceeded')
            position = float(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0.0)
            pts = max(0.0, (position - 1.0) / native_fps)
            if first_pts is None:
                first_pts = pts
                if start_frame > 0 and pts + 2.0 < start:
                    raise RecordingFrameError('seek_not_effective')
            if pts + 1e-6 < next_sample:
                continue
            if pts > end + 0.05:
                break
            if frame.ndim == 3 and frame.shape[2] == 3:
                cv2.imwrite(str(root / ('%04d.jpg' % (saved + 1))), frame,
                            [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if saved == 0:
                    # 抽帧按原生分辨率保存；画幅是路线 A 射线投影的必要输入，随结果返回。
                    frame_height, frame_width = int(frame.shape[0]), int(frame.shape[1])
                saved += 1
            attempted += 1
            next_sample += interval
            while next_sample <= pts:
                next_sample += interval
    finally:
        cap.release()
    if saved == 0:
        raise RecordingFrameError('no_frames_in_window')
    return {
        'status': 'ok',
        'frames': saved,
        'frame_width': frame_width,
        'frame_height': frame_height,
        'requested_start_seconds': start,
        'actual_first_pts_seconds': round(first_pts, 3) if first_pts is not None else None,
        'seek_drift_seconds': (round(first_pts - start, 3)
                               if first_pts is not None else None),
        'read_failed': read_failed,
        'cost': {'frames_requested': max_frames, 'frames_saved': saved,
                 'frames_attempted': attempted, 'read_failed': read_failed},
    }


def write_window_manifest(out_dir, manifest: dict):
    Path(out_dir, 'window.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                            encoding='utf-8')
