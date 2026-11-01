"""yolo_end2end 解码契约测试：Ultralytics YOLO26 的 NMS-free 导出 [1, N, 6]。"""

import numpy as np
import pytest

from app.engines.yolo_postprocess import decode_outputs

LABELS = ["person", "bicycle", "car"]


def _e2e(rows, fill=300):
    arr = np.zeros((1, fill, 6), dtype=np.float32)
    if rows:
        arr[0, :len(rows)] = np.asarray(rows, dtype=np.float32)
    return [arr]


def test_scales_boxes_and_filters_by_conf():
    rows = [[100, 100, 200, 300, 0.9, 0], [10, 10, 20, 20, 0.1, 2]]
    out = decode_outputs(_e2e(rows), "yolo_end2end", "detect", LABELS,
                         (640, 640), 0.25, 0.5, (1280, 720))
    assert len(out) == 1
    assert out[0]["label"] == "person"
    assert out[0]["task"] == "detect"
    box = out[0]["box"]
    # sx = 1280/640 = 2.0, sy = 720/640 = 1.125
    assert abs(box[0] - 200) <= 1 and abs(box[1] - 112) <= 1
    assert abs(box[2] - 400) <= 1 and abs(box[3] - 337) <= 1
    assert 0.85 <= out[0]["score"] <= 0.95


def test_all_zero_rows_return_empty():
    out = decode_outputs(_e2e([]), "yolo_end2end", "detect", LABELS,
                         (640, 640), 0.25, 0.5, (640, 640))
    assert out == []


def test_rejects_raw_head_layout():
    # yolo8/11 原始头是 [1, 4+nc, N]，不能当端到端输出解析
    arr = np.zeros((1, 6, 8400), dtype=np.float32)
    out = decode_outputs([arr], "yolo_end2end", "detect", LABELS,
                         (640, 640), 0.25, 0.5, (640, 640))
    assert out == []


def test_overlapping_boxes_are_not_nms_suppressed():
    # 端到端模型自带去重，重复 NMS 会误删并列候选
    rows = [[100, 100, 200, 300, 0.9, 0], [102, 102, 198, 298, 0.8, 0]]
    out = decode_outputs(_e2e(rows), "yolo_end2end", "detect", LABELS,
                         (640, 640), 0.25, 0.5, (640, 640))
    assert len(out) == 2


def test_unknown_class_index_falls_back_to_string():
    rows = [[10, 10, 50, 90, 0.7, 99]]
    out = decode_outputs(_e2e(rows), "yolo_end2end", "detect", LABELS,
                         (640, 640), 0.25, 0.5, (640, 640))
    assert out and out[0]["label"] == "99"
