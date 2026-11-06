"""RF-DETR Small / Medium / Large 适配器：复用 TrajNexa BGR → 检测框统一契约。

仅加载部署者放在本地的 .pth 文件；不允许管理 API 在推理时联网下载权重。
RF-DETR 的 ONNX 导出与 D-FINE 的双输入 ONNX 契约不同，不能复用 DfineEngine。
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

from app.engines.base import BaseEngine, DetectionResult, EngineInferenceError

logger = logging.getLogger("app.engines.rfdetr")


RFDETR_RESOLUTIONS = {"small": 512, "medium": 576, "large": 704}


class RfdetrEngine(BaseEngine):
    """RF-DETR 三种官方 COCO 检测变体，使用各自模型类及原生输入尺寸。"""

    ENGINE_NAME = "rfdetr_pytorch"

    def __init__(self, model_variant="small", **kwargs):
        super().__init__(**kwargs)
        self.model_variant = str(model_variant or "small").lower()
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
        resolution = RFDETR_RESOLUTIONS.get(self.model_variant)
        if resolution is None or tuple(self.input_size) != (resolution, resolution) or self.task_type != "detect":
            logger.warning("RF-DETR 变体、输入分辨率或任务类型不匹配")
            return False
        if not self.labels or len(self.labels) != 80:
            logger.warning("RF-DETR COCO 预训练权重必须携带按 COCO 顺序的 80 类标签")
            return False
        if not self.is_available():
            logger.warning("RF-DETR: 未安装可选依赖 rfdetr")
            return False
        try:
            from rfdetr import RFDETRSmall, RFDETRMedium, RFDETRLarge
            variants = {
                "small": RFDETRSmall,
                "medium": RFDETRMedium,
                "large": RFDETRLarge,
            }
            # 选定变体对应的官方模型类；绝不通过调整尺寸来伪装权重变体。
            device = "cuda" if self.device in ("gpu", "cuda") else "cpu"
            # 显式绝对路径：仅从本地权重读取，不触发官方裸文件名自动下载。
            self._model = variants[self.model_variant](
                pretrain_weights=str(path.resolve()), device=device)
            self._loaded = True
            return True
        except Exception as exc:
            logger.warning("RF-DETR %s 加载失败: %s", self.model_variant, type(exc).__name__)
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
            logger.warning("RF-DETR %s 推理失败: %s", self.model_variant, type(exc).__name__)
            raise EngineInferenceError("rfdetr_inference_failed") from exc
