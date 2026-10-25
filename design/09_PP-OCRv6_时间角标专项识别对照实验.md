# PP-OCRv6 时间角标专项识别对照实验（独立、可回滚）

> 2026-10-26 · 当前为实验方案和测试脚本，不代表实测 OCR 准确率已达到要求。

## 为什么不是直接换版本？

现有用户环境是 PaddleOCR 3.7.0 / PaddlePaddle 3.2.0；**PaddleOCR 3.7 的通用产线默认已使用 PP-OCRv6 medium**。先前实测时间角标 OCR 文本 \`2026-10-3/14:59:59\`，人工核对图像为 \`2026-10-23 14:59:59\`。所以本轮实验的区别是 **绕过通用文字检测，固定裁切右上角时间行，直接用 PP-OCRv6 文本识别模块**，不是简单升级版本号。

脚本：\`backend/scripts/benchmark_ppocrv6_clock.py\`。

- 默认仅运行 \`TextRecognition(model_name="PP-OCRv6_medium_rec", device="cpu")\`；可额外对照 \`PP-OCRv6_small_rec\`。
- 右上角 ROI 默认相对坐标：\`x=0.55–0.99, y=0.00–0.095\`。可用 \`--roi\` 修改。
- 对比 \`original\`、\`upscaled_2x\`、\`clahe_2x\`、\`light_text_2x\`。
- 输出 OCR 原始文字、模型分数、推理耗时、合法时间解析与逐字正确性。**模型分数不等于录像时间真实性**。
- 人工确认的 \`--expected\` **仅用于实验评分**，不会参与 OCR 推理，更不会自动修复日期缺字或覆盖历史报告。
- 不改变 M1 原有 OCR Worker、API、数据库、TrajNexa_Map；不提交任何原始监控素材或识别结果到公开 GitHub。

## Windows Docker Desktop 试验步骤

在 \`TrajNexa_main\` 根目录中：

\`\`\`powershell
git pull
New-Item -ItemType Directory -Force storage\validation | Out-Null
Copy-Item "C:\你的截图路径\截图.jpg" "storage\validation\sample.jpg"
docker compose -f compose.yaml -f compose.ocr.yaml up --build -d
docker compose -f compose.yaml -f compose.ocr.yaml exec worker python scripts/benchmark_ppocrv6_clock.py /data/storage/validation/sample.jpg --models PP-OCRv6_medium_rec --expected "2026-10-23 14:59:59" --preview /data/storage/validation/ppocrv6_roi.jpg --output /data/storage/validation/ppocrv6_medium_result.json
\`\`\`

先打开 \`storage\validation\ppocrv6_roi.jpg\`，确认时间角标完整保留。然后查看 \`storage\validation\ppocrv6_medium_result.json\` 中每种变体的 \`raw_text\`、\`parsed_clock_unverified\`、\`exact_match_to_manual_truth\` 与 \`inference_seconds\`。

若需要加测 small（可能额外下载权重、扩大模型缓存）：

\`\`\`powershell
docker compose -f compose.yaml -f compose.ocr.yaml exec worker python scripts/benchmark_ppocrv6_clock.py /data/storage/validation/sample.jpg --models PP-OCRv6_medium_rec PP-OCRv6_small_rec --expected "2026-10-23 14:59:59" --output /data/storage/validation/ppocrv6_compare.json
\`\`\`

模型下载源不通时按官方说明设置 \`PADDLE_PDX_MODEL_SOURCE=BOS\`，但网络/权重可用性尚未在开发机验证。脚本需要当前 OCR Worker 的 PaddleOCR 3.x，正常 M0 Worker 不包含该依赖。

## 验证状态

本地完整回归：**45/45 Python 测试通过**；新增 5 项为裁切、模拟模型输出适配、完整时间比较及不自动修正测试。当前工具环境不含 \`paddleocr\` / \`paddlepaddle\`，**未运行 PP-OCRv6 真实推理**。不能据此断言这张截图已经识别成功。

获得至少 10 张经过人工确认的不同监控角标截图后，再比较完全一致率、拒判与延迟，决定是否集成主系统。

## 官方依据

- PaddleOCR 通用产线：https://www.paddleocr.ai/main/version3.x/pipeline_usage/OCR.html
- 独立文本识别接口：https://www.paddleocr.ai/main/version3.x/module_usage/text_recognition.html
