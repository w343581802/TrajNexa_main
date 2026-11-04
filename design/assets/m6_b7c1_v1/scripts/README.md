# 本机复现脚本（路径为 2026-11-04 实验机路径）

`b7c1_build.py` 生成裁剪与 `crops.json`（依赖 `C:\Users\...\qwen-test\mk\s6b` 帧目录与 `G:\tools\b7c1` 输出目录）。
`b7c1_run_models.py` 跑提示 A/B；`b7c1_caption.py` 跑 caption 预设；`b7c1_score.py` 生成 `scores.json`。
换机器运行需改这三个绝对路径：帧目录、`G:\tools\b7c1`、WeVisOne 权重路径。