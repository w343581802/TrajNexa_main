# M2b：TrajNexa_main 只读摄像头空间数据（DB 直连）

> 基于当前 TrajNexa_Map 的 models.py 和 CameraRegistry。Demo，尚未完成用户 Docker Desktop 实际数据库验收。

## 真实字段约定

- 地图摄像头 `public.cameras_camera.id` 是 **bigint**，只用作 GIS 图元关联。
- 台账 `public.cameras_registry.id` 才是稳定 `camera_uuid`，通过 `map_camera_id` 左连接；未建台账保持 `camera_uuid=null`。
- `ST_X / ST_Y(location)` 为 WGS84 `lon/lat`；`direction` 对应 `bearing_deg`；`angle` **是俯角** `tilt_deg`，不冒充水平视场角。
- `horizontal_fov_deg` 若台账存在才读取；缺失就 `null`。
- `cameras_camerafocus` 按 `scenario='mean'` 取得 `identification / recognition / observation` 三种 geometry（GeoJSON），保留洞、`with_intersection`；它们是 **模型生成视域**，不是实测真实覆盖。
- `pose_quality='calibrated'` 仅在台账标明 calibrated、GIS 朝向俯角齐全且存在正式关联时保留；其余不擅自提升质量等级。即便校准，其 FOV 仍是模型几何。
- 读表查询都在单个 REPEATABLE READ + READ ONLY 事务中；不迁移、更新或删除 Map 数据，不访问人脸或录像。

## Map PostgreSQL 权限

沿用 M2a 只读账号 `trajnexa_reader`。在 TrajNexa_Map 所属数据库以有管理权限账号运行：

```sql
GRANT SELECT (id, location, direction, angle, camera_type, effective_range_m)
ON TABLE public.cameras_camera TO trajnexa_reader;
GRANT SELECT (id, name, map_camera_id, deleted_at, pose_quality, position_quality, enabled, horizontal_fov_deg)
ON TABLE public.cameras_registry TO trajnexa_reader;
GRANT SELECT (camera_id_id, scenario, level, with_intersection, geom)
ON TABLE public.cameras_camerafocus TO trajnexa_reader;
-- has_video：只判断该镜头是否导入过录像参考帧，不读取图片内容
GRANT SELECT (camera_id, source_type, image)
ON TABLE public.cameras_snapshot TO trajnexa_reader;
```

`has_video` 只按 `source_type='video_frame'` 聚合，不返回快照 ID、文件名或图片；缺少这段授权时接口会降级为全部 `has_video=false`，地图也不会因可选字段 503。

必须保留 M2a 所授予的台账其它列的 SELECT 权限。不要授予写权限，不要使用 Map 数据库管理员账号作为主系统连接。

## 主系统启动与接口

`TrajNexa_main/.env` 设置 `MAP_DB_READONLY_URL`，连接到地图 Docker 网络（参见 M2a `compose.map-db.yaml`）。

```powershell
git pull
docker compose -f compose.yaml -f compose.ocr.yaml -f compose.map-db.yaml up -d --build
Invoke-RestMethod http://localhost:8000/api/v1/spatial/cameras
```

返回 `status=available`、`source=map_postgresql_readonly`、`spatial_revision=mapdb-spatial:...`、`counts`、`cameras`。数据库未配置、不可达或数据超限时返回 HTTP 503 与**不包含数据库密码**的错误码；**不自动回退到虚假 GIS 数据**。

`cameras[]` 在原有字段上增加 `has_video`，`counts` 增加 `with_video`。它只表示该 Map 镜头的台账下存在 `source_type='video_frame'` 的参考帧，用来在前端区分灰色/点亮；**不表示录像可回放、覆盖完整或实时在线**，也不改变 FOV 的模型几何声明。

## 范围和后续

本次首次加载最多 3000 台 GIS 摄像头及 9000 条 FOV，超额直接拒绝（不截断）；总视域 GeoJSON 超过 8 MiB 也拒绝。可选的 `has_video` 行数超限时截断为前 3000 条并继续返回地图，避免一个可选字段拖垮整页。后续应增加 bbox 或 GIS tiles 按需加载。主系统前端的演示工作台目前仍使用 SVG 示意坐标，因此另外提供**真实空间数据独立预览**，不会把地图经纬度当作示意坐标，也不宣称已经有道路/建筑 GIS 底图。

M2a 名称目录与 M2b 空间视域使用相同只读连接，但**目录修订号与空间几何修订号相互独立**。已有人工确认绑定、OCR 报告、Map 台账均不改变。

测试：`cd backend && python -m pytest -q tests/test_map_db_spatial.py tests/test_spatial_api.py`。测试模拟 PG 连接，不等于跨 Docker 实际联调。


## 数字 ID 精度

Map 的 bigint 摄像头 ID 在 API JSON 中以 **string** 输出（例如 `"9000000000000003"`），避免 JavaScript Number 精度丢失。数据库查询仍使用 bigint，`camera_uuid` 仍保持单独的 UUID 字段。

## 本地实际环境验收（2026-10-27）

真实 PostgreSQL + Docker + 浏览器联调结果：

| 检查项 | 结果 |
| --- | --- |
| 只读账号 `trajnexa_reader` | 按本文与 M2a 的列级授权创建；`UPDATE` 被 `read_only` 事务拒绝 |
| 主系统 `GET /api/v1/spatial/cameras` | `status=available`、`source=map_postgresql_readonly`、`spatial_revision=mapdb-spatial:09379c788b6902d4` |
| counts | 55 个地图镜头 / 55 可显示 / 台账关联 2 / 未关联 53 / 缺坐标 0 / 有 FOV 7 |
| 台账 UUID 对应 | `9000000000000023 → 93b78618-…`、`9000000000000024 → cef44224-…`，与 `cameras_registry` 完全一致 |
| FOV 几何 | 21 条（7 镜头 × 3 等级）逐条与 `cameras_camerafocus` 的顶点数、环数、外接框一致；顶点起点在镜头位置（0–8 m 偏移） |
| 跨系统一致性 | Map `/api/v1/spatial/snapshot/` 与本接口 ID 集合、`with_focus=7`、`with_pose=2` 相同 |
| 前端 | `查看 Map 真实空间数据` 预览渲染 55 点 + 21 个 FOV 面，无控制台错误，1280×800 无横向溢出 |

发现的 Map 侧数据问题（不影响主系统只读接入，建议在 Map 台账修正）：

1. `9000000000000024`：`cameras_camera.angle=23°` 与 `cameras_registry.tilt_deg=15°` 不一致；当前接口按本文约定取 `angle`。
2. 同一镜头的 `recognition` 与 `observation` 两级 FOV 几何完全相同，`observation` 未比 `recognition` 更大，模型输出疑似退化。
3. Map 自身 snapshot 的 `camera_id` 仍以 JSON 数字输出（16 位 bigint），JS 侧存在精度风险；主系统已改用字符串。

测试：`tests/test_spatial_api.py` 原先 patch 的是 `app.map_db_spatial.read_map_spatial`，而路由在 `app.main` 里已按值导入，导致「成功路径」用例实际打到真实函数并 503。已改为 patch `app.main.read_map_spatial`，四个文件 25 项全部通过。

## 几何与投影复核（可复跑，2026-10-28）

单元测试只证明适配器按约定解析，不证明「真实库里的数对得上、画到屏幕上也对得上」。为此补了一个可复跑的只读核对脚本：

```bash
docker run --rm --network trajnexa_map_default \
  -e MAP_DB_READONLY_URL="postgresql://trajnexa_reader:...@postgis:5432/trajnexa_map" \
  -v "$PWD/backend/app:/srv/app" -v "$PWD/backend/tools:/srv/tools" -w /srv \
  trajnexa_main-api python tools/verify_map_spatial.py
```

它把 `read_map_spatial()` 的返回与 `cameras_camera` / `cameras_registry` / `cameras_camerafocus(scenario='mean')` 逐条比对，任何一项不符退出码 1（可挂 CI）。当前结果：

| 检查 | 结果 |
| --- | --- |
| 状态与集合 | `available`；55 个点位与 `cameras_camera` 完全一致 |
| 坐标 | 逐条 ≤1e-6°（等价 0.1 m，差异仅来自 6 位小数的取整） |
| 朝向 / 俯角 / 量程 | 全部等于 `direction` / `angle` / `effective_range_m` |
| UUID | 2 个关联全部等于 `cameras_registry.id`，无冲突、无凭空生成 |
| FOV 集合 | 21 个面与 `cameras_camerafocus(mean)` 一一对应 |
| FOV 几何 | 顶点坐标（6 位小数）与外接框完全一致；面积与 `ST_Area(geography)` 相符 |
| FOV 起点 | 每个面离镜头 ≤8 m，顶点落在镜头处 |
| 汇总 | 55 点 / 2 关联 / 21 面 / 0 项失败 |

渲染层另做了像素级核对（仅开发构建暴露 `window.__gisMap`）：把地图定位到 `9000000000000023` 的 18 级，读取 FOV 专用 canvas 在「开/关 FOV」前后的像素差，与同一视野内 6 个可见 FOV 面的投影多边形比对——

| 检查 | 结果 |
| --- | --- |
| 绘制区域 vs 投影外接框 | 偏差 ≤1 px（`[-1,-1,0,0]`） |
| 覆盖面积 | 9366 px / 11775 px ≈ 0.80（差值来自描边与重叠填充只计一次） |
| 楔形顶点到镜头像素 | 0 px |

结论范围要说清楚：

- **已验证**：点位、UUID、FOV 几何与台账一致；几何经 Leaflet WGS84 投影后落在应有像素上；跨系统（主系统接口 / Map 快照 / 数据库）三方 ID 集合一致。
- **未验证**：FOV 是 Map 的模型几何，**不是实测覆盖**——没有遮挡计算（`occlusion_applied` 全为 false），也没有与真实画面、录像可用性、绝对朝向精度做过比对；55 个点位里只有 2 个经过台账标定姿态，其余 5 个带 FOV 的镜头位姿未知，界面按 `modelled_unverified` 展示。
- **台账缺陷（需 Map 侧修）**：`9000000000000024` 的 `recognition` 与 `observation` 几何完全相同；`cameras_camera.angle`（23°）与 `cameras_registry.tilt_deg`（15°）冲突，`tilt_deg` 未授予只读角色，脚本只能 SKIP 该比对。
