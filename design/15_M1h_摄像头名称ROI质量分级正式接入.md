# M1h 摄像头名称 ROI 质量分级正式后端接入

保留 PP-OCRv6 为主识别，增加两次右下角专用识别：right_bottom、right_bottom_tight（只在首个可用帧）。

实验复用 scripts.camera_osd_name_quality 的结构完整性判断与同名 OCR 抖动分组；不将相似名称认定为同一设备。

新增 camera.name_roi_analysis 保存原始文本、ROI 坐标、警告、质量状态；旧值另外放入 legacy_overlay_name_raw 与 legacy_camera_candidates。

只有唯一的 candidate_unverified 且无警告时，才以候选名称进行只读目录精确查找，且不自动填充 UUID；其他情况一律标记 name_requires_review，旧 OCR 名称不得绕过审核。

NAME_ROI_ENABLED=true 默认打开（仅 OCR Worker 实际运行时生效），设置 false 可随时回滚；额外两次 OCR 增加 CPU 耗时。

前端添加目标要素界面未改动；旧报告不能直接升级，须对样本重新发起预检。

本轮代码和注入式测试不构成 PP-OCRv6 六张实拍验收；需用户使用真实 Worker 复测。

命令：cd backend && python -m pytest -q tests/test_m1h_camera_name_roi_integration.py
