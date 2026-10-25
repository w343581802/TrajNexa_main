# M1b｜角标 OCR 与多帧时间一致性（Demo 实施记录）

> 日期：2026-10-25；只属于 TrajNexa 主系统。**本阶段尚未完成真实 PaddleOCR / Docker / 浏览器验收。**

## 功能

- 读取图片顶部及底部各 22% 的角标区域，或对视频最多取三帧；采样偏移随报告保存。
- 使用可选 PaddleOCR 3.x CPU 引擎读取文字并保存原文、置信度、采样时间及所在区域；不修改上传原件。
- 解析年月日 + 时分秒（含连字符、斜杠、中文年月日）；不猜测时区，不用文件创建时间冒充录像时间。
- 多帧角标时间差与视频偏移比较（容差 3 秒），给出 `consistent_unverified` / `inconsistent` / `insufficient_samples` / `unreadable` 等状态；一致也不证明相机已校时。
- 仅对显式含“摄像头/通道/监控点/Camera/Channel/Cam”等标签的文字提取设备名称候选；不自动绑定 `camera_uuid`，不把路边招牌当作相机名称。
- 未安装 PaddleOCR、OCR 未开启、帧不可提取、图像不可解码等均有可区分状态，不强行给出结论。

## 运行（可选 OCR）

默认 `OCR_ENABLED=false`，M0 + M1a 不需要下载 OCR 模型。

要测试真实 PaddleOCR CPU 推理，请在项目 `.env` 中设置：

```dotenv
OCR_ENABLED=true
```

然后使用独立 OCR Worker 镜像：

```bash
docker compose -f compose.yaml -f compose.ocr.yaml up --build -d
```

首次部署需要安装 PaddlePaddle、PaddleOCR 并获取模型权重，联网条件、硬件及容器内存会影响首次初始化。**目前未验证该 Docker 安装和实际 OCR 精度。** 如不需要 OCR，恢复 `OCR_ENABLED=false` 并使用常规 `docker compose up --build -d`。

## 验证

```bash
cd backend
python -m pytest -q
```

本地：原 M0/M1a 测试及 M1b 注入式 OCR 测试总计 **15/15 通过**。这里的文字识别器使用模拟返回，仅证明解析、状态标注、采样一致性与 API 兼容性；**不是 PaddleOCR 识别准确率测试**。

## 后续未完成

1. 按真实相机角标布局配置不同 ROI；含无标签名称、中文字形偏差与跨行名称的样本评测。
2. OCR 角标名称到 TrajNexa_Map `camera_uuid` 的别名映射、冲突处理与人工确认。
3. 参考图场景匹配与绝对朝向/FOV 标定核查。
4. 整段冻结、丢帧、解码异常等逐帧检查，以及时间漂移的更严格分析。

## 开源接口

参考 PaddleOCR 官方 3.x `PaddleOCR(...).predict(image)` 与 `res.json['res']['rec_texts'/'rec_scores']`。详见 https://www.paddleocr.ai/latest/en/quick_start.html 。
