# TrajNexa 迹枢 · 主系统前端（V1.3 UI 原型）

按《TrajNexa（迹枢）主系统页面与交互设计说明书 V1.3》重建的主系统工作台。
本期只做 **UI 与本地交互**，未接线后端；所有业务数据来自 `src/data/mock.ts`，地图为示意图层。

## 运行

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # vue-tsc --noEmit && vite build
npm run typecheck
```

技术栈：Vue 3.5 `<script setup>` + TypeScript(strict) + Vite 6，无 UI 框架、无网络字体/瓦片。

## 页面结构

| 区域 | 组件 | 说明 |
| --- | --- | --- |
| 顶栏 | `AppHeader.vue` | 任务、Map 快照版本、授权范围、演示态切换 |
| 地图（只读） | `WorkspaceMap.vue` | SVG 示意图层：路网/街区、FOV、轨迹（已核验/盲区/待验证/多分支）、锚点、13 台摄像头、图例与时间窗 |
| 目标特征栏 | `TargetFeatureBar.vue` | 1–3 张目标主图紧凑卡片（可切换、+N 展开全部）、6 项特征（已核验/未知/待复核/冲突）、来源统计 |
| 统一观测时间线 | `ObservationTimeline.vue` | 12 条观测，按锚点/待复核/已审核/已排除筛选 |
| 任务操作台（底部左） | `TaskControlPanel.vue` | 录像目录、目标要素、研判控制 |
| 索引进度 / 分析作业 / 按需预览（底部右） | `JobPanel.vue` | 索引与研判进度分离；预览默认关闭 |
| 底部容器 | `BottomDock.vue` | 可拖拽 196px–58vh、可收起 |

弹层：`modals/CoverageModal.vue`（录像覆盖时间轴）、`modals/AddElementModal.vue`（左图右信息 + 框选）、`modals/FeatureDetailSheet.vue`（特征校验/历史）、`modals/ElementsDrawer.vue`（要素列表）、`modals/CorrectionDialog.vue`（空间修正工单）。

## 设计约定

- 颜色/间距/字号全部来自 `src/styles/tokens.css`；组件样式只引用变量。
- 三套语义色严格分开：绿色 `--ok` = 已索引/已核验；琥珀 `--warn` = 待复核/未知；红色 `--danger` = 异常；紫 `--violet` = 锚点/要素；蓝 `--accent` = 交互。
- 摄像头置灰只表示“当前索引范围内未发现录像”，不代表离线；文案与 `title` 均按此表述。
- 每个操作面都带“不构成身份结论 / 需人工核验”提示；机器提议与人工值并列，不静默覆盖。

## 演示入口

- 顶栏「空任务态 / 示例任务」：切换空工作台（无目录、无要素、无观测、无作业）。
- URL 直达（便于截图核对）：`?modal=coverage|element|elements|feature|correction|empty`。
- 分类可用 `#coverage` 等作为 hash 入口。

## 尚未接线（下一步）

- 目录选择、增量索引、研判启停、结果写入、佐证导出等按钮只做本地提示（`toast`）。
- 地图为示意图层，接入真实 Map 发布快照时替换 `WorkspaceMap.vue` 的 SVG 底图即可，FOV/锚点/轨迹的数据结构已按空间快照组织。
- 录像预览仅用 `MockFrame.vue` 占位，后续对接按需截取接口。

## 截图

`docs/screenshots/` 内含主界面、覆盖时间轴、添加目标要素、特征详情、空任务态。
