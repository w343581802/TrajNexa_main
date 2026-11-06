"""RF-DETR Small 适配器：复用 TrajNexa 的 BGR → 检测框统一契约。

仅加载部署者放在本地的 .pth 文件；不允许管理 API 在推理时联网下载权重。
RF-DETR 的 ONNX 导出与 D-FINE 的双输入 ONNX 契约不同，不能复用 DfineEngine。
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from app.engines.base import BaseEngine, DetectionResult, EngineInferenceError

logger = logging.getLogger("app.engines.rfdetr")


class RfdetrEngine(BaseEngine):
    """RF-DETR Small PyTorch 检测引擎（首批接入 Small / COCO 80 类）。"""

    ENGINE_NAME = "rfdetr_pytorch"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._model = None

    @staticmethod
    def is_available():
        return importlib.util.find_spec("rfdetr") is not None

    @staticmethod
    def version():
        try:
            from importlib.metadata import version
            return version("rfdetr")
        except Exception:
            return None

    def load(self):
        self._loaded = False
        self._model = None
        path = Path(self.model_file)
        if not path.is_file() or path.suffix.lower() != ".pth":
            logger.warning("RF-DETR: 未找到本地 .pth 权重文件")
            return False
        # 官方 Small 权重的输入为 512x512；使用其他变体须单独注册相应适配器，
        # 不能仅修改尺寸就把 Small 权重当 Medium/Large 加载。
        if tuple(self.input_size) != (512, 512) or self.task_type != "detect":
            logger.warning("RF-DETR Small 仅支持 detect / 512x512")
            return False
        if not self.labels or len(self.labels) != 80:
            logger.warning("RF-DETR Small COCO 预训练权重必须携带按 COCO 顺序的 80 类标签")
            return False
        if not self.is_available():
            logger.warning("RF-DETR: 未安装可选依赖 rfdetr")
            return False
        try:
            from rfdetr import RFDETRSmall
            # 明确设备，不因为检测到 GPU 就悄悄更换用户选择。
            device = "cuda" if self.device in ("gpu", "cuda") else "cpu"
            # 使用官方安全加载默认值（trust_checkpoint=False）；仅从本地权重读取。
            self._model = RFDETRSmall(pretrain_weights=str(path.resolve()), device=device)
            self._loaded = True
            return True
        except Exception as exc:
            logger.warning("RF-DETR Small 加载失败: %s", type(exc).__name__)
            self._model = None
            return False

    def detect(self, frame_bgr):
        if not self.ready() or self._model is None:
            raise EngineInferenceError("rfdetr_not_loaded")
        if frame_bgr is None:
            return []
        try:
            import cv2

            # 官方 RF-DETR predict(array) 使用 RGB，而本项目引擎输入是 BGR。
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            result = self._model.predict(frame_rgb, threshold=float(self.conf_threshold))
            out = []
            for box, class_id, confidence in zip(result.xyxy, result.class_id, result.confidence):
                idx = int(class_id)
                if not 0 <= idx < len(self.labels):
                    raise ValueError("rfdetr_class_index_out_of_range")
                score = float(confidence)
                if score < self.conf_threshold:
                    continue
                out.append(DetectionResult(
                    box=[float(v) for v in box],
                    label=self.labels[idx],
                    score=score,
                ))
            return out
        except Exception as exc:
            logger.warning("RF-DETR Small 推理失败: %s", type(exc).__name__)
            raise EngineInferenceError("rfdetr_inference_failed") from exc
