# M2c：主系统独立 GIS 地图（Map 真实道路与建筑）

> 在 M2b 只读空间数据之上，给主系统一个真实 GIS 视图：道路、建筑、摄像头点位与模型 FOV 都来自 TrajNexa_Map，
> 不再使用示意坐标。业务工作台的示意地图保持不变，两者可切换。

## 数据来源与边界

| 图层 | 表 | 说明 |
| --- | --- | --- |
| 道路 | `public.cameras_osm_road` | OSM 线要素，按 `highway` 分类配色；247,480 条 |
| 建筑 | `public.cameras_building` | OSM 面要素；109,661 栋，缩放 ≥ 15 才加载 |
| 摄像头 | `public.cameras_camera` + `cameras_registry` | 复用 M2b 接口，含台账 UUID |
| FOV | `public.cameras_camerafocus` | `scenario='mean'` 三级模型几何 |

全部走 M2a/M2b 的只读账号 `trajnexa_reader`，单个 `REPEATABLE READ + READ ONLY` 事务，禁止写、禁止迁移。
OSM 数据必须显示 `© OpenStreetMap contributors (ODbL)` 署名，前端固定在右下角。

## 接口

```http
GET /api/v1/spatial/basemap?bbox=west,south,east,north&zoom=16
```

- `bbox` 为 WGS84 `EPSG:4326`；跨度上限 0.5°，下限 0.0004°（过小说明客户端还没定位好）。
- `zoom` 取 10–19，决定 `ST_SimplifyPreserveTopology` 的容差（19 级 ≈ 0.000015° ≈ 1.6 m，10 级 ≈ 0.0012°）。
- 返回 `status=available`、`source=map_postgresql_readonly`、`basemap_revision=mapdb-basemap:<sha256 前 16 位>`、
  `counts.{roads,buildings,buildings_skipped_below_zoom}`、`attribution` 与 `roads[] / buildings[]`（GeoJSON）。
- **超额拒绝而非截断**：道路超 6000 条、建筑超 6000 栋、单要素点数超 2 万、响应超 8 MiB 都返回 503 与错误码，
  由前端提示放大；数据库未配置或不可达同样 503，**不返回任何替代几何**。
- `camera` 的 bigint `osm_id` / `id` 一律以字符串输出，避免 JavaScript 精度问题。

## 前端

新增 `frontend/src/components/MapGisView.vue`（Leaflet 1.9，无新增依赖）：

- 地图面板右上角三态切换：`示意地图 / 空间数据 / Map GIS`（`App.vue` 的 `mapMode`）。
- 底图：无栅格瓦片，直接绘制 Map 台账的矢量道路与建筑；道路按等级配色加粗，建筑限 15 级起显示。
- 覆盖层：摄像头点（蓝＝台账已关联、橙＝仅 GIS 点位）、朝向短线、可选名称；FOV 三级面（识别/辨识/观察各一色，
  `occlusion_applied=false` 时虚线），单独 canvas 图层保证压在道路之上。
- 视口变化 250 ms 防抖后按 bbox 拉取，AbortController 丢弃过期响应；13 级以下提示放大，15 级以下提示建筑不加载。
- 加载失败只显示错误码横幅，保留上一次图层并明确“不显示任何替代几何”。
- 结论性文案常驻：FOV 是模型几何，不构成真实覆盖证明，也不构成身份结论。

## 验收（2026-10-27，本机真实环境）

| 项目 | 结果 |
| --- | --- |
| 缩放 16（金水路） | 道路 238 · 建筑 686，`mapdb-basemap:56257390e8dfb62d` |
| 缩放 18（近景） | 道路 15 · 建筑 29，两级校准镜头的三级 FOV 面与朝向线可见 |
| 缩放 13 | 道路 2679 · 建筑不加载，提示“放大到 15 级后出现” |
| 图层开关 | 道路/建筑/摄像头/FOV/名称 均即时生效 |
| 控制台 | 无报错；1280×800 无横向溢出 |
| 只读性 | `trajnexa_reader` 写操作被 `read_only` 事务拒绝 |

## 仍未做

- 没有栅格底图与地名注记；Y 轴以上（城市级）没有分级简化缓存，靠 `zoom` 容差与 bbox 上限控制体量。
- 没有做瓦片缓存，同视口重复进入会重新查询（数据库侧 6 s 超时保护）。
- 轨迹、案件要素仍画在示意地图上，尚未叠加到 GIS 视图；M2d 再做业务图层与 GIS 的对齐。
