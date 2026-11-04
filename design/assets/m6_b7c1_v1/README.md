# B7-C1 属性识别基准 V1（20 裁剪）

- 观测：10 个，来自 2-2 / 2-1 / 1-1 三个摄像头；每个观测 person + context 两种裁剪，共 20 个。
- 真值：assistant 目视标注（2026-11-04），状态 known / unknown / not_applicable，**待人工复核**。
- 模型：`tencent/WeVisOne-VL-2B`（transformers, CPU bf16，本机）；`qwen3-vl:4b-instruct`（Ollama, q4_K_M, RTX 3060 Laptop 6GB）。
- 提示词：
  - A（结构化）：只输出 JSON，字段 garment_color / garment_type / vehicle_color / vehicle_type / companion_present；看不清写 unknown。
  - B（自然语言）：一句话描述衣着、车辆、是否同行。
  - C（WeVisOne caption）：`describe the image with bounding boxes in detail`（demo 的 Detailed Caption 预设，task=freeform）。
- 解析：WeVisOne 保留 `<ref>文本</ref><box>…</box>` 原文，按 ref 文本还原；颜色按族归一（light blue→blue，dark green→green），衣类按 outerwear/shirt/t_shirt 归并。
- 文件：`annotations.json`（真值+裁剪参数）、`wevisone_v1.json` / `qwen_v1.json`（A+B 原始输出）、`wevisone_caption_v1.json`（C）、`scores.json`（指标）、`crops/`（20 张裁剪）。