# PP-OCRv6 时间角标微调：多 ROI 对照（仅离线实验）

> TrajNexa_main / 2026-10-26。新增命令行工具，不修改正式 OCR Worker、数据库、报告及原始媒体。**尚无真实 PP-OCRv6 对照结果，禁止声称识别率已提高。**

## 为什么需要单独测试

实测 OSD 原文 `2026-10-3/15:17:19`，人工从画面读为 `2026-10-23 15:17:19`。树叶等复杂背景干扰右上角日期，原识别区域较大。仅允许斜杠分隔符并不能恢复被漏识别的数字。

## 对照策略

复用 `backend/scripts/benchmark_ppocrv6_clock.py` 的 PP-OCRv6 单行文字识别和四种图像预处理，逐个测试：

- `wide`：x=55–99%，y=0–9.5%（此前默认）
- `tight`：x=62–99%，y=0–6%（减少背景）
- `compact`：x=63.5–96.5%，y=0–5.2%（更紧；先检查没有裁断数字）

每个区域依次运行 `original / upscaled_2x / clahe_2x / light_text_2x`。一个模型只初始化一次，三档 ROI 共 12 个结果。输出每次原文、解析结果、模型分数、耗时和不同区域是否冲突。模型高置信度仍不等于文字正确，也不证明真实拍摄时间。

## Windows Docker Desktop 中测试

把有权使用的截图放入 `storage/validation/sample.png`，不要提交监控图片到公开 GitHub。然后：

```powershell
git pull
docker compose -f compose.yaml -f compose.ocr.yaml up --build -d
docker compose -f compose.yaml -f compose.ocr.yaml exec worker python scripts/benchmark_ppocrv6_roi_matrix.py /data/storage/validation/sample.png --expected "2026-10-23 15:17:19" --preview-dir /data/storage/validation/roi_matrix_preview --output /data/storage/validation/roi_matrix_result.json
```

查看 `storage/validation/roi_matrix_preview`，确保 `compact` 没裁断角标。如果不同设备的时间位置不同，使用 `--roi 0.55,0,0.99,0.095` 指定单个区域，或者调 `--presets tight compact`。

`--expected` 仅用于人工真值评分，**不会参与 OCR 推理或自动补造缺失日期**。返回 `single_candidate_unverified` 也只是“不同裁切得到了一个候选”，不代表真实拍摄时间确认；若出现多个候选则标记 `conflicting_candidates_require_review`。

## 测试与下一步

独立逻辑测试使用模拟 PP-OCRv6 返回，覆盖 ROI 尺寸关系、冲突保留、单模型初始化一次；不能代表真实模型准确率。优先收集多张已人工核对的白天、夜间、红外角标样本，比较完整日期时间精确匹配率、错误日期率、拒判率和每张推理耗时。真实效果证明改善后才考虑把最优 ROI 做成按摄像头配置并接入 MediaInspector。
