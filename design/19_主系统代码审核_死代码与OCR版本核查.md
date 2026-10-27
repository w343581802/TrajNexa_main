# 主系统代码审核：死代码清理与 OCR 版本核查（2026-10-28）

> 范围：`TrajNexa_main` 前端工作台 + FastAPI 后端 + OCR worker。
> 结论先说：删除了 1 个无引用组件、9 个无引用导出符号与 4 处后端未用导入；「添加要素」的 OCR 链路确实跑在 **PP-OCRv6** 上，
> 但此前只是“靠默认值碰巧对了”，本次已改成显式钉扎，并把引擎版本写进报告与界面。

## 1. 审核方法

| 检查项 | 方法 | 结果 |
| --- | --- | --- |
| 前端文件是否被引用 | 逐个 `.vue`/`.ts` 反查引用计数（排除自身） | 1 个组件 0 引用 |
| 前端导出符号 | 解析 `export const/function/type` 后在其余文件中计数 | 9 个导出无人使用 |
| 后端模块级定义 | AST 抽取定义 + 全仓引用计数 | 126 个定义，仅 20 个“0 外部引用”，全为 FastAPI 路由函数（装饰器注册） |
| 后端未用导入 | `pyflakes` | 4 处 |
| 路由 ↔ 前端调用 | 路由表 vs `mainApi.ts` / 组件内 `fetch` | 8 个路由当前 UI 未调用（见 §3） |
| 残留标记 | 搜 `TODO/FIXME/deprecated/待删除`、旧接口名 | 0 |

## 2. 已删除的死代码

| 文件 | 符号 | 判定 |
| --- | --- | --- |
| `frontend/src/components/ui/ProgressBar.vue` | 整个组件 | 全仓 0 引用 |
| `frontend/src/store/workbench.ts` | `visibleFeatures` | 0 引用（特征栏直接用 `featureList`） |
| 同上 | `selectedElement` / `selectedAnchor` / `selectedJob` | 0 引用（选中态走 `wb.selected*Id`） |
| 同上 | `canStartRound` | 0 引用（按钮用 `startConditions` 自行判断） |
| `frontend/src/data/mock.ts` | `mapLabels` + `MapLabel` | 0 引用（示意地图标签已由 SVG 内建） |
| 同上 | `intersection()` | 0 引用（时间轴交集用 `overlap()`） |
| `frontend/src/services/mapSnapshot.ts` | `cachedSnapshot()` | 0 引用（`loadMapSnapshot` 内部仍用模块级 `cache`） |
| `backend/app/main.py` | `from pathlib import Path` | pyflakes 未使用 |
| `backend/tools/verify_map_spatial.py` | `LEVELS` | pyflakes 未使用 |
| `backend/scripts/benchmark_camera_name_roi.py`、`camera_osd_name_quality.py` | `defaultdict` | pyflakes 未使用 |

清理后：后端 119 个用例全通过，前端 `vue-tsc --noEmit && vite build` 通过，`v3test` 10/10 PASS。

## 3. 保留了但“当前没被调用”的东西（有意保留）

- **后端 8 个 UI 尚未接的接口**：`GET /api/v1/media/{id}`、`POST /api/v1/jobs/{id}/retry`、
  `GET/POST /api/v1/inspections/{id}/camera-bindings`、`GET/POST /api/v1/inspections/{id}/view-checks`、
  `GET/POST /api/v1/cameras/{uuid}/view-references`。它们是 M1c/M1d 的服务端能力，有独立测试与设计文档
  （`design/04`、`design/05`），**不按“没人调”删除**；等 UI 接线时直接可用。
- **6 个图标条目**（`trash/eye/pin/filter/crop/more`）：图标表是设计系统超集，删了会让后续改版重新补，保留。
- **5 个对照实验脚本**（`backend/scripts/benchmark_*.py`）：`design/09`、`design/10`、`design/13` 可复跑入口，保留。

## 4. 「添加要素」是否正确接 PP-OCRv6

### 4.1 事实核查（修改前）

| 项目 | 实测 | 说明 |
| --- | --- | --- |
| worker 镜像 | `paddleocr 3.7.0` + `paddlepaddle 3.2.0` | `Dockerfile.ocr` 当时写 `paddleocr>=3.2,<4` |
| 模型缓存 | `PP-OCRv6_medium_det` / `PP-OCRv6_medium_rec` | `/root/.paddlex/official_models` |
| 代码 | `PaddleOCR(...)` **没有传 `ocr_version`** | 走库的默认值；3.7 的默认恰好是 PP-OCRv6，3.2/3.3 的默认是 PP-OCRv5 |
| 结论 | **跑的是 PP-OCRv6，但属于“碰巧”** | 重建镜像时若解析到 3.2~3.6，会静默退回 PP-OCRv5，报告里也看不出来 |

### 4.2 本次修改

1. `backend/app/overlay_ocr.py`
   - 新增常量 `OCR_VERSION = 'PP-OCRv6'`，`_get_ocr_engine()` 显式 `ocr_version=OCR_VERSION`；
   - 新增 `ocr_engine_info()`，把 `library / library_version / ocr_version` 写进报告 `time.ocr_engine`。
2. `backend/Dockerfile.ocr`：`paddleocr>=3.7,<3.8`（3.7 起默认才是 v6），`paddlepaddle==3.2.0` 不变。
3. 前端 `services/mainApi.ts` + `modals/AddElementModal.vue`：「识别依据」新增一行 **识别引擎**，
   显示 `paddleocr 3.7.0 · PP-OCRv6`。
4. 前端补一条兜底：`ocr_engine_status` 为 `disabled/unavailable/error` 时明确提示
   「OCR 未启用/不可用，识别为空不代表画面没有角标」，避免又一次被误读成“画面无摄像头名称”。

### 4.3 实测证据（本机，2026-10-28）

| 环节 | 结果 |
| --- | --- |
| 容器内引擎 | `paddleocr 3.7.0`、`paddle 3.2.0`、`OCR_VERSION=PP-OCRv6` |
| 合成角标图（`2026-10-25 18:42:11` + `camera: CAM-10 SCHOOL WALL`） | 时间 `2026-10-25T18:42:11`（0.9969）；名称 `CAM-1O SCHOOL WALL`（0 误认成字母 O，属 OCR 混淆，系统不自动纠正） |
| 完整 job 链路（上传 → inspect → report） | `succeeded`；`ocr_engine={"library":"paddleocr","library_version":"3.7.0","ocr_version":"PP-OCRv6"}`，`ocr_engine_status=ran` |
| 界面 | 「添加要素」识别后，「识别依据」显示 `识别引擎 paddleocr 3.7.0 · PP-OCRv6`，时间候选 99.7%，名称候选标为「已识别名称，未绑定镜头目录」 |

## 5. 仍然存在的风险（未解决，别当成已验收）

- **真实素材识别率没有验收**：上面的证据来自合成图，不能代表夜间、红外、低分辨率、强压缩监控画面。
  `design/09`、`design/10`、`design/15` 里的对照实验都标注了「尚无真实 PP-OCRv6 对照结果」。
- **字符混淆不纠错**：`0/O`、`1/l` 这类常见混淆只原样保留，报告与界面都不做自动纠正（这是有意的：一律人工确认）。
- **OCR 只在 OCR 镜像里有**：API/默认 worker 镜像不含 PaddlePaddle，`OCR_ENABLED=false` 时报告为 `disabled`；
  现在界面会明确提示，不再显示成“无名称”。
- **worker 镜像 2.2 GB**：Paddle 栈体积大，属预期成本；如需瘦身可换 `PP-OCRv6_small_rec` 或拆独立 OCR 服务。
- **`time.ocr_engine` 之外没有逐模型耗时/内存指标**：本轮没加，需要时再补。
