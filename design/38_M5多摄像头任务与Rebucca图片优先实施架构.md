# 38｜M5 多摄像头追踪任务架构 × Rebucca 复用 × 图片优先整改（V0.1）

> 2026-11-01 决策稿；2026-11-01 开始第一批图片集成。
>
> 级别：**已确认的总体方向 + 分阶段落地**。本文所列多视频调度、共享推理资源池、跨镜头关联属于**目标架构**，不表示已实现或现场验收。
>
> 依据：`00_总体规划与Map职责边界_V1.0.md`、`26_能力地图与复用契约.md`、`37_M5_Rebucca视频大小模型链路与复用试验.md`，以及本轮确认的“同一追踪任务可同时分析多个摄像头录像；首先把图片分析做好”。Rebucca 上游基线见 37。

## ADR-M5-01：任务中心而不是视频中心

**决定**：TrajNexa 的调度单位是“一个被授权的目标追踪/轨迹研判任务”，任务下可以存在任意数量、不同时间窗、不同来源的摄像头视频分析子任务。每个子任务可复用 Rebucca 的 `EngineFactory`、预处理、检测器、短时跟踪思路；模型实例/显存由共享推理服务管理。TrajNexa 管理任务状态、来源/时间、证据链、摄像头关系和多轮策略。

```text
TrajNexa InvestigationTask（一个研判目标，状态/范围/预算）
  ├─ CameraAnalysisJob(A, clip A1, 时间窗 t1, profile P1) ─┐
  ├─ CameraAnalysisJob(B, clip B1, 时间窗 t2, profile P2) ─┼─ Worker/队列
  ├─ CameraAnalysisJob(C, clip C1, 时间窗 t3, profile P3) ─┤    多路并发解码
  └─ CameraAnalysisJob(B, clip B2, 补帧/新时间窗) ─────────┘    有界共享推理
                                                            │
   本地只读视频读取 → 已知帧索引/时间来源 → 分阶段小模型推理 ─────┤
   目标 ROI（原始帧）→ 可编辑 Prompt 与可选视觉大模型 ────────┘
                                                            ▼
                 逐帧候选 + 单镜头 tracklet + 多模型差异
                                                            │
                                      TrajNexa 证据池/假设/人工复核
                                                            │
                                新证据触发任务重排/追加/暂停/终止
```

**不照搬 Rebucca 的地方**：
- Rebucca `CameraPipeline` 的 `deque(maxlen=2)` / 取最新帧适合直播，但历史录像不能静默丢帧；需要确定性的帧采样计划、准确记录实际处理帧、失败断点与可恢复偏移。
- Rebucca 的运动检测只能作为计算优化开关；远距、低运动目标的离线分析允许关闭 motion gate，并可做分块/补帧重检。
- Rebucca 的 `IoUTracker` 仅做单镜头临时编号，先修复重复匹配/ID 切换风险，不作为跨镜头身份 ID。
- Rebucca 业务规则 `check_happen` / 报警不是 TrajNexa 的事实或结构化属性输出；需要独立逐帧检测与 VLM 记录。
- 两个检测模型不要求各起一个完整视频解码链；同一帧只读复用，独立保留模型输出和类别映射。
- TrajNexa_Map 仍只读发布摄像头地理数据；M5 不向 Map 写目标或轨迹。

## 1. 调度与推理资源边界（目标架构）

| 组件 | 拥有者 | 职责 | 首批状态 |
|---|---|---|---|
| InvestigationTask | TrajNexa | 研判起点、授权范围、预算、暂停/恢复、完成/证据不足 | **规划** |
| CameraAnalysisJob | TrajNexa | camera_uuid / clip_id / 时间窗 / frame plan / profile / 去重键 / checkpoint | **规划** |
| LocalVideoFrameProvider | TrajNexa | 读取已有登记录像，复用 `overlay_identity.grab_evidence_frame` 与坏帧过滤；保持原件只读 | **规划** |
| RebuccaDetectorAdapter | 独立 Rebucca 环境 + TrajNexa 薄客户端 | 模型列表、图片推理、逐模型状态、框坐标与标签映射 | **图片集成第一批** |
| SharedInferencePool | 模型推理服务 | 一个模型加载实例可供多个图片/视频子任务复用；限制同时 GPU 大模型数量 | **图片服务最小单请求队列** |
| CandidateEvidence | TrajNexa | 原件哈希、帧/时间语义、模型/权重版本、框与状态、人工复核 | **已有 M4a 图片候选，M5 扩展中** |
| CrossCameraReasoner | TrajNexa | 汇总多镜头候选与时空冲突、多轮追加任务；不自动认定身份 | **规划** |
| PromptProfile | TrajNexa | 模型 × 任务 × 可编辑 Prompt/参数版本 | 详见 M5 文档；**规划** |

建议任务结构：

```json
{
  "investigation_id": "task-id",
  "case_id": "case-id",
  "analysis_jobs": [
    {"camera_uuid":"camera-A","clip_id":"clip-A1","start_offset_ms":0,
     "end_offset_ms":30000,"frame_plan":"sampled_with_gap_record",
     "profile_id":"person-primary","checkpoint_frame_index":0,"status":"queued"},
    {"camera_uuid":"camera-B","clip_id":"clip-B1","start_offset_ms":15000,
     "end_offset_ms":45000,"frame_plan":"sampled_with_gap_record",
     "profile_id":"vehicle-secondary","checkpoint_frame_index":0,"status":"queued"}
  ]
}
```

上面是**规划字段示例，不是现有 API**。`start_offset_ms` 为请求偏移，不是校准后 UTC 时间或可信 PTS。子任务失败可独立重试，不推翻同任务其他已核实结果；新增录像可追加 job 而不重置原来的任务。

**并发原则**：多个摄像头可并发读文件/解码/预处理；GPU 推理采用有界队列、按模型分组或小批次执行。6–8GB 显存下 VLM 可以按阶段串行切换，不把并发摄像头数等同于并发模型实例数。设置任务级 CPU/GPU 配额、优先级、取消和恢复，避免单个任务占满资源。

## 2. 图片优先：实际整改第一批

**先让现有添加要素图片入口可用，再考虑把同一推理客户端接入多摄像头视频 Worker。**

```text
AddElementModal（已有截图上传/OCR/人工框选）
    │
    ├─ 旧引擎：D-FINE（保留回退）或 Qwen3-VL 2B/4B（保留对照）
    └─ 新引擎：Rebucca 检测服务（本机单独进程）
           ├─ /health /models → 已配置的若干检测模型
           └─ /detect（图片 JPEG + 选定模型 ID）→ 逐模型候选
                │
                ▼
      FastAPI M4 /media/{id}/detect（先校验案件归属、文件哈希、只读解码）
                │
                ▼
      UI 同图切模型/多模型对照、显示原始类别/分数/模型来源
                │
                ▼
      用户点选框 → 现有 /roi-features（Qwen 解释，失败降级）
                │
                ▼
       人工确认后才保存 M4a 观测（不得自动写跨镜头轨迹）
```

**为什么不复制 Rebucca 模型代码？** Rebucca `EngineFactory`、各推理 engine、模型标签在 Rebucca 的 Python 环境运行；TrajNexa 只保留 HTTP 薄适配和原件归属校验。本地图片服务实现以 Rebucca `EngineFactory` 为后端，不绕过其预处理或重新实现 YOLO/D-FINE ONNX。

**模型/结果格式**：
- `/models` 返回检测模型 ID、名称、算法类型，**仅显示可用的本地配置**；模型文件路径/密钥不回传。
- `/detect` 接受图片和多个 `model_ids`，依次使用已经加载的 Rebucca 模型；逐模型标记 `returned` / `error`，不把失败伪装为“画面没有目标”。
- Rebucca 的每条 `{box, label, score, mask_polygon?}` 用 `m5_rebucca_contract.normalize_detections` 转成 `bbox_normalized`；`unknown` 类别展示为“其他（待核实）”，保留原标签与来源；**跨模型不自动去重**。
- JPEG 通过同主机或已保护的本机网络调用，只读来源。请求超时、模型缺失、解析失败必须可见。
- 选中候选后从**原始图片**（不是用于整图检测的降采样缩略图）裁剪 ROI，调用视觉模型，保留未经核实语义；人员/车辆提示词后续通过独立 Prompt 工作台配置。
- Rebucca 服务只承担**实验图片推理**：不访问 TrajNexa 数据库、不接 Rebucca 布控报警、不写 Map。

## 3. 第一批验收（图片）

1. 未启用 Rebucca 服务时 UI 明确提示“模型服务不可用”，D-FINE / Qwen 和人工框选仍可用。
2. Rebucca 能列出配置的 2 个检测模型（若本机已有权重），同一张图片可选择其中一个或多选；每框注明来源模型、原始类别及分数。未安装模型不假装可用。
3. 同图多模型框不自动合并为身份确认；手动选中框才走既有 ROI 分析。ROI 来源和原件哈希一致；图像不被修改。
4. 车辆图片至少覆盖白天、夜间、红外、远处小车、两轮车遮挡和多人混行；记录每模型漏检/误检与耗时；未有人工作为真值时不算“准确率”。
5. 服务超时、坏框、非法模型 ID、错误响应不写库不崩 UI；服务限制图片尺寸、请求大小；模型网络端点的暴露方式需说明。
6. CI 使用可替身的 Rebucca 接口测试；**真实 Rebucca 权重 + Windows 端到端必须另行验收**。

## 4. 第二批与第三批（现在不实现）

**第二批：本地录像单路 Worker**：视频读取复用已有截图规范，定义帧序号/采样时间来源/缺帧和 checkpoint；调用同一检测服务；只读逐帧输出。

**第三批：同一 InvestigationTask 下多路 CameraAnalysisJob 并发**：多路线程/队列读录像，共享推理服务（按 GPU 有界调度），统一结果汇集、多轮追加任务。测试 A/B/C 多台摄像头同时工作、B 停止而 A/C 继续、任务取消/恢复、同一模型只有一个加载实例。

注意：多镜头分析不等于跨镜头身份已确认，时间、空间与外观冲突必须显式呈现。

## 5. 依赖、冲突与回退

- 既有 `M4a` 图片上传、`M4b` D-FINE、`M4d` Qwen 模型维持可用；Rebucca 作为可选引擎，不直接替换其他路径。
- 既有 `overlay_identity.grab_evidence_frame` 为后续视频取帧唯一入口，不引入新 ffmpeg/read_frame 实现。
- 本阶段不增加数据库迁移，不改动 TrajNexa_Map，不要求 Rebucca 访问案件目录。
- 若 Rebucca 模型服务不可用，关闭 `M5_REBUCCA_ENABLED` 即回退；不会修改数据库中的任何已核实观测。
- PR #2 规划多模型与 Prompt 工作台，PR #3 承载复用探针及当前图片集成；合并前需复核设计文档索引/总体规划冲突，不直接覆盖旧规划。

## 6. 上游事实和测试证据等级

Rebucca `CameraPipeline` 多模型调用、`EngineFactory` 和 `LLMUtils` 的存在属于源码确认；上游实际模型准确度以用户本机测试为准。新图片适配与 UI 合同测试若通过，仍只能称“模拟测试通过”；没有 Windows 真模型运行日志时不得写成“实测通过”。
