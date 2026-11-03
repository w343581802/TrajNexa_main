"""B4.2：从已确认起点 A 的录像里提取检索参考特征。

只允许用 A 的帧；B 的任何特征都不得预先加载，否则等于把答案喂给检索。
"""
from __future__ import annotations

import numpy as np


class ReferenceUnavailable(RuntimeError):
    """稳定错误码：reference_unavailable_*。"""


def extract_reference(read_image, extract_embedding, observations, model_id,
                      max_frames=3, min_norm=1e-6):
    """observations: [{"frame_id": int, "bbox": [x1,y1,x2,y2]}]，来自 A 的轨迹。"""
    sources, failures, vectors = [], [], []
    for obs in list(observations)[:max_frames]:
        image = read_image(obs["frame_id"])
        if image is None:
            failures.append({"frame_id": obs["frame_id"], "reason": "frame_unreadable"})
            continue
        try:
            vector = extract_embedding(image, obs["bbox"])
        except Exception as exc:
            failures.append({"frame_id": obs["frame_id"], "reason": "extract_failed",
                             "detail": str(exc)[:120]})
            continue
        vector = np.asarray(vector, dtype="float32")
        norm = float(np.linalg.norm(vector))
        if norm < min_norm:
            failures.append({"frame_id": obs["frame_id"], "reason": "zero_embedding"})
            continue
        vectors.append(vector / norm)
        sources.append({"frame_id": obs["frame_id"], "bbox": list(obs["bbox"])})
    if not vectors:
        raise ReferenceUnavailable("reference_unavailable_no_valid_frame")
    mean = np.mean(vectors, axis=0)
    return {"model_id": model_id, "sources": sources, "failures": failures,
            "vector": (mean / np.linalg.norm(mean)).astype("float32")}
