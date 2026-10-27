# TrajNexa 迹枢 · 主系统前端（V1.3 UI 原型）

按《TrajNexa（迹枢）主系统页面与交互设计说明书 V1.3》重建的主系统工作台。
业务数据来自 `src/data/mock.ts`；地图已接入 **TrajNexa_Map 只读空间快照 + OSM 瓦片**，其余功能未接线。

## 运行

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # vue-tsc --noEmit && vite build
npm run typecheck
```

技术栈：Vue 3.5 `<script setup>` + TypeScript(strict) + Vite 6 + Leaflet 1.9。
地图开发代理：`/map-api` → `MAP_PROXY_TARGET`（默认 `http://localhost:8088`，即本机 TrajNexa_Map）。
主系统 API 代理：`/api` → `API_PROXY_TARGET`（默认 `http://localhost:8000`；本机 compose 把 api 容器映射到 8010 时用
`API_PROXY_TARGET=http://127.0.0.1:8010 npm run dev`）。

## 页面结构

| 区域 | 组件 | 说明 |
| --- | --- | --- |
| 顶栏 | `AppHeader.vue` | 案件名、轮次与方向（R2 向前倒查 18:00–20:12）、Map 数据版本、时间范围、录像覆盖入口 |
| 地图（只读） | `WorkspaceMap.vue` | 顶部「地图 / 卫星 / 三维」分段 + 摄像头搜索 + 图层开关条；Leaflet 承载 OSM 原色瓦片或 Esri 影像，叠加 TrajNexa_Map 镜头、标定视域与演示业务层 |
| 目标特征栏 | `TargetFeatureBar.vue` | 目标主图 + 查看详情/编辑特征 + 6 个特征格（属性 / 取值 / 可信度 / 编辑）+ 参考要素 E01–E04 缩略图与「添加要素」 |
| 观测时间线 | `ObservationTimeline.vue` | 时间轴卡片：时间 + 镜头 + 要素编号 + 轮次、缩略帧 + 状态胶囊 + 追查方向、特征摘要；三个下拉筛选/排序 |
| 任务控制（底部） | `BottomDock.vue` | 左侧 5 个分区（任务控制 / 录像资源 / 正在分析 / 候选结果 / 历史记录），右侧轮次步骤条、折叠详情行、继续/暂停/终止 |
| 底部容器 | `BottomDock.vue` | 可拖拽 196px–58vh、可收起；收起时显示一行摘要 |

弹层：`modals/CoverageModal.vue`（录像覆盖时间轴）、`modals/AddElementModal.vue`（画面来源 → 识别 → 人工确认时间与摄像头名称）、`modals/FeatureDetailSheet.vue`（特征校验/历史）、`modals/ElementsDrawer.vue`（要素列表）、`modals/CorrectionDialog.vue`（空间修正工单）。

### 观测时间线的结构

- **一行一条**：左侧时间轴圆点按状态着色，卡片内第一行是 `时间 + 镜头 + 要素编号 + 轮次`，第二行是 `缩略帧 + 状态胶囊 + 追查方向`，第三行是特征摘要（单行截断）。
- **状态只用一枚胶囊**：起始锚点 / 已检验 / 待核查 / 已排除 / 检测候选 / 盲区，颜色与圆点一致，不再重复成“候选观测 · …”前缀。
- **筛选与排序**：全部轮次 / 全部状态 / 时间正序（可切倒序）三个下拉；筛选只改视图，不改观测事实。
- **长度不受限**：条目多时列表自身滚动（页脚常驻 `按真实观测时间排序 · 不构成身份结论`），不再分页。

### 任务控制的分区

- 左侧竖排五个分区，选中项高亮；右侧内容随分区切换，全部使用同一套 mock 数据。
- 「任务控制」：轮次摘要 + 5 段步骤条（准备中 / 目标索引 / 视频排队 / 模型分析 / 结果整理）+ 继续分析 / 暂停 / 终止 + 三条可折叠详情（录像资源索引、正在分析的视频、候选结果）。
- 其余分区：录像资源六项指标、作业清单、候选结果清单、历史记录空态。


## 地图数据接入

- **底图**：默认「地图」= OSM 标准瓦片（`tile.openstreetmap.org`），**原色不做任何滤镜**，与 TrajNexa_Map 的显示一致；
  可切「卫星」= Esri World Imagery（`server.arcgisonline.com`，影像 © Esri/Maxar/Earthstar）；「三维」需要 WebGL 地形服务，本版本置灰未接入。
  暗色只用在控制台外壳（工具条、面板、状态栏），不对底图做反相。
- **摄像头搜索**：工具条搜索框按 `camera_id / index_code / external_index_code / zone` 匹配快照镜头，命中列表点选即定位并打开镜头卡。
- **镜头数据**：`GET /api/v1/spatial/cameras`（主系统直连 Map 只读库的 M2b 接口，见 `design/17`），
  字段 `map_camera_id / camera_uuid / display_name / location / bearing_deg / tilt_deg / horizontal_fov_deg /
  range_m / pose_quality / fov_levels`，`spatial_revision` 为内容哈希，坐标恒为 `EPSG:4326`；
  前端在 `src/services/mapSnapshot.ts` 里把它适配成地图快照结构，**不再依赖 Map 的 HTTP 服务，也不需要 `/map-api` 代理**。
- **视域配色与 Map 完全一致**：`fov_levels` 按 辨识（identification `#ff3131`）/ 识别（recognition `#ffbf00`）/
  观察（observation `#00b300`）三级下发，填充 0.4、无描边，和 Map 的 `FOV_STYLES` 同源同色。
- **姿态未知不画扇形**：只有 `pose_quality = calibrated` 且带 `fov_levels`（或旧字段 `fov_rings`）的镜头才绘制视域；
  其余镜头只落点位，界面提示“姿态未知（不画扇形）”，不臆造朝向。
- **图层**：Map 镜头（快照）/ 标定视域 / 任务镜头（演示）/ 目标锚点 / 轨迹与盲区 / 名称标注，均可独立开关。
- **默认视野**：桂林临桂（`110.20302,25.24968 – 110.21394,25.25272`，约 1.1×0.3 km，初始 z17），
  Map 库里两台**已标定姿态**的镜头就在这片，三级视域能直接看到；其余镜头随平移查看。
- **演示业务层**：13 台任务镜头、轨迹分段、E01–E04 锚点仍来自 `mock.ts`，按同一 bbox
  做仿射投影后叠加，仅用于界面演示，不代表真实轨迹；图例与状态栏均已标注来源。
- Map 空间数据不可用（`/api/v1/spatial/cameras` 返回错误）时地图不空白：顶部提示“Map 空间数据不可用：仅显示演示业务层”，并提供重试。

## Map GIS 视图（M2c）

地图面板右下角有三态切换：`业务示意 / 空间原始预览 / 真实 GIS 地图`。前两个分别是工作台示意地图与 M2b 空间数据预览，
`真实 GIS 地图` 是独立的只读 GIS 视图（`src/components/LiveGisMap.vue`），业务示意地图不受影响。

- **数据来源**：一个 `GET /api/v1/gis/viewport?bbox=w,s,e,n` 请求，后端在**同一个 repeatable-read 只读事务**里取四类数据：
  - 道路 = `cameras_osm_road` 底图 + `cameras_map_feature`（`kind='road'`，人工修订优先；已删除的不出现）
  - 建筑 = `cameras_building`（含 `cameras_building_source` 升级来源）+ `cameras_map_feature`（`kind='building'`）
  - 镜头 = `cameras_camera` ⟕ `cameras_registry` ⟕ `cameras_cameratags`（旧名称兜底）
  - 视域 = `cameras_camerafocus`（`scenario='mean'`），沿用 Map 的模型几何，**不是实测覆盖**
  每个要素带 `source_key` / `source_ref` / `reviewed_geometry`，人工编辑过的道路与建筑以紫色描边区分。
- **上限与失败方式**：单图层 1500 个要素、500 个镜头、1500 个视域、8 MiB 响应；超限返回
  `gis_viewport_limit_exceeded_zoom_in`（422），**拒绝而不是截断**。前端收到 `zoom_in` 会自动放大一级重取（最多两跳），
  仍超出才显示错误横幅，并在出错时清空旧图层，绝不沿用上一视野的几何。
- **底图**：默认不套栅格瓦片，只用台账里的矢量道路与建筑；「在线 OSM 瓦片（可选）」勾选后才向
  `tile.openstreetmap.org` 取图，署名保留在下方图例里。
- **找镜头**：状态栏有「视野内 N / 库内 55 个镜头 · 7 个有模型 FOV」计数，输入框可按名称、Map ID、UUID 即时搜索，
  点结果直接定位到 18 级；另有「定位 Map 镜头」按钮跳到台账里第一台有坐标的镜头，「刷新」重取当前视野。
- **署名与结论**：图例固定 `OSM 数据 © OpenStreetMap 贡献者 · ODbL` 与「FOV 是模型推算，非实测覆盖」；
  所有面板都不构成身份结论。
- **只读账号需要的最小权限**（本机已按此授予，列级授权）：`cameras_camera`、`cameras_registry`、`cameras_camerafocus`、
  `cameras_osm_road`、`cameras_building`（含 `osm_id`）、`cameras_building_source`（`id`,`promoted_building_id`）、
  `cameras_map_feature`（`id`,`kind`,`geom`,`name`,`source_key`,`source_ref`,`is_deleted`）、
  `cameras_cameratags`（`id`,`name`,`value`,`camera_id_id`）。


## 添加目标要素：时间与摄像头名称识别

本版本只接「角标时间 + 摄像头名称」两项识别；人体 / 人脸 / 车辆检测尚未接入，界面不做自动框选，也不给身份结论。

- **画面来源**：媒体库（`GET /api/v1/media`）按 sha256 去重后列出，用顶部下拉选择、可搜索、可上传（`POST /api/v1/media`）；
  选中后经 `GET /api/v1/media/{id}/content` 回显只读原图或视频，预览占满左侧可用区域，不做任何改写。
- **识别链路**：`POST /api/v1/media/{id}/inspect` 建预检作业 → 轮询 `GET /api/v1/jobs/{id}` → `GET /api/v1/jobs/{id}/report`，
  实现在 `src/services/mainApi.ts`；作业状态、进度、失败原因直接显示在弹窗里，「识别依据」可展开查看 OCR 原始行。
- **时间**：优先取结构化样本 `time.overlay_time_samples[].time_text`（如 `2026-10-3/14:59:59`），解析成 `日期 + HH:MM:SS`
  并附 OCR 置信度；`source_conflict_needs_review` 给“时间存在冲突，需人工确认”，`observed_unverified` 给“时间待人工确认”。
  识别不到可手填或标「时间未知」。
- **摄像头名称**：先取后端结构化结果（`overlay_name_raw` / `camera_candidates` / 文件名提示 / 目录候选）；
  结构化失败时，再从 OCR 行里挑“像名称”的文本（带分隔符或数字、排除时间与地名）作为低信任候选，标注「OCR 文本候选」。
  名称输入框**始终存在且可编辑**：识别只填初值，错字可改、可清空；命中镜头目录 =「来源已确认」，只识别到名称 =「候选待核验」，都没有 =「仅确定区域」。
  名称匹配度只表示匹配程度，界面常驻“名称匹配度 ≠ 同一目标的身份结论”。
- **保存**：`actions.saveElement` 写入要素 + 起始锚点观测 + 按属性生成特征候选（`proposed`，计入待校验）；
  要素列表的「编辑」走 `actions.updateElement` 复用同一份表单。保存后时间线、参考要素、特征格、地图锚点同步刷新。
- **后端新增只读接口**：`GET /api/v1/media/{media_id}/content`（`backend/app/main.py`），仅用于回显原始画面，
  路径做 `is_relative_to(storage_root)` 校验，不改写、不转码。

## 设计约定

- 颜色/间距/字号全部来自 `src/styles/tokens.css`；组件样式只引用变量。
- 三套语义色严格分开：绿色 `--ok` = 已索引/已核验；琥珀 `--warn` = 待复核/未知；红色 `--danger` = 异常；紫 `--violet` = 锚点/要素；蓝 `--accent` = 交互；青 `--cyan` = Map 快照镜头。
- 摄像头置灰只表示“当前索引范围内未发现录像”，不代表离线；文案与 `title` 均按此表述。
- 每个操作面都带“不构成身份结论 / 需人工核验”提示；机器提议与人工值并列，不静默覆盖。

## 演示入口

- 顶栏「空任务态 / 示例任务」：切换空工作台（无目录、无要素、无观测、无作业）。
- URL 直达（便于截图核对）：`?modal=coverage|element|elements|feature|correction|empty`。
- 分类可用 `#coverage` 等作为 hash 入口。

## 尚未接线（下一步）

- **人体 / 人脸 / 车辆检测**：按当前范围不做，弹窗里没有自动框选与检测结果；可观察属性由人工填写。
- **要素空间落点**：识别到的摄像头名未绑定 Map 镜头目录前，锚点仍用演示坐标；绑定后才能得到真实落点。
- 目录选择、增量索引、研判启停、结果写入、佐证导出等按钮只做本地提示（`toast`）。
- Map 快照目前由开发代理直连 Map 服务；生产部署需在网关提供同路径转发（`/map-api/**` → Map 服务），
  或改为主系统后端 `/api/v1/map/snapshot` 服务端代理。
- 演示业务层（任务镜头/轨迹/锚点）仍需替换为主系统自身的真实业务数据。
- 录像预览仅用 `MockFrame.vue` 占位，后续对接按需截取接口。

## 截图

`docs/screenshots/` 内含主界面（真实 OSM 底图 + Map 镜头）、覆盖时间轴、添加目标要素（真实识别结果）、特征详情、空任务态。