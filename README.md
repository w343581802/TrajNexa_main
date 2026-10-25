# TrajNexa Main — M0 standalone demo preview

**注意：此文件夹是全新 TrajNexa 主系统的独立预览，绝不是 TrajNexa_Map。**

M0 只实现“上传 → 素材持久化 → 排队 → 独立 Worker → 模拟报告 → 前端查看”，不执行 OCR、AI、身份识别或跨镜头追踪。Map 仅在未来通过独立只读 API 接入。请不要把此项目代码覆盖到原先的地图项目源码上。

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
python -m pytest -q
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

M0 不实现 M1 OCR/质量检测、不提供真实媒体播放、不提供预检人工修正接口。模拟报告中未知字段为 `not_checked`，不会输出虚假时间、位置或识别结果。

## 与地图项目的关系

`TrajNexa_Map` 只是空间数据生产、编辑、校准与发布系统，主系统不得直接修改它的数据库或把它的地图编辑器作为自身业务地图。

完整设计参见 `design/01_Demo_M0-M1_初步技术规划_V0.1.md`。后续 M1 再接 FFprobe、PaddleOCR 与 OpenCV。

## M1a 基础预检增量

当前在 M0 基础上已增加 `backend/app/inspector.py`：图片使用 OpenCV 解码，记录实际尺寸、像素中位亮度、Laplacian 方差；视频使用 FFprobe 读取容器与视频流参数。损坏文件会以 `decode_status=failed` 保存报告。`ffprobe` 元数据成功**不等于逐帧解码成功**。

**尚未实现**：角标时间 OCR、摄像头名称 OCR、设备绑定、视角/参考图校验、视频逐帧质量检查。模糊和亮度指标仅是粗略候选告警，不能作为真实事实恢复或身份识别依据。以上任务将继续独立验收。新的 Docker 后端包含 FFmpeg/FFprobe 和 `opencv-python-headless`。


## M1b 角标 OCR（可选）

M1b 已加入时间角标 OCR、带标签的摄像头名称候选提取及视频三帧时间一致性检查，默认 `OCR_ENABLED=false`，不影响 M0/M1a。实际启用：将 `.env` 中 `OCR_ENABLED=true`，使用 `docker compose -f compose.yaml -f compose.ocr.yaml up --build -d`。CPU 镜像首次需要安装 PaddleOCR / PaddlePaddle 并下载模型；此容器部署及真实角标识别效果尚未验证。参见 `design/03_M1b_角标OCR与多帧一致性_实施验证.md`。
