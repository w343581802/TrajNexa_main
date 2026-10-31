# M4b：D-FINE ONNX 可选检测候选（截图，人工复核）

## 范围与约束

本轮在 M4a 的案件截图、框选、原始证据哈希和观测记录基础上增加自动 **检测建议框**。D-FINE 只检测 COCO 中 person / bicycle / car / motorcycle / bus / truck；车辆合并展示为 vehicle。它不识别人脸、不输出姓名、不进行跨镜头重识别、不直接形成 GIS 轨迹。返回的候选不自动写入 `target_observations`；用户选择/改框后仍需按 M4a 的流程确认授权、填写理由并保存观测。

没有 ONNX 权重、SHA-256、CPU runtime 或配置时明确返回 HTTP 503，**不使用随机/模拟框填充业务界面**。

## 技术输入输出

- 官方 D-FINE ONNX 导出：输入 `images` (N,3,640,640) 和 `orig_target_sizes` (N,2)；输出 `labels`, `boxes`, `scores`。此版输入采用 640 方形等比例 letterbox，反向去除 padding 后返回原图坐标。
- 自动候选为 `bbox_normalized=[x1,y1,x2,y2]`，带检测类别、置信分数、原始截图 SHA-256 和模型权重 SHA-256。
- 最大候选数量 30，过滤异常坐标、过小框和非目标类别。置信度不是“属于同一个人”的概率，模型也不能证明对象身份。
- 原图必须先通过上传素材 SHA-256 校验，且必须归属于案件；模型也要先验证配置的 SHA-256，不允许加载未确认的模型版本。
- 模型推理 CPU 只允许一个预览并发，其他请求得到 `m4b-detector-busy`，不会覆盖原始媒体或录像。
- 接口为 `POST /api/v1/media/{media_id}/detections/preview`，请求 `{ "authorized": true, "min_confidence": 0.4 }`。

## 安装真实模型（需自行审核授权、许可及模型出处）

参考 D-FINE 上游仓库 `Peterande/D-FINE` 的官方 `tools/deployment/export_onnx.py`，选择与权重相符的配置与 checkpoint，导出经过检查的 ONNX 模型。不要下载不明来源权重。

PowerShell 示例：

~~~powershell
# 在已审核来源的 D-FINE 项目环境中导出 model.onnx
python tools/deployment/export_onnx.py -c configs/dfine/dfine_hgnetv2_s_coco.yml -r <本机模型权重.pth> --check
# 将导出的 .onnx 复制到 TrajNexa_main/models/dfine.onnx
Get-FileHash .\models\dfine.onnx -Algorithm SHA256
~~~

在 TrajNexa_main 的本地 `.env` 写入（不要提交真实文件路径或敏感信息）：

~~~dotenv
M4A_ENABLED=true
M4B_MODEL_PATH=/models/dfine.onnx
M4B_MODEL_SHA256=<Get-FileHash返回的SHA256十六进制>
~~~

启动可选 CPU 推理镜像：

~~~powershell
docker compose -f compose.yaml -f compose.ocr.yaml -f compose.m4b.yaml up -d --build
~~~

`compose.m4b.yaml` 只为 `api` 安装 ONNX Runtime 并只读挂载 `./models:/models:ro`。前端「添加目标要素」上传截图后先确认处理授权，点击「生成自动检测候选框」。点击一个候选将其放入 M4a 框选区域，必要时重新拖动手动修框，然后点击「保存区域观测」。模型不自动设置已确认摄像头 UUID、地理坐标和人员身份。

## 验证

CI 的 M4 contract checks 运行：

~~~bash
cd backend
python -m pytest -q tests/test_m4a_observations.py tests/test_m4b_detection.py tests/test_m4b_preview_api.py
cd ../frontend
npm ci && npm run build
~~~

该 CI 用注入的假检测模型输出测试坐标映射、参数边界和 HTTP 错误分支；**不等于真实 D-FINE 权重已推理成功**。需要在本地 Docker CPU 环境用真实模型额外验收实际截图上的框选正确性和延迟。

## 下一阶段

M4c 继续做视频关键帧和单镜头 Tracklet，必须复用现有 `overlay_identity.grab_evidence_frame`，不应为视频识别再实现一套未受审计的抽帧链路。
