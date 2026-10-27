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
```

必须保留 M2a 所授予的台账其它列的 SELECT 权限。不要授予写权限，不要使用 Map 数据库管理员账号作为主系统连接。

## 主系统启动与接口

`TrajNexa_main/.env` 设置 `MAP_DB_READONLY_URL`，连接到地图 Docker 网络（参见 M2a `compose.map-db.yaml`）。

```powershell
git pull
docker compose -f compose.yaml -f compose.ocr.yaml -f compose.map-db.yaml up -d --build
Invoke-RestMethod http://localhost:8000/api/v1/spatial/cameras
```

返回 `status=available`、`source=map_postgresql_readonly`、`spatial_revision=mapdb-spatial:...`、`counts`、`cameras`。数据库未配置、不可达或数据超限时返回 HTTP 503 与**不包含数据库密码**的错误码；**不自动回退到虚假 GIS 数据**。

## 范围和后续

本次首次加载最多 3000 台 GIS 摄像头及 9000 条 FOV，超额直接拒绝（不截断）；总视域 GeoJSON 超过 8 MiB 也拒绝。后续应增加 bbox 或 GIS tiles 按需加载。主系统前端的演示工作台目前仍使用 SVG 示意坐标，因此另外提供**真实空间数据独立预览**，不会把地图经纬度当作示意坐标，也不宣称已经有道路/建筑 GIS 底图。

M2a 名称目录与 M2b 空间视域使用相同只读连接，但**目录修订号与空间几何修订号相互独立**。已有人工确认绑定、OCR 报告、Map 台账均不改变。

测试：`cd backend && python -m pytest -q tests/test_map_db_spatial.py tests/test_spatial_api.py`。测试模拟 PG 连接，不等于跨 Docker 实际联调。


## 数字 ID 精度

Map 的 bigint 摄像头 ID 在 API JSON 中以 **string** 输出（例如 `"9000000000000001"`），避免 JavaScript Number 精度丢失。数据库查询仍使用 bigint，`camera_uuid` 仍保持单独的 UUID 字段。
