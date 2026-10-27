# M2c 独立真实 GIS 地图：只读 Map PostgreSQL

基于 TrajNexa_Map 现有 OSM、SinoBF-1、人工 GIS 编辑和台账数据，主系统新增 Leaflet 独立真实 GIS 地图，保留旧业务示意图及 M2b 原始空间预览。

## 原始表与覆盖语义

- 道路基座 public.cameras_osm_road、建筑基座 public.cameras_building 原封不动。
- 手工修订表 public.cameras_map_feature 保存增删改。源对象只要存在覆盖/删除墓碑，即不再显示旧源几何，显示有效人工编辑版本（跨 bbox 的墓碑同样有效）。
- 建筑来源按 Map 原有规则区分 osm、sinobf1、promoted，不将 SinoBF 与 OSM 原始记录混为一体。
- 摄像头由 cameras_camera 通过 cameras_registry.map_camera_id 只读关联稳定 UUID；未关联台账 UUID 留空。
- bigint 摄像头 Map ID 输出字符串。camera.angle 对应 tilt_deg 俯角，不是 horizontal_fov_deg。
- FOV 来自 Map 模型的识别/辨识/观察级几何，不可视为实测覆盖。

## 接口与保护

GET /api/v1/gis/viewport?bbox=west,south,east,north

限制单次视野经度跨度和纬度跨度分别不超过 0.18 度。最多 400 条道路、400 栋建筑、500 个镜头、1500 条 FOV，返回 JSON 最大 8 MiB。超出就提示缩小视野；不静默截断，也不会用模拟数据补位。

全部只读查询处于同一个 REPEATABLE READ / READ ONLY 事务；错误码不泄漏 PostgreSQL 密码。

## Map 数据库只读账户需补充权限

~~~sql
GRANT SELECT (osm_id,geom,highway,name) ON TABLE public.cameras_osm_road TO trajnexa_reader;
GRANT SELECT (id,osm_id,geom) ON TABLE public.cameras_building TO trajnexa_reader;
GRANT SELECT (id,promoted_building_id) ON TABLE public.cameras_building_source TO trajnexa_reader;
GRANT SELECT (id,source_key,source_ref,kind,is_deleted,name,geom) ON TABLE public.cameras_map_feature TO trajnexa_reader;
~~~

继续使用 M2a/M2b 已授予的 registry、camera 与 camerafocus 权限。主系统绝不能采用 Map 的管理员账号，也不需要删除数据或迁移数据库。

## 部署与验收

主系统本地 .env 配置 MAP_DB_READONLY_URL，Map Docker 网络连接沿用 compose.map-db.yaml。

~~~powershell
git pull
docker compose -f compose.yaml -f compose.ocr.yaml -f compose.map-db.yaml up -d --build
Invoke-RestMethod 'http://localhost:8000/api/v1/gis/viewport?bbox=110.20,25.24,110.22,25.27'
~~~

打开 localhost:5173，切换「真实 GIS 地图」。平移缩放时按视野请求镜头、道路、建筑和模型 FOV。默认不请求外部地图瓦片；勾选在线 OSM 栅格后，浏览器才会访问 OSM 瓦片服务器。

入口旁仍可切换业务示意与原始空间预览，真实 GIS 不反向改写现有任务、OCR 报告、人工绑定或地图编辑成果。

## 质量验证

新增注入式只读事务、人工修订/墓碑查询、超限、坏几何、bbox、API 错误测试。目前未在用户实际 Docker、PostGIS、npm 网络和浏览器执行完整验收。需运行：

~~~powershell
docker compose -f compose.yaml -f compose.ocr.yaml -f compose.map-db.yaml run --rm --no-deps api sh -lc "pip install -r requirements-dev.txt && python -m pytest -q tests/test_map_db_gis.py tests/test_gis_viewport_api.py"
# 全套测试（可在上述容器中将末尾 pytest 参数改为 -q）
docker compose exec -T frontend npm run build
~~~

局限：目前大量道路和建筑场景会按上限拒绝；后续可使用 PostGIS MVT 按瓦片发布并加入空间范围过滤。仅绘制镜头安装点位于视野内的 FOV，不能覆盖所有跨视野的远距离 FOV。


## 旧版地图摄像头名称

M2c 在地图记录尚未关联 CameraRegistry 时，从 cameras_cameratags 中读取 name 标签作为显示名称。保留 camera_uuid=null，不能因此自动认为台账已绑定。必须为只读账号补充：

~~~sql
GRANT SELECT (camera_id_id, name, value) ON TABLE public.cameras_cameratags TO trajnexa_reader;
~~~

真实 GIS 视图对 OSM 矢量路网始终署名 © OpenStreetMap 贡献者（ODbL）；在线 OSM 瓦片依旧需要明确勾选才会请求外部服务器。


M2c 额外提供 GET /api/v1/gis/anchor，只读获取 Map 数据库首个有点位的摄像头坐标作为首次视野和「定位 Map 镜头」操作；无设备则 center=null，前端不生成假设备。这个接口只使用现有 cameras_camera 的 location 和 id 字段权限。


### 尚未覆盖的 Map 图层

M2c 初版只绘制融合建筑基座 cameras_building（包括已提升的 SinoBF-1 建筑）和 MapFeature 有效人工建筑修订；**未提升、仅暂存在 cameras_building_source 的 SinoBF-1 源轮廓没有作为独立图层发布**。OSM context 中的铁路、水系、围墙、出入口、功能区及行政边界也暂未集成；这些源数据不会因此被删除或覆盖。下一轮可以继续增加按需矢量瓦片/图层，而不是让一次 bbox 请求加载全量。
