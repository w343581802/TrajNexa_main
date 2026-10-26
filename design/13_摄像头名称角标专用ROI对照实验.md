# M1｜摄像头名称 OSD 专项 ROI 实验

**独立实验，不修改现有 MediaInspector、数据库、设备绑定或用户界面。** 用现有 PP-OCRv6 引擎而非新增大模型。

用户提供的 E05 页面中，角标名称识别混入了车牌（例如 C-D80119、C-WF598）和场景中文，名称也出现缺字。当前 `overlay_ocr.py` 对底部 22% 整条区域识别，而且没有利用 PaddleOCR 文字框位置。只看模型置信度无法保证名称正确。

## 实验

新增 `backend/scripts/benchmark_camera_name_roi.py`：

- 右下角 ROI：x=47%–100%，y=84%–100%
- 紧凑 ROI：x=54%–100%，y=90%–100%
- 底部全宽：仅用于诊断，不允许作为设备名称确认依据
- 每个 ROI 分别识别原图和 CLAHE 放大图，保留 OCR 原文、分数、文字框和位置。
- 只把符合特定结构（如 `3-DX-地点-镜头类型`）的结果作为**待核对候选**；车牌和普通场景文本不进入设备候选。
- 如果多个专用 ROI 返回不同名称，标记 `conflicting_name_candidates_review`，不选最高分结果，更不自动绑定摄像头 UUID。

### Windows PowerShell 本机验证

把原始摄像头截图（非 UI 截图）放到 `storage/validation/name_sample.png`，运行：

```powershell
git pull
docker compose -f compose.yaml -f compose.ocr.yaml exec -T worker python scripts/benchmark_camera_name_roi.py `
  /data/storage/validation/name_sample.png `
  --output /data/storage/validation/camera_name_roi.json `
  --preview-dir /data/storage/validation/camera_name_roi_preview
```

检查 JSON 中 `summary` 和 `raw_ocr_rows`，与原始角标逐字对照。原始素材与 ROI 预览留在本地，不上传公共 GitHub。

## 验证边界

自动化测试仅覆盖结构化候选、排除车牌、原文坐标、冲突不绑定和模拟 OCR 路径；**尚未在用户已安装 PaddleOCR 的 Worker 上做真实识别率测试**。

当前 GitHub 主分支的前端 `AddElementModal.vue` 为模拟画面，与用户截图的真实上传窗不同。避免覆盖用户可能尚未推送的前端改动。

### 下一步

在 5–10 张含不同角标布局的人工核实截图上比较专用 ROI 与当前底部扫描的误识别率、候选覆盖率、人工拒判率。实验有效后，再把其能力接入正式 MediaInspector，而非现在直接替换。
