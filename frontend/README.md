# TrajNexa 迹枢 · 主系统前端（V1.3 UI 原型）

按《TrajNexa（迹枢）主系统页面与交互设计说明书 V1.3》重建的主系统工作台。
案件、要素文件、录像资源走主系统后端（`/api/v1`）；地图接入 **TrajNexa_Map 只读空间快照 + 压暗的 OSM 瓦片**；
要素 / 观测 / 特征仍是前端内存态，**没有演示种子数据：新建案件一律空任务态**。

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
| 顶栏 | `AppHeader.vue` | 案件入口（当前案件名 + 编号，未选择时显示「未选择案件 · 新建」）、轮次与方向（R2 向前倒查 18:00–20:12）、Map 数据版本、时间范围、录像覆盖入口 |
| 地图（只读） | `WorkspaceMap.vue` | 顶部「地图 / 卫星 / 三维」分段 + 摄像头搜索 + 图层开关条；Leaflet 承载压暗的 OSM 瓦片或 Esri 影像，叠加 TrajNexa_Map 镜头、标定视域与目标要素锚点 |
| 目标特征栏 | `TargetFeatureBar.vue` | 目标主图 + 查看详情/编辑特征 + 6 个特征格（属性 / 取值 / 可信度 / 编辑）+ 参考要素缩略图与「添加要素」 |
| 观测时间线 | `ObservationTimeline.vue` | 时间轴卡片：时间 + 摄像头名称 + 轮次、缩略帧 + 状态胶囊 + 追查方向、特征摘要；三个下拉筛选/排序 |
| 任务控制（底部） | `BottomDock.vue` | 左侧 4 个分区（任务控制 / 录像资源 / 候选结果 / 历史记录）；「任务控制」「录像资源」都是左右两栏 |
| 底部容器 | `BottomDock.vue` | 可拖拽 208px–62vh、可收起；收起时显示一行摘要（含待审核数） |

弹层：`modals/CaseManageModal.vue`（案件管理：新增 / 切换 / 删除）、`modals/CoverageModal.vue`（录像覆盖时间轴）、`modals/AddElementModal.vue`（「添加要素」先弹选图框，选中即打开弹窗并自动识别 → 人工确认时间与摄像头名称）、`modals/RecordingDirModal.vue`（录像目录选择，支持「添加视频 / 挂载目录」两种入口文案）、`modals/ClipReviewModal.vue`（待审核片段的人工审核）、`modals/FeatureDetailSheet.vue`（特征校验/历史）、`modals/ElementsDrawer.vue`（要素列表）、`modals/CorrectionDialog.vue`（空间修正工单）。

### 案件（研判工作的入口）

- 顶栏「案件」按钮打开案件管理弹窗；左侧列表支持搜索、切换当前案件、二次确认删除，右侧表单新增案件（名称必填，编号服务端生成 `CASE-YYYYMMDD-NN`）。
- 当前案件持久化在 `localStorage`（`trajnexa.currentCaseId`）；新增案件即成为当前案件，切换案件会一起切换工作区（要素 / 观测 / 特征按案件在内存里隔离；刷新后案件保持，工作区回到空任务态）。
- 删除边界写在弹窗与删除确认里：**只清理该案件上传的要素文件**与录像资源的扫描记录；**录像文件与目录结构不受影响**（后端 `release_recording_resources()` 只删登记与结果行）。
- 未选择案件时不能上传新文件（「添加目标要素」会提示先选择或新建案件），也不能添加录像目录。

### 观测时间线的结构

- **一行一条**：左侧时间轴圆点按状态着色，卡片内第一行是 `时间 + 摄像头名称 + 轮次`，第二行是 `缩略帧 + 状态胶囊 + 追查方向`，第三行是特征摘要（单行截断）。
- **状态只用一枚胶囊**：起始锚点 / 已检验 / 待核查 / 已排除 / 检测候选 / 盲区，颜色与圆点一致，不再重复成“候选观测 · …”前缀。
- **筛选与排序**：全部轮次 / 全部状态 / 时间正序（可切倒序）三个下拉；筛选只改视图，不改观测事实。
- **长度不受限**：条目多时列表自身滚动（页脚常驻 `按真实观测时间排序 · 不构成身份结论`），不再分页。

### 底部控制栏的分区

- 左侧竖排四个分区（任务控制 / 录像资源 / 候选结果 / 历史记录），选中项高亮；任务控制、录像资源都是**左右两栏**。
- 「任务控制」
  - 左栏：轮次摘要（R2 · 起始要素 · 向前倒查 · 时间范围）+ **「启动后在做什么」一段文字**（正在读哪一段、在比对什么、几段排队 / 几段待人审；没有进度条和百分比）+ 继续分析 / 暂停 / 终止 + 要素 / 作业 / 分析中 / 待审核一行小字 + 录像索引一行。
  - 右栏：正在分析的摄像头视频，**卡片式**。卡片左边是缩略图或缩略视频（顶部「缩略图 / 缩略视频」统一控制，记在 `localStorage`），右边是摄像头名称、分析时间、状态文字（也不给百分比）；**需要人工审核的卡片自动排到最前并高亮**，点开是 `ClipReviewModal`（确认候选 / 排除 + 备注）。
- 「录像资源」
  - 左栏：添加视频 / 挂载目录 / 开启分析 / 自动分析，下面是已登记目录（每个目录可单独开关「自动」、立即扫描、移除；移除只删登记，不动录像文件）。
  - 「添加视频」「挂载目录」都打开 `RecordingDirModal`：像文件对话框一样按 `/api/v1/recordings/browse` 逐级浏览录像根目录，点文件夹进下一级、点视频文件选中（视频模式），面包屑 / 上一级 / 刷新可用；确认后登记的是**当前目录**，目录里的视频按只读方式扫描角标。接口不可用时给中文提示 + 「重试」，不甩 `Not Found`。
  - 右栏：视频文件信息表（开始时间、摄像头、文件名、时长、匹配状态），按开始时间倒序，最多渲染最近 200 行。
- 「候选结果」：只统计**已经分析过**的视频——已分析 / 有检出 / 无检出 / 待审核 / 失败 / 候选总数，加按摄像头统计与候选条目。
- 「历史记录」：轮次结束后的记录，仍是空态。
- 原「正在分析」分区已取消：当前正在分析的视频并入「任务控制」右栏。


## 地图数据接入

- **底图**：默认「地图」= OSM 标准瓦片（`tile.openstreetmap.org`），再由 `styles/map.css` 的 `.is-dark-base`
  对瓦片层做 `brightness(.7) contrast(1.2)` —— 与 TrajNexa_Map 暗色模式同一套做法与参数（`--dark-map-filter`），
  颜色关系不变、只是整体压暗；标记与视域在别的 pane，语义色不受影响。
  可切「卫星」= Esri World Imagery（`server.arcgisonline.com`，影像 © Esri/Maxar/Earthstar）**保持原色**；
  「三维」需要 WebGL 地形服务，本版本置灰未接入。与 Map 的差异见 `design/22`。
- **摄像头搜索**：工具条搜索框按 `camera_id / index_code / external_index_code / zone` 匹配快照镜头，命中列表点选即定位并打开镜头卡。
- **镜头数据**：`GET /api/v1/spatial/cameras`（主系统直连 Map 只读库的 M2b 接口，见 `design/17`），
  字段 `map_camera_id / camera_uuid / display_name / location / bearing_deg / tilt_deg / horizontal_fov_deg /
  range_m / pose_quality / has_video / fov_levels`，`spatial_revision` 为内容哈希，坐标恒为 `EPSG:4326`；
  前端在 `src/services/mapSnapshot.ts` 里把它适配成地图快照结构，**不再依赖 Map 的 HTTP 服务，也不需要 `/map-api` 代理**。
- **视域配色与 Map 完全一致**：`fov_levels` 按 辨识（identification `#ff3131`）/ 识别（recognition `#ffbf00`）/
  观察（observation `#00b300`）三级下发，填充 0.4、无描边，和 Map 的 `FOV_STYLES` 同源同色。
- **灰色 = 录像资源没识别到**：镜头是否点亮只看「录像资源」里扫描到、并**唯一精确命中台账**的
  `camera_uuids`（`GET /api/v1/recordings/summary`）；没有扫描结果或没选案件时一律灰色，不代表离线。
  添加要素（截图）不会点亮任何镜头。
- **姿态未知不画扇形**：只有 `pose_quality = calibrated` 且带 `fov_levels`（或旧字段 `fov_rings`）的镜头才绘制视域；
  其余镜头只落点位，界面提示“姿态未知（不画扇形）”，不臆造朝向。
- **图层**：Map 镜头（快照）/ 标定视域 / 目标起始锚点，均可独立开关；不再有任务镜头、轨迹与盲区等演示图层。
- **默认视野**：桂林临桂（`110.20302,25.24968 – 110.21394,25.25272`，约 1.1×0.3 km，初始 z17），
  Map 库里两台**已标定姿态**的镜头就在这片，三级视域能直接看到；其余镜头随平移查看。
- **要素锚点**：要素绑定的 `cameraId` 就是 Map 的 `camera_uuid`，锚点直接落在该镜头经纬度；
  没有绑定镜头的要素画成「区域锚点（待定位）」圈，只表示待定位区域，不是真实轨迹。
- Map 空间数据不可用（`/api/v1/spatial/cameras` 返回错误）时地图不空白：顶部提示“Map 空间数据不可用：暂时没有镜头点位与视域”，并提供重试。

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
- **底图**：默认不套栅格瓦片，只用台账里的矢量道路与建筑；「在线暗色瓦片（可选）」勾选后才向
  `tile.openstreetmap.org` 取图并按 `.is-dark-base` 压暗，署名保留在下方图例里
  （Leaflet 自带的缩放/图层控件也一并改成暗色）。
- **找镜头**：状态栏有「视野内 N / 库内 55 个镜头 · 7 个有模型 FOV」计数，输入框可按名称、Map ID、UUID 即时搜索，
  点结果直接定位到 18 级；另有「定位 Map 镜头」按钮跳到台账里第一台有坐标的镜头，「刷新」重取当前视野。
- **署名与结论**：图例固定 `底图 © OpenStreetMap 贡献者 · ODbL` 与「FOV 是模型推算，非实测覆盖」；
  所有面板都不构成身份结论。
- **只读账号需要的最小权限**（本机已按此授予，列级授权）：`cameras_camera`、`cameras_registry`、`cameras_camerafocus`、
  `cameras_osm_road`、`cameras_building`（含 `osm_id`）、`cameras_building_source`（`id`,`promoted_building_id`）、
  `cameras_map_feature`（`id`,`kind`,`geom`,`name`,`source_key`,`source_ref`,`is_deleted`）、
  `cameras_cameratags`（`id`,`name`,`value`,`camera_id_id`）。


## 添加目标要素：时间与摄像头名称识别

本版本只接「角标时间 + 摄像头名称」两项识别；人体 / 人脸 / 车辆检测尚未接入，界面不做自动框选，也不给身份结论。

- **画面来源**：只接受截图（`image/png|jpeg|webp`）。点「添加要素」会**立刻弹出系统选图框**，选中文件后自动打开
  「添加目标要素」弹窗并**马上开始上传 + 识别**（`POST /api/v1/media`）；弹窗里仍保留「选择截图并识别」可重选。
  不再从媒体库挑多个素材，也不再支持视频。未选择案件时不会弹选图框，而是直接提示先选案件。
- **识别链路**：`POST /api/v1/media/{id}/inspect` 建预检作业 → 轮询 `GET /api/v1/jobs/{id}` → `GET /api/v1/jobs/{id}/report`，
  实现在 `src/services/mainApi.ts`；作业状态、进度、失败原因直接显示在弹窗里，「识别依据」可展开查看 OCR 原始行。
- **识别引擎**：OCR 在独立 worker（`compose.ocr.yaml` + `Dockerfile.ocr`）里跑 PaddleOCR，
  代码显式传 `ocr_version='PP-OCRv6'`（不依赖库的默认值），镜像锁 `paddleocr>=3.7,<3.8`；
  报告的 `time.ocr_engine` 记录 `library / library_version / ocr_version`，「识别依据」里显示「paddleocr 3.7.0 · PP-OCRv6」，
  可以直接核对这次跑的是哪一版模型。
- **时间**：优先取结构化样本 `time.overlay_time_samples[].time_text`（如 `2026-10-3/14:59:59`），解析成 `日期 + HH:MM:SS`
  并附 OCR 置信度；`source_conflict_needs_review` 给“时间存在冲突，需人工确认”，`observed_unverified` 给“时间待人工确认”。
  识别不到可手填或标「时间未知」。
- **摄像头名称**：先取后端结构化结果（`overlay_name_raw` / `camera_candidates` / 文件名提示 / 目录候选）；
  结构化失败时，再从 OCR 行里挑“像名称”的文本（带分隔符或数字、排除时间与地名）作为低信任候选，标注「OCR 文本候选」。
  名称输入框**始终存在且可编辑**：识别只填初值，错字可改、可清空；命中镜头目录 =「来源已确认」，只识别到名称 =「候选待核验」，都没有 =「仅确定区域」。
  名称匹配度只表示匹配程度，界面常驻“名称匹配度 ≠ 同一目标的身份结论”。
- **名称相似建议**：精确匹配不到时，后端按目录名称算相似度返回最多 3 条（`camera.name_suggestions`，含 `score`）；
  界面显示为可点标签（如「…-向东 100%」），点击**只填名称**，不改绑定状态、不写 `camera_uuid`，
  状态标为「已采用相似名称，未绑定镜头目录」，绑定仍需人工确认。算法与阈值见 `design/20`。
- **保存**：`actions.saveElement` 写入要素 + 起始锚点观测 + 按属性生成特征候选（`proposed`，计入待校验）；
  要素列表的「编辑」走 `actions.updateElement` 复用同一份表单。保存后时间线、参考要素、特征格、地图锚点同步刷新。
- **后端新增只读接口**：`GET /api/v1/media/{media_id}/content`（`backend/app/main.py`），仅用于回显原始画面，
  路径做 `is_relative_to(storage_root)` 校验，不改写、不转码。

## 顶部工具栏：视频命名

顶部工具栏第一个小工具「视频命名」放在顶栏中间（不占地图高度），用于批量整理录像，不依赖案件：

- 前端只传**路径文本**（本地 demo）：一个视频文件或一个目录，每行一个；目录会递归遍历子文件夹。
  支持宿主机路径（如 `G:\测试数据\视频\正常`）或容器可写路径，后端直接读本地文件，不经过浏览器上传。
- 可勾选「同时重命名子文件夹」：同一子目录内视频识别为同一摄像头时，文件夹按
  `摄像头名称_开始至结束` 改名；名称不一致则跳过。
- API 展开目录、过滤视频，建 `MediaAsset + AnalysisJob(module=video_naming:<batch>)`；
  OCR 只在 worker（PaddleOCR PP-OCRv6）里跑，读每个视频开头 0.5 / 2 / 4 秒的真实画面。
- 按 `摄像头名称_YYYYMMDD_HHMMSS.扩展名` **直接重命名原文件**（不改内容、不转码），并保留开头截图作为证据；
  OCR 缺名称/时间时原名加「OCR未识别」并标「需核对」，不臆造名称。
- 结果表可逐行「看截图」「改名」；OCR 没识别出来的行点「手动命名」，左侧看开头截图，右侧填名称，底部确定直接改磁盘文件。
  下载按钮已移除（文件就在挂载目录里）。也可下载 UTF-8 BOM 的 CSV（含原路径/新路径/文件夹新旧名，Excel 直接打开）。
- 接口、路径规则与命名边界见 `design/24_视频命名工具.md`。

## 设计约定

- 颜色/间距/字号全部来自 `src/styles/tokens.css`；组件样式只引用变量。
- 三套语义色严格分开：绿色 `--ok` = 已索引/已核验；琥珀 `--warn` = 待复核/未知；红色 `--danger` = 异常；紫 `--violet` = 锚点/要素；蓝 `--accent` = 交互；青 `--cyan` = Map 快照镜头。
- 摄像头置灰只表示“当前索引范围内未发现录像”，不代表离线；文案与 `title` 均按此表述。
- 每个操作面都带“不构成身份结论 / 需人工核验”提示；机器提议与人工值并列，不静默覆盖。

## 开发期直达入口

- URL 直达（便于截图核对）：`?modal=coverage|element|elements|feature|correction|empty`。
- 分类可用 `#coverage` 等作为 hash 入口。
- 没有「示例任务 / 空任务态」切换：所有案件都从空任务态开始。

## 尚未接线（下一步）

- **人体 / 人脸 / 车辆检测**：按当前范围不做，弹窗里没有自动框选与检测结果；可观察属性由人工填写。
- 研判启停、结果写入、佐证导出等按钮只做本地提示（`toast`）；要素 / 观测 / 特征尚未按 `case_id` 持久化。
- Map 快照目前由开发代理直连 Map 服务；生产部署需在网关提供同路径转发（`/map-api/**` → Map 服务），
  或改为主系统后端 `/api/v1/map/snapshot` 服务端代理。
- 录像预览仅用 `MockFrame.vue` 占位，后续对接按需截取接口。

## 截图

`docs/screenshots/` 内含主界面（真实 OSM 底图 + Map 镜头）、覆盖时间轴、添加目标要素（真实识别结果）、特征详情、空任务态。
