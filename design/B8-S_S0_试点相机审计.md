# B8-S0 试点相机与绑定审计（2026-11-05）

## 结论

- `3-DX-中仁路98号-球`（Map registry id `345f9636-0dff-4fb5-87e9-01d5f1cb9e1d`，map_camera_id `9000000000000030`）**设备类型为 fixed**，不是 PTZ，可作为 S2 首个标定对象；但以下三项未达标，暂不能发布 L2。

## 审计明细

| 项 | 现状 | 判定 |
|---|---|---|
| 设备类型 | `device_kind=fixed` | 通过（固定视角） |
| 姿态 | `pose_quality=calibrated`，bearing 355°，tilt 25°，安装高 4 m | 通过 |
| 水平视角 | `horizontal_fov_deg = NULL` | **缺**：无法生成 L1 方向带，但 L2 单应标定不依赖它 |
| 位置质量 | `position_quality=reported` | 仅报告值，未实测 |
| 通道绑定 | 主系统 `m6_camera_bindings.json` 仅有 1-1/2-1/2-2 三条 `pending_human_review`，**无该相机的 camera_uuid ↔ map_camera_id 绑定** | **阻断**：未绑定不得输出 L2 |
| 录像段 | 查询输出：8dd9ca8c-ebab-4ab7-85d0-fa65ce1b930a / matched / 345f9636-0dff-4fb5-87e9-01d5f1cb9e1d / 1801.923076923077
0faff109-7f92-43cc-8b87-ef75d0dac057 / matched / 345f9636-0dff-4fb5-87e9-01d5f1cb9e1d / 1801.923076923077 | 需人工确认 |

## 闸门

1. 固定视角条件满足，允许先做 S2 标定探针；
2. 发布前必须补该相机的显式绑定（人工核验，禁止按名称相似度自动生成）；
3. `horizontal_fov_deg` 需实测或从设备参数录入，否则 L1 方向带保持关闭；
4. 地图坐标 CRS 尚未核实，标定输出先统一声明 `world_crs`，未确认前不得对外称 WGS84。

## 下一步

按计划第 8 节：在原始截图中选 8–12 组地面控制点、留 3 组独立验证点，用 `backend/scripts/s2_calibration_probe.py` 出留出点 P90 误差，再决定是否进入 Map 编辑器实现。
