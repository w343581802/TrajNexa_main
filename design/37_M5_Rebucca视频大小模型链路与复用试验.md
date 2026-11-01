# 37｜M5 Rebucca 视频 → 多小模型 → 多视觉大模型复用链路（实验 V0.1）

> 2026-11-01；**状态：Rebucca 源码调用链已核查；新增 TrajNexa 离线只读探针和契约测试；真实权重、Windows 视频及多模型组合尚未在此处验收。**
>
> 本文对应用户确认的优先级：**先复用 Rebucca 的多模型加载/推理和视频处理能力，之后建设 TrajNexa 的统一模型工作台和可编辑 Prompt 版本系统。** Rebucca 不是 TrajNexa_Map；不得合并 Map 数据库。
>
> 上游版本基线：`yuturuishi/rebucca` main `98b8e4b3adda6ebc01723298b50c8cf73dd2b9aa`；仅用于说明与审计，不代表该提交已做本地模型实测。

## 1. Rebucca 现有调用链（源码确认）

### 1.1 实时 RTSP 流

```text
摄像头 / ZLMediaKit RTSP
  → app/analysis/frames.py FrameSource（OpenCV/FFmpeg 解码）
  → pipeline._decode_loop（帧写入 deque，满时丢旧帧）
  → pipeline.run（分析频率节流，取最新帧）
  → MotionDetector 门控；部分业务区域规则开启强制检测
  → for detector in _detectors:
        EngineFactory / DetectorWorkerPool 获取引擎
        engine.detect(frame_bgr) → {box xyxy原图像素, label, score, ...}
        记录 algorithm_id / algorithm_name
  → 合并本帧各检测器候选（注意：只是追加，并非可靠融合）
  → IoUTracker（仅单镜头临时关联）
  → 命中业务算法/区域/越线等后处理
  → 如果 flow_type=3，裁剪轨迹 box → JPEG
  → LLMUtils.infer(业务规则可编辑的 llm_prompt, JPEG)
  → check_happen（基于逗号关键词子串匹配）
  → 符合规则时才形成 Rebucca 报警
```

源码：
- `app/analysis/frames.py`、`app/analysis/pipeline.py`：解码、频率控制、多检测器、IoUTracker、ROI→VLM。
- `app/analysis/manager.py` / `process_worker.py`：加载摄像头所需的多个小模型。
- `app/analysis/engines/factory.py` / `engines/base.py`：Rebucca `EngineFactory.create(...).load().detect(frame_bgr)`。
- `app/utils/LLMUtils.py`：OpenAI-compatible JPEG data URL 推理，返回文本。
- `app/models.py` / `views/AlgorithmView.py`：业务规则可配置小模型 ID、大模型 ID、`llm_prompt`、`llm_validate`。

### 1.2 上游原生组合能力及局限

| `flow_type` | 上游已有逻辑 | 不能误认为已具备的能力 |
|---|---|---|
| 1 | 小模型 + 后处理 | 不是跨摄像头身份确认 |
| 2 | 运动触发的整帧大模型 + 后处理，区域调用约 8 秒节流 | 不是逐帧全部大模型识别 |
| 3 | 小模型候选 + IoU 单镜头轨迹 + ROI 裁剪 + 大模型按规则复核，轨迹调用约 6 秒节流 | **一条规则一般绑定一个大模型**；不等于多 VLM 并行对比 |
| 4 | 检测 + ReID + 后处理的配置；离线测试服务具备检测→ReID 实验流程 | 不能据此断言实时流水线已完整使用 ReID 向量 |

**已知风险**：`CameraPipeline` 使用小队列和最新帧策略，可能丢中间帧；`object_start` 用 `time.time()`，不是录像已校验 PTS；`IoUTracker` 是贪心关联，不能直接升级为跨镜头 ID；`check_happen` 字符串关键词可能产生误判；部分引擎异常会返回 `[]`，需区别失败与真实空结果。

### 1.3 上游的独立模型测试能力

`app/views/SmallModelView.py` 的 `/smallmodel/openTestStart`、`openTestStatus` 及 `app/services/algorithm_test_service.py` 支持单模型对图片或有限视频执行离线测试，输出渲染图/视频及汇总报告。

**但不宜直接作为 TrajNexa 历史录像证据 API**：视频任务最多处理配置上限（当前源代码：600 帧/60 秒）；`report.detections` 仅保留前 50 条的扁平数据，不包含逐框的已校验帧时间或连续可复核轨迹。因此必须新增或提取独立的原始逐帧结果接口，而不能把“报警记录”当作每个模型输出。

## 2. TrajNexa 拟采用的组合流水线（目标设计，不表示已上线）

```text
目标相关的录像 / 授权摄像头流
       │
       ├── 直播：复用 Rebucca FrameSource / EngineFactory / 推理池
       └── 历史录像：复用 TrajNexa 已有案件录像读取、哈希与抽帧
       │
       ▼
帧封装（来源/案件关联、frame_index、时间候选与准确性状态、原件哈希）
       │
       ▼
模型调度：按任务选择多个“小模型”
       ├── 人员检测：D-FINE / YOLO 人员权重
       ├── 车辆检测：YOLO / D-FINE / 车辆专项权重
       ├── 可选两轮车专项检测
       └── 可选实例分割、ReID（必须单独验证）
       │
       ▼
保留每个模型的原始检测 + 统一像素坐标/类别映射 + 模型元数据
       │
       ▼
去重/冲突标记/短时单镜头 Tracklet（避免抹掉模型分歧）
       │
       ▼
按任务策略、画面质量、遮挡等选少量 ROI / 关键帧
       │
       ▼
从【原始分辨率】裁剪目标，保留少量背景
       │
       ├── 大模型 A（车辆外观 Prompt vN）
       ├── 大模型 B（车辆类别复核 Prompt vM）
       └── 大模型 C（人员外观 Prompt vK）
       │
       ▼
记录各模型的原文 + 结构化候选 + Prompt/参数/模型版本
       │
       ▼
TrajNexa 人工复核 / 时空研判（不自动确认身份或凭空补轨迹）
```

**必须先匹配任务再调用大模型**。车辆识别失败时不要向全部画面无限循环调用 VLM。检测器漏检时应保留人工选框、分块补检或关键帧重检的入口。多模型运行可按 GPU 资源串行，不等于要求多模型同时常驻显存。

**与上游的关键差异**：Rebucca 将大模型结果判定为业务事件关键词是否命中；TrajNexa 需要人/车属性、无法判断项、冲突信息、模型版本和完整媒体出处。保留 Rebucca 的推理能力，但不复用 `check_happen` 作为证据真值判断。

## 3. 第一批实际落地：独立只读离线探针

本分支新增：

- `backend/app/m5_rebucca_contract.py`：转换 Rebucca `DetectionResult` 的原图像素框到 [0,1]，保留模型来源、原标签、显式类别映射及可选分割多边形；**不跨模型去重或自动产生身份**。
- `backend/scripts/rebucca_chain_probe.py`：在已安装 Rebucca 依赖的 Python 环境运行，直接调用其 `EngineFactory.create/load/detect`；离线读取本地图片或视频；可选针对检测 ROI 调用 Rebucca 原有 `LLMUtils.infer`，支持多个 VLM/Prompt；输出一个只读 JSONL 对照记录。
- `backend/tests/test_m5_rebucca_*.py`：无真实模型的接口/坐标/本地 VLM 端点测试。

**这个探针直接复用 Rebucca 引擎和大模型调用工具，不复制 Rebucca 的 Django ORM、报警 API 或视频业务模块。它不接 TrajNexa 正式案件库、也不提供线上跨摄像头服务。**

### 3.1 示例模型配置（仅示意，路径和标签需按本机实际填写）

保存到本地 `m5-probe.json`，不要提交真实图片、密钥或配置文件：

```json
{
  "detectors": [
    {
      "id": "dfine-n",
      "name": "Rebucca D-FINE N",
      "model_file": "static/upload/weight/dfine/dfine_n_coco_opset17_fp32_static.onnx",
      "inference_engine": "onnxruntime",
      "algorithm_type": "dfine",
      "task_type": "detect",
      "device": "cpu",
      "conf_threshold": 0.35,
      "labels_file": "C:/your-model-labels/dfine-coco-80-ordered.labels",
      "class_map": {
        "person": "person",
        "car": "vehicle",
        "motorcycle": "vehicle",
        "bicycle": "vehicle",
        "人": "person",
        "汽车": "vehicle",
        "摩托车": "vehicle",
        "自行车": "vehicle"
      }
    }
  ],
  "vlms": [
    {
      "id": "vehicle-qwen4b",
      "model": "qwen3-vl:4b-instruct-q4_K_M",
      "base_url": "http://127.0.0.1:11434/v1",
      "timeout_seconds": 90,
      "categories": ["vehicle"],
      "detector_ids": ["dfine-n"],
      "prompt": "只描述裁剪画面中可直接观察到的车辆属性，输出JSON。对于无法辨认的颜色、车型、载物不要猜测，标记unknown；不要推断车牌号或目标身份。"
    }
  ]
}
```

**注意：D-FINE 的 `labels` 必须严格匹配所安装权重的类别顺序**。Rebucca 仓库包含模型 ONNX 文件，但这个探针不能从模型文件自动推断中文 80 类的正确标签顺序；需从 Rebucca 模型配置导出准确顺序到 `labels_file`，否则会发生错误分类或空检测。可以在 `detectors` 加入第二个 YOLO 模型配置做组合对照。

### 3.2 Windows 离线运行示例

在**已安装 Rebucca Python requirements 的环境**，从 TrajNexa 项目目录运行（路径按本地调整）：

```powershell
python backend/scripts/rebucca_chain_probe.py `
  --rebucca-root "D:/Rebucca" `
  --media "D:/test-frames/vehicle.mp4" `
  --config "D:/test-frames/m5-probe.json" `
  --output "D:/test-frames/m5-result.jsonl" `
  --sample-fps 1 --max-frames 20
```

在模型检测正常、Ollama 启动后，增加 `--with-vlm --max-vlm-calls 8`，才会将本地裁剪 ROI 发给回环地址的视觉大模型。默认**不调用 VLM**；端点限制为本机 `http://127.0.0.1` / `localhost` / `::1` 的 `/v1` 路径。结果以 JSONL 形式记录原件 SHA256、抽帧序号、未核实时间候选、每模型检测框、VLM 原文及 Prompt SHA256；**不会覆盖视频或写案件表**。

6–8GB GPU 建议先让 D-FINE 走 CPU、Qwen4B 使用 GPU，每次只检测少量帧，再逐渐增加样本和模型数量。

## 4. 第一阶段验收与已知限制

| 阶段 | 检验内容 | 当前状态 |
|---|---|---|
| S0 源码审计 | 确认 Rebucca 输入、EngineFactory、多个检测器与 ROI→LLM 调用链 | 已核对源码 |
| S1 契约测试 | 多模型来源、原像素→归一化、类别映射、坏框、mask、VLM 上限与本地端点 | 有自动化测试（需 CI 验证） |
| S2 真实离线探针 | Windows 本机 D-FINE/YOLO 真权重，真实监控录像，真实 Qwen/其他 VLM | **待手动实测** |
| S3 服务化适配 | Rebucca 运行侧增加逐帧原始观测事件 / 本地只读 API；TrajNexa 接收并绑定案件原件 | 尚未实施 |
| S4 正式模型工作台 | 模型注册、Prompt UI 编辑/版本发布/回滚、同视频评测 | 尚未实施 |

**仍有技术缺口**：探针不是正式的 Rebucca Web 界面，也没有重用实时流队列；OpenCV 视频偏移不等于已校准 PTS；部分上游检测引擎内部会捕获异常返回空列表，故`model_runs.status=returned`只表示调用返回，不能断言“成功扫描无目标”；JSONL 中 VLM 原文没有经过可信属性解析。正式系统必须在 S3/S4 解决时间精度、失败状态与完整证据协议。

## 5. 下一步正式复用顺序

1. 在 Rebucca 安装环境按 S2 实测同一段车辆录像：D-FINE N、其他 YOLO、Qwen 4B；保留 0 检测帧、误分类帧和分歧，不看总框数作结论。
2. **优先从 Rebucca 的推理端增加“逐帧检测结果”导出**，将 `EngineFactory/DetectorWorkerPool` 包装为独立本地任务接口，而不是让 TrajNexa 访问 Django 管理数据库或把报警输出当检测结果。
3. TrajNexa 后端实现小模型/大模型供应商适配器，历史录像复用现有录像校验抽帧入口；再增加可编辑的 Prompt 工作台和多模型实验室。
4. 通过样本回归验收后再接单镜头 Tracklet 与跨镜头时空分析；人工复核始终区分原始观测、辅助描述与关联假设。

参考：`design/26_能力地图与复用契约.md`、`design/30_Rebucca技术验证与集成决策.md`、M5 多模型与提示词管理决策（独立 Draft PR `docs/m5-model-prompt-management`）。
