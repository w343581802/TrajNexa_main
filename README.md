# TrajNexa Main — M0 + M1a/M1b/M1c/M1d Demo

**注意：此文件夹是全新 TrajNexa 主系统的独立预览，绝不是 TrajNexa_Map。**

M0 实现“上传 → 素材持久化 → 排队 → 独立 Worker → 报告 → 前端查看”；M1a 提供基础媒体信息/画质指标，M1b 提供可选角标 OCR，M1c 提供只读摄像头目录候选与手动绑定记录；M1d 可登记人工核实的静态参考画面，并在绑定后比较背景几何。**不执行身份识别或跨镜头追踪。**Map 仅在未来通过独立只读 API 接入。请不要把此项目代码覆盖到原先的地图项目源码上。

## 环境

- Docker Desktop + Docker Compose（建议；目前未在此环境验证容器启动）
- 首版不用 GPU，依赖 PostgreSQL/PostGIS、FastAPI、Vue 3 + Leaflet。
- 演示仅绑定回环地址，不提供用户认证，请勿暴露到公网。

## Docker 启动（计划验证流程）

```bash
cp .env.example .env
mkdir -p storage
docker compose up --build -d
```

- 前端：http://localhost:5173
- API 健康检查：http://localhost:8000/api/v1/health
- FastAPI 文档：http://localhost:8000/docs

请勿对存有用户数据的卷执行 `docker compose down -v`。

## 无 Docker 的后端开发/测试

```bash
cd backend
python -m venv .venv
# 激活虚拟环境，再安装依赖
pip install -r requirements-dev.txt
# 设置 DATABASE_URL=sqlite:///./trajnexa_dev.sqlite3（可用于快速单实例演示）
alembic upgrade head
uvicorn app.main:app --reload
# 新终端（backend 目录）
python -m app.worker
# 测试
pytest -q
```

M0 的 SQLAlchemy 模型兼容 SQLite，用于开发与单元测试；Docker 默认 PostGIS。没有 Redis/Celery 需求。独立 Worker 是单进程设计，启动时将残留运行中任务重置排队。后续需要多 Worker 时必须完善租约、超时和并发声明，不能直接横向扩容。

前端本地启动：

```bash
cd frontend
npm install
npm run dev
```

## 已实现的 HTTP 接口

- `GET /api/v1/health`
- `POST /api/v1/media`：`multipart/form-data` 中单个 `file`；文件名去路径；不信任客户端 MIME；按文件头初筛。**签名有效并不等于文件可正确解码**，M1 将进一步探测。
- `GET /api/v1/media` / `GET /api/v1/media/{media_id}`
- `POST /api/v1/media/{media_id}/inspect`：产生持久化排队作业。
- `GET /api/v1/jobs/{job_id}` / `POST /api/v1/jobs/{job_id}/retry`
- `GET /api/v1/jobs/{job_id}/report` / `GET /api/v1/inspections/{inspection_id}`

M0 初始阶段不提供 OCR/质量检测或真实媒体播放；M1a–M1d 已分别增量实现对应能力。未知字段为 `not_checked`，不会输出虚假时间、位置或识别结果。

## 与地图项目的关系

`TrajNexa_Map` 只是空间数据生产、编辑、校准与发布系统，主系统不得直接修改它的数据库或把它的地图编辑器作为自身业务地图。

完整设计参见 `design/01_Demo_M0-M1_初步技术规划_V0.1.md`。后续 M1 再接 FFprobe、PaddleOCR 与 OpenCV。

## M1a 基础预检增量

当前在 M0 基础上已增加 `backend/app/inspector.py`：图片使用 OpenCV 解码，记录实际尺寸、像素中位亮度、Laplacian 方差；视频使用 FFprobe 读取容器与视频流参数。损坏文件会以 `decode_status=failed` 保存报告。`ffprobe` 元数据成功**不等于逐帧解码成功**。

**该阶段尚未实现，后续模块已补充**：角标 OCR（M1b）、摄像头候选与人工绑定（M1c）、固定场景参考图校验（M1d）；**仍未实现**全片逐帧质量检查。模糊和亮度指标仅是粗略候选告警，不能作为真实事实恢复或身份识别依据。以上任务将继续独立验收。新的 Docker 后端包含 FFmpeg/FFprobe 和 `opencv-python-headless`。

## M1b / M1c 角标 OCR 与设备目录匹配

- OCR 是可选功能，默认 `.env` 内 `OCR_ENABLED=false`；需要 OCR 时使用 `docker compose -f compose.yaml -f compose.ocr.yaml up --build -d`，仍需使用真实样本验证。
- **首次测试摄像头目录**：`cp examples/camera_catalog.demo.json storage/camera_catalog.json`（Windows 可复制该文件到 `storage` 文件夹）。此样例完全虚构，不是实际 Map 导出。
- `GET /api/v1/cameras/catalog` 检查目录加载状态。M1c 只做精确别名候选匹配，不确认摄像头。
- 人工绑定接口：`POST /api/v1/inspections/{id}/camera-bindings`；操作会追加到独立历史表，撤销/更正不会改写 OCR 预检报告。
- **升级已有开发数据库**：`docker compose run --rm migrate`（如果已启动并运行 compose，则直接执行 `docker compose up --build -d` 触发 migrate）；不要清除现有数据库卷。
- 可复现自动化测试：`cd backend && python -m pytest -q`，以及本地 SQLite 上 `alembic upgrade head`。
- 真实 Map 的稳定 `camera_uuid` 发布接口尚未接通，设备快照只是适配器；M1d 已实现独立参考场景比较，但尚未进行实景精度和绝对地理朝向验收。
- M1c 设计/验证见 `design/04_M1c_摄像头目录候选匹配与人工绑定.md`。

## M1d 人工参考图与固定场景视角候选校验

1. 先从 `媒体素材` 上传一张人工核实的参考截图，以及待核查的图片或录像；给待核查素材执行预检。
2. 在预检报告中使用 M1c **人工确认**摄像头（不是 OCR 自动确认）；M1d 区域中选取参考图片并填写来源说明。
3. 选择登记的参考图，点击 `比较当前素材与参考图`；会生成独立、不可覆盖的场景校验历史。
4. `reference_view_consistent_candidate` 仅表示静态背景几何对应；`possible_view_change` 仅提示疑似画面变化；`indeterminate` 表示证据不足。**这三项都不能直接证明地图上的地理朝向角度对或错。**
5. M1d 依赖新增数据库迁移 `0003_m1d_view_reference.py`：正常执行 `alembic upgrade head` / `docker compose up --build -d` 即可，不能删除现有业务数据卷。
6. 后端合成测试：`cd backend && python -m pytest -q`（本轮 **26 passed**）。SQLite 从空库迁移验证通过；由于当前环境无法连接 npm registry，前端 build 与 Docker Desktop 整体验收仍待执行。
7. 实施/限制说明见 `design/05_M1d_固定场景参考图与视角一致性校验.md`；真实 TrajNexa_Map GIS 朝向/FOV 数据发布接口仍未接入。
