# M2a：主系统直连 TrajNexa_Map 的 PostgreSQL（可选只读模式）

当前 Map 仓库已经有 cameras_registry，含稳定 id UUID、name、enabled、deleted_at 和 map_camera_id。本阶段仅从此表读取摄像头名称目录，不写 Map 数据库，不直接读取 FOV、照片、建筑或道路。

## 在 Map 数据库授予最小 SELECT 权限

进入 TrajNexa_Map 项目，在 PowerShell 执行以下命令打开 psql：

    docker compose exec postgis sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

在 psql 输入（数据库名按本机实际环境调整）：

    CREATE ROLE trajnexa_reader LOGIN;
    GRANT CONNECT ON DATABASE trajnexa_map TO trajnexa_reader;
    GRANT USAGE ON SCHEMA public TO trajnexa_reader;
    GRANT SELECT (id,name,enabled,map_camera_id,index_code,external_index_code,deleted_at) ON TABLE public.cameras_registry TO trajnexa_reader;
    ALTER ROLE trajnexa_reader SET default_transaction_read_only = on;

然后通过交互式 psql 的 \password trajnexa_reader 为该账号设置密码，切勿把明文密码提交 GitHub 或复用 Map 管理员账号。

## 主系统配置

先通过 docker network ls 查看 Map Compose 网络，默认是 trajnexa_map_default。如实际不同，修改 compose.map-db.yaml 中 external 网络名称。主系统目录 .env 设：

    MAP_DB_READONLY_URL=postgresql://trajnexa_reader:<URL编码后的密码>@postgis:5432/trajnexa_map

在 TrajNexa_main 下启动：

    docker compose -f compose.yaml -f compose.ocr.yaml -f compose.map-db.yaml up -d --build

再访问 http://localhost:8000/api/v1/cameras/catalog；期望 status=available、source=map_postgresql_readonly、diagnostics.live_read=true。目录返回的 spatial_revision=mapdb-directory:<digest> 仅是目录版本标记，不是地理空间修订号。

## 保留人工别名

Map 暂无独立的中文摄像头名称历史别名表。可以使用不进 Git 的 storage/map_camera_aliases.json，在 aliases_by_uuid 内按稳定 UUID 管理人工核实的旧名称。不会修改 Map 台账正式名称。示例：

    {"schema_version":"1.0","aliases_by_uuid":{"11111111-1111-4111-8111-111111111111":["示例旧名称"]}}

## 安全与回退

- 没有设置 MAP_DB_READONLY_URL 时使用原来的 storage/camera_catalog.json。本项目不自动删除或覆写该文件。
- 设置后每次实时只读查询 Map；连接失败明确报告 map_db_query_unavailable，不会静默使用可能过期的目录。
- deleted_at 已软删除设备不返回；enabled=false 则 active=false。无名称记录在诊断中计数，不创建虚构姓名。
- 一次超过 3000 条会明确报 map_db_catalog_exceeds_3000，绝不会忽略超额部分；规模增长后需分页或改为查询 API。
- 数据库表字段如升级发生变化，需调整该适配器。生产长期应优先选择版本化只读视图或 Map 既有只读 API。
- 本 Demo 无公开认证：只能在本机或可信网络使用，严禁把 PostgreSQL 或系统 Web/API 暴露到公网。

## 测试与限制

新增模拟 PG 连接与切换模式测试；本轮不能访问你的 Docker Desktop / Map 生产数据库，所以不能声称真实跨容器联调已完成。重新发起预检才会根据当时 Map 的目录信息产生新候选，旧报告与人工绑定记录不改写。