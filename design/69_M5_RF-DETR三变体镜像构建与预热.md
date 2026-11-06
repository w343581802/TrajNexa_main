# M5 RF-DETR 三变体：权重、镜像构建与启动预热

日期：2026-11-06。对应代码：`backend/app/engines/rfdetr_engine.py`、`backend/app/detectors.py`、`backend/app/main.py`、`backend/Dockerfile.rfdetr`、`backend/Dockerfile.ocr-rfdetr`、`compose.rfdetr.yaml`、`compose.worker-rfdetr.yaml`。

## 1. 结论

RF-DETR Small / Medium / Large 三个变体在本机 Docker 里**已实际可推理**，不是“配置存在但加载失败”：

| 注册 ID | 变体 | 原生输入 | 权重文件 | 体积 |
| --- | --- | --- | --- | --- |
| `rfdetr-s` | small | 512×512 | `backend/models/rfdetr/rf-detr-small.pth` | 368 MB |
| `rfdetr-m` | medium | 576×576 | `backend/models/rfdetr/rf-detr-medium.pth` | 386 MB |
| `rfdetr-l` | large | 704×704 | `backend/models/rfdetr/rf-detr-large-2026.pth` | 130 MB |

`GET /api/v1/ai/image-models` 的 `models` 列表包含 `dfine-n / yolo11n / yolo26s / rfdetr-s / rfdetr-m / rfdetr-l`，`unavailable_models` 为空。

## 2. 权重获取

官方 GCS 直链下载后按上表改名即可，MD5 见 `models/README.md`。要点：

- `rfdetr>=1.11` 的注册表在 `rfdetr/assets/model_weights.py`，Large 对应 `rf-detr-large-2026.pth`（130 MB）；
- 旧的 `https://storage.googleapis.com/rfdetr/rf-detr-large.pth` 是 1.5 GB 的上一代架构，**不能改名冒充**；
- `backend/models/` 被根 `.gitignore` 的 `models/` 忽略，884 MB 权重不入库，靠 `compose.yaml:34` 的 `./backend/models:/srv/models` 挂进容器；
- 加载走绝对路径（`rfdetr_engine.py` 的 `pretrain_weights=str(path.resolve())`），运行期不会联网下载；`rfdetr` 用 `weights_only=True` + `argparse.Namespace` 白名单安全加载。

## 3. 镜像构建

### 3.1 两个必须的构建条件

1. **`backend/.dockerignore` 排除 `models/`**。不加时 build context 达 954 MB（权重全量上传），加后 15 KB。权重是运行时挂载，不进镜像层。
2. **先装 CPU 版 torch 再装 requirements**。`rfdetr` 声明 `torch>=2.2.0`，直接装会拉 CUDA 版加整套 `nvidia-*`/triton（数 GB）。两个 Dockerfile 都在 requirements 前加：

```dockerfile
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch torchvision
```

实测装到 `torch-2.15.0+cpu` / `torchvision-0.29.2+cpu`，约 210 MB。本机 components 层耗时：torch 108s、rfdetr+track 232s。

### 3.2 worker 要同时保留 OCR

`compose.rfdetr.yaml` 只把 api 和 worker 都指到 `Dockerfile.rfdetr`，但录像扫描与命名依赖 `paddleocr`。因此新增 `backend/Dockerfile.ocr-rfdetr`（RF-DETR + paddlepaddle 3.2.0 + paddleocr 3.7）与 `compose.worker-rfdetr.yaml`：

```bash
docker compose -f compose.yaml -f compose.override.yaml \
  -f compose.rfdetr.yaml -f compose.worker-rfdetr.yaml build api worker
docker compose -f compose.yaml -f compose.override.yaml \
  -f compose.rfdetr.yaml -f compose.worker-rfdetr.yaml up -d --no-build api worker
```

**顺序敏感**：`compose.worker-rfdetr.yaml` 必须排在 `compose.rfdetr.yaml` 之后，否则 worker 的 `build.dockerfile` 被覆盖回 `Dockerfile.rfdetr`，OCR 丢失。`docker compose config` 可核对 api→`Dockerfile.rfdetr`、worker→`Dockerfile.ocr-rfdetr`。

paddle 会把 numpy 从 2.5.4 降到 2.3.5、并让 opencv-contrib 4.10 与 opencv-python 5.0 共存；已实测 `import app.overlay_ocr / recordings / worker / detectors` 正常。

## 4. 启动预热

问题：CPU 上把 6 个检测器逐个 load 要一两分钟，而打开“小模型管理”就会请求 `/ai/image-models`，首屏像卡死（实测首个请求 126.55s，之后 0.3s 级）。

做法：`app/detectors.py` 新增 `warmup()`（try 内 `_load_all()`，成功 `logger.info("detector warmup done: ...")`，失败只 warning）；`app/main.py` 用 `@asynccontextmanager lifespan` 在 startup 时起 daemon 线程调用它，环境变量 `TRAJNEXA_DETECTOR_WARMUP=0` 可关。

踩坑：FastAPI 0.162 已删除 `app.add_event_handler`（`AttributeError: 'FastAPI' object has no attribute 'add_event_handler'`，容器直接起不来），只能用 lifespan。

日志：uvicorn 的 root 是 WARNING，`app.*` 的 INFO 会被吞掉；`main.py` 里加 `logging.basicConfig(level=logging.WARNING)` + `logging.getLogger("app").setLevel(logging.INFO)`，重启后可见：

```
INFO:app.detectors:detector warmup done: dfine-n, rfdetr-l, rfdetr-m, rfdetr-s, yolo11n, yolo26s
```

效果：容器起来约 2 分钟后模型全部就绪，`/api/v1/ai/image-models` 从 126s 降到 0.06–0.11s；api 常驻内存约 1.02 GiB（15.47 GiB 可用），worker 空转 228 MiB。

## 5. 真实推理验证

素材取 `/data/videos/29/1-LT-桂康路松山巷-向北_20261023150000至20261023153030` 的第 3 秒帧（2560×1440），三模型各出 2 个候选：

| 模型 | 首次调用 | 后续调用 | 最高候选 |
| --- | --- | --- | --- |
| small | 126.55s（含加载全部 6 个检测器） | — | 自行车 0.849 / 摩托车 0.841 |
| medium | — | 0.33s | 自行车 0.855 / 摩托车 0.854 |
| large | — | 0.48s | 自行车 0.879 / 摩托车 0.845 |

输出框已按原图尺寸归一化还原，说明 COCO 80 类映射、预处理和 letterbox 反变换都对。

## 6. 已知限制

- worker 没有预热：首个真实研判任务会自己加载模型，可能慢一分多钟；
- 三个变体都是 COCO 80 类通用检测，不认“外卖骑手”这类业务类别，结果只能是待核实候选；
- CPU 推理吞吐有限，Large 单帧 0.48s，多路视频同时分析要排队（`_infer_lock` 是全局单并发）。