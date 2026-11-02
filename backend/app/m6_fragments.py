"""M6 D2：同帧去重（F1）与人体碎片归属（F2）。

问题：D-FINE 会在同一个人身上同时给出"完整人体框"和"头肩碎片框"，
两者 IoU 只有 0.30 左右，0.5 的常规去重合不掉。碎片框一旦拿到一条轨迹，
同一个真值就会被拆成多段（D1 里 person_003 被拆成 3 段）。

两条规则都与"降低全局 IoU 门限"无关：
- F1 只处理 **IoU ≥ 0.5** 的重复框，属于常规 NMS 范畴；
- F2 处理"小框几乎完全落在大框内"的包含关系，绝不放宽 IoU 门限，
  拿不准时保留歧义（两个框都留下），宁可多一条轨迹也不合并两个人。

所有合并都保留原始检测编号，轨迹仍能追回原图与原检测框。
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_FRAGMENT_PARAMS = {
    # 碎片必须基本落进人体框里
    "containment_ratio": 0.70,
    # 面积比上限：超过它更像两个人而不是一个人身上的碎片
    "max_area_ratio": 0.60,
    # 高度比上限：头肩碎片约为人体的 1/3～1/2
    "max_height_ratio": 0.50,
    # 人体框太小的时候，包含关系没有判别力
    "min_body_height": 60.0,
    # 中心点必须落在人体框内（允许 5% 外扩）
    "center_margin": 0.05,
    # 头肩碎片的形态特征：顶边与人体框几乎齐平、只占上半身、宽度明显更窄。
    # 这条用来挡住“另一个完整的人恰好站进大框里”——那种情况顶边不会齐平。
    "max_top_offset_ratio": 0.15,
    "max_bottom_ratio": 0.60,
    "max_width_ratio": 0.80,
    # 用上一帧轨迹做消歧时的最小 IoU
    "history_iou": 0.30,
}


@dataclass
class Detection:
    index: int
    bbox: tuple
    confidence: float
    label: str = ""
    merged_from: list = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {"bbox": [float(v) for v in self.bbox],
               "confidence": float(self.confidence),
               "index": int(self.index), "label": self.label}
        if self.merged_from:
            out["merged_from"] = list(self.merged_from)
        return out


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def _containment(small, big) -> float:
    """小框有多大比例落在框 big 里。"""
    x1, y1 = max(small[0], big[0]), max(small[1], big[1])
    x2, y2 = min(small[2], big[2]), min(small[3], big[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    area = _area(small)
    return inter / area if area > 0 else 0.0


def _center_inside(small, big, margin=0.05) -> bool:
    cx = (small[0] + small[2]) / 2.0
    cy = (small[1] + small[3]) / 2.0
    w = (big[2] - big[0]) * margin
    h = (big[3] - big[1]) * margin
    return big[0] - w <= cx <= big[2] + w and big[1] - h <= cy <= big[3] + h


def dedupe_same_frame(detections, iou_threshold=0.5, prefer="conf"):
    """F1：同帧 IoU 去重，保留更可信的框，返回 (保留, 被丢弃记录)。

    prefer="conf" 按置信度保留；prefer="area" 按面积保留（完整人体优先）。
    """
    items = list(detections)
    order = sorted(items, key=lambda d: (
        (d.confidence if prefer == "conf" else _area(d.bbox)), _area(d.bbox)),
        reverse=True)
    kept, dropped = [], []
    for det in order:
        clash = None
        for keep in kept:
            value = _iou(det.bbox, keep.bbox)
            if value >= iou_threshold:
                clash = (keep, value)
                break
        if clash is None:
            kept.append(det)
        else:
            dropped.append({"index": det.index, "bbox": list(det.bbox),
                            "confidence": det.confidence,
                            "duplicate_of": clash[0].index,
                            "iou": round(clash[1], 4)})
    kept.sort(key=lambda d: d.index)
    return kept, dropped


def attribute_fragments(detections, previous_boxes=None, params=None):
    """F2：把"基本落在人体框内"的小框并进人体框，作为辅助观测。

    返回 (保留的检测, 合并记录, 保留歧义记录)。
    - 只有唯一候选人体、且满足包含/面积/高度/中心四条件才合并；
    - 候选不唯一时用上一帧轨迹消歧，仍不唯一就保留两个框（记为 ambiguity）；
    - 绝不因为"面积小"就无条件合并（重叠的两个人都可能一大一小）。
    """
    p = dict(DEFAULT_FRAGMENT_PARAMS)
    p.update(params or {})
    items = sorted(detections, key=lambda d: _area(d.bbox), reverse=True)
    bodies, fragments, merged, ambiguous = [], [], [], []
    for det in items:
        claims = []
        for body in bodies:
            if _area(body.bbox) <= _area(det.bbox):
                continue
            if (body.bbox[3] - body.bbox[1]) < p["min_body_height"]:
                continue
            area_ratio = _area(det.bbox) / _area(body.bbox)
            height_ratio = (det.bbox[3] - det.bbox[1]) / max(1e-6, body.bbox[3] - body.bbox[1])
            if area_ratio > p["max_area_ratio"] or height_ratio > p["max_height_ratio"]:
                continue
            if _containment(det.bbox, body.bbox) < p["containment_ratio"]:
                continue
            if not _center_inside(det.bbox, body.bbox, p["center_margin"]):
                continue
            body_h = body.bbox[3] - body.bbox[1]
            if abs(det.bbox[1] - body.bbox[1]) > p["max_top_offset_ratio"] * body_h:
                continue          # 顶边不齐：更像框里还站着另一个完整的人
            if det.bbox[3] > body.bbox[1] + p["max_bottom_ratio"] * body_h:
                continue          # 伸到下半身：不是头肩碎片
            if (det.bbox[2] - det.bbox[0]) > p["max_width_ratio"] * (body.bbox[2] - body.bbox[0]):
                continue          # 与人体同宽：不是局部碎片
            claims.append(body)
        if not claims:
            bodies.append(det)
            continue
        if len(claims) > 1 and previous_boxes:
            scored = [(max([_iou(b.bbox, prev) for prev in previous_boxes if prev], default=0.0), b)
                      for b in claims]
            scored.sort(key=lambda x: x[0], reverse=True)
            if scored[0][0] >= p["history_iou"] and scored[0][0] > scored[1][0] + 0.05:
                claims = [scored[0][1]]
        if len(claims) == 1:
            body = claims[0]
            body.merged_from.append({"index": det.index, "bbox": [float(v) for v in det.bbox],
                                     "confidence": float(det.confidence),
                                     "area_ratio": round(_area(det.bbox) / _area(body.bbox), 3),
                                     "containment": round(_containment(det.bbox, body.bbox), 3)})
            merged.append({"index": det.index, "into": body.index,
                           "bbox": list(det.bbox), "confidence": det.confidence})
        else:
            bodies.append(det)
            fragments.append(det.index)
            ambiguous.append({"index": det.index, "bbox": list(det.bbox),
                              "candidates": [b.index for b in claims]})
    bodies.sort(key=lambda d: d.index)
    return bodies, merged, ambiguous


def to_detections(raw):
    """把逐帧检测 dict 转成 Detection；index 用原始列表下标，保证可追溯。"""
    out = []
    for i, item in enumerate(raw):
        box = item.get("bbox") or item.get("box")
        out.append(Detection(index=int(item.get("index", i)),
                             bbox=tuple(float(v) for v in box),
                             confidence=float(item.get("confidence", item.get("score", 1.0))),
                             label=str(item.get("label") or "")))
    return out
