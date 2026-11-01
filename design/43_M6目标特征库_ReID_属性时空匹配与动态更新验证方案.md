# 43｜M6 目标特征库 × ReID × 属性 × 时空 × 动态更新：验证方案 V0.1

> 决策日期：2026-11-01。性质：**技术验证 Demo 方案，尚非功能验收报告**。
> 本文先于代码实施；仅当有真实样本和可重跑结果时，才把“规划”改为“已验证”。
> 业务目标：调查人员提供一张目标照片，TrajNexa 查找归属录像，观察该目标在同一录像中的连续帧，补全可信的可见外观线索；再在其他已登记、获授权的多个摄像头录像中进行**候选检索和轨迹还原**，而不是仅提示核查哪些摄像头。
> 人工核实前只称“疑似同一目标/待核实候选”，不得自动确认真实身份。

## 0. 依赖、现状与边界

**以 `main@4f6f7097` 为设计基线：**

| 能力 | 已有复用点 | 当前缺口 |
| --- | --- | --- |
| 小模型检测与候选框 | `app/detectors.py` → `app/engines/EngineFactory` → `proposals`；`/ai/model-test/detect` | 检测类别并不等于同一目标；部分模型配置错误，先修 P0 |
| 人员 ReID 引擎 | `app/engines/reid_onnx_engine.py` 提供 `extract_embeddings(frame_bgr, boxes)`，OSNet ONNX | **只是引擎文件**；未装入 registry / 图片测试 / 目标特征库；需实际 OSNet 权重和验证 |
| 大模型特征 | `app/m4_vision.py:describe_roi`；`/ai/model-test/roi` 临时 Prompt | 返回短语数组，不是带证据/状态的稳定属性表；尚未与观测持续关联 |
| 截图、录像、时间来源 | `MediaAsset`、`RecordingClip`、`m4_video.fetch_frame`、`overlay_identity.grab_evidence_frame` | 现有视频仅有限关键帧预览，无连续 Tracklet / Verified PTS |
| 观测记录 | `TargetObservation.analysis_json`（人工框选、来源） | 不是可版本化的目标档案，无 embedding、候选隔离及更新协议 |
| 摄像头/路网 | `map_db_gis` / `map_db_spatial` 对 Map 只读 | 还没有连通距离/方向/到达时间约束的跨镜头关联 |
| 模型测试 UI | `ModelTestLab.vue` | 只比较检测框/ROI 提示词，尚不能“指定 A，在 B 中排序” |

**最高约束：** [26_能力地图与复用契约](26_能力地图与复用契约.md) 的 Demo 优先与 C1–C12 有效；取帧唯一入口 `overlay_identity.grab_evidence_frame`、录像只读、时间/摄像头名称用 `OverlayIdentity` 与原有人工绑定；新数据表需 Alembic；前端走 `/api`；TrajNexa_Map 只读，保持 OSM 基座/人工图层边界。M5 [42_检测引擎移植](42_检测引擎移植.md) 已把 Rebucca 引擎移植入仓库，**本阶段不得再新建 Rebucca sidecar 或重复实现 ReID 引擎**。

## 1. 五模块职责

| 模块 | 输入 | 输出 | 首批验证 |
| --- | --- | --- | --- |
| **目标参考特征库 TargetProfile** | 用户给定照片 + 手动框 + 来源证据 | 不变的初始目标锚点、多视角参考图、人员/车辆/人车关系、模型版本与可见属性；人工确认记录 | P1 在模型测试内先使用**临时内存 profile**，不提前建正式数据库 |
| **ReID 视觉匹配** | 原图像素 ROI + 已安装的类型合适的 ReID 权重 | L2 单位向量、候选 cosine 排名、相似度及来源 | 人员 OSNet 已移植，先做 A↔B 单图候选检索；车辆 ReID 不能借用人员 OSNet |
| **属性匹配** | VLM 结构化可见属性 + 质量/遮挡/可见性 | 一致、矛盾、未知的解释性证据 | 紫色衣服、尾箱、侧挂粉色物品等；不把“未观察到”当“确认不存在” |
| **时空约束** | 来源摄像头、角标时间来源/校验程度、相机方位、路网可用性 | 可到达性区间、物理不可能冲突或“未知” | P3 才接；缺失路/未覆盖视野不得作否定证据 |
| **动态特征更新** | 已确认连续帧/摄像头候选 + 原始目标锚点 | 版本化候选/确认池、增加的参考模板、撤回与审计 | P2 同视频多帧开始；跨镜头未确认候选**绝不自学习** |

### 1.1 不是“让 YOLO 学会识别这一个人”

- YOLO、D-FINE **只负责在每帧发现候选**；不能直接用一段中文属性替代权重训练，也不能把检测置信度当同一目标概率。
- 人员 ReID 用 **Person OSNet**；车辆使用另行验证的 Vehicle ReID；骑乘组合是**人员、车辆、物品及同时出现关系**的联合观测，不应整体归成一个新 YOLO 类别。
- VLM 以低频关键帧描述；ReID 向量适合批量计算；交通时间/摄像头路径用于候选约束。
- 候选检索与身份确认严格分开：排序前几名≠确认身份；相似度不解释成百分比身份概率。

### 1.2 骑乘人车示例

目标初始图：骑手上衣偏紫，当前帧**未见头盔佩戴**；疑似电动两轮车、有尾箱、右侧粉色悬挂物疑似头盔，尾箱上方疑似绑有物品。按画面可见情况分开记录：

```json
{
  "target_type": "rider_vehicle_pair",
  "reference": {"anchor_id": "initial_manual_roi", "verified": true},
  "entities": [{"key":"rider","type":"person"}, {"key":"vehicle","type":"two_wheeler"}],
  "relations": [{"subject":"rider","predicate":"riding","object":"vehicle","time_scope":"observed_frame"}],
  "attributes": [
    {"entity":"rider","key":"upper_color","value":"purple","visibility":"visible","certainty":"likely"},
    {"entity":"rider","key":"helmet_worn","value":"not_seen","visibility":"head_visible","certainty":"likely"},
    {"entity":"vehicle","key":"rear_box","value":"present","visibility":"visible","certainty":"likely"},
    {"entity":"vehicle","key":"side_hanging_item","value":"pink_object","visibility":"visible","certainty":"likely"},
    {"entity":"vehicle","key":"side_hanging_item_type","value":"helmet","visibility":"partial","certainty":"suspected"},
    {"entity":"vehicle","key":"rear_box_tied_item","value":"present","visibility":"partial","certainty":"suspected"}
  ]
}
```

上面是**期望字段示例，不是模型对原图的已验证识别结果**。每个属性还需保存 `source_media_id/clip_id`、帧请求偏移/时间来源、ROI、模型/Prompt 哈希、人工核实状态；`not_seen` 不等于 `absent`。动态可变的“是否戴头盔”与车辆固有结构要分开设置时间有效范围。

## 2. 匹配计算与决策

建议用**逐级召回/重排**，而不是一开始把所有模型的数值简单加权：

1. **检测与单镜头去重**：D-FINE/YOLO → 框 → 局部短轨迹；优先低漏检，保留模型/框来源。图片 P1 暂不做跟踪。
2. **类型一致的 ReID 排序**：固定人员 OSNet 对人员 ROI；车辆需独立向量模型。对两张清晰参考图做多模板 cosine（各模板评分都保留），展示 Top-K。不能把不匹配类型的向量强行比较。
3. **可见属性重排**：按 `match / conflict / unknown` 汇总。只有**相关部位足够清晰且确证不一致**才记冲突；缺视角、遮挡、画质差记 unknown；显著但不确定的“粉色挂物”等为核查线索而非硬排除规则。
4. **时空可行性**：前后镜头的时间窗口、摄像头方向、路网可达区间与估计速度；仅对已校验来源施加硬约束，未核实时间给出范围/不确定标志。
5. **多帧复核与人工裁决**：模糊候选可利用临近同行人/车辆作为时间位置参考，但不把伴行关系直接当身份凭据；VLM 只复核 Top-K 关键帧；证据不足则继续追踪或返回“未能确认”。

**权重/阈值原则：** 人员余弦阈值、车辆余弦阈值、属性权重、时空容忍区间必须从真实正负样本验证中标定，文档**不预设**诸如“0.85 = 85% 同一人”的伪概率。计算排名时保留各维独立分数与冲突列表，避免过早合成为不可解释的一个分值。

## 3. 动态更新防漂移机制

将来源分为：
- **锚点层（immutable anchor）**：用户最初选中的 ROI + 所属案件/录像/坐标（不可被后续推断替换）。
- **可信层（confirmed templates）**：当前录像连续跟踪且经核实、或由人工确认的跨镜头同目标证据，可增加视角模板/高质量局部图；版本+来源可回滚。
- **隔离层（pending candidates）**：单凭 ReID/属性/时空建议的其他摄像头目标，不写入参考向量池，不参与自动再检索的自扩张。
- **否决/撤销层（rejected/revoked）**：保留拒绝原因、模型版本、人工操作，后续分析不得覆盖人工决定（对应契约 C6）。

即使“高分候选”，也不能自动将其属性添加为该目标固有特征。允许对每帧保存**观测**，但提升成目标**参考**需有明确人工复核/可追溯证据。置信度不随重复帧数量无上限增加；同一轨迹的高度相关帧不可被当作独立投票。

## 4. 分阶段验证：先证据、再扩展

### P0-A｜检测器配置与候选准确性（先于 ReID）

当前 `backend/config/detectors.json` 中 `yolo-v5` / `yolo-v8` / `yolo-v26` 三条登记实际指向 D-FINE ONNX 权重，是**类型与权重不符的测试条目**；`dfine-n` 内联标签仅 58 项且与标准 COCO80 顺序从索引 4 起不同。**不得以此配置出具模型召回结论。**

验收：
- 去掉不能工作的伪 YOLO 实例或替换为与算法对应的真实权重；不改用户机器上现有的私有 ONNX 文件。
- `dfine-n` 统一引用 `config/labels/coco80_zh.txt` 的 **80 类正确顺序**；不能因替换中文字符串而改变 class index。
- 保留真正存在的 `dfine-n`、`yolo11n` 配置；若机器缺权重则显示不可用，**不伪造框或自动换文件**。
- 增加纯配置契约测试：禁止伪 YOLO 指向 D-FINE；D-FINE 标签索引与 COCO80 一致；对应权重实际加载/检测另列现场测试。
- 图像 P0 样本必须包括：小/远骑手、两轮车、尾箱、粉色悬挂物、遮挡/夜景，按 **检出/漏检/误检**分开记录。

### P0-B｜CI 测试入口恢复

当前 `.github/workflows/m4-contract.yml` 仍引用移植时删除的旧测试（例如 `test_m4b_detection.py`），导致 `main` Actions 在收集阶段失败。以**仓库实际存在的测试**替换旧文件引用；至少执行 `test_detectors_registry.py`、`test_m5_model_test_api.py`、`test_m5_vision_registry.py`、`test_m4a_observations.py`、`test_m4c_*.py`、`test_architecture_contracts.py`。前端类型检查/构建保持。不得把“CI 通过”描述为“模型效果已通过”。

### P1-A｜ReID 引擎图片链路（先人员）

最小输入：**参考图片 A（人工指明人员 ROI）** + **搜索图片 B（包含多个候选人员）**。
- `ModelTestLab.vue` 下加独立“指定目标相似检索（实验）”区，复用现有图片选择/结果画布模式；只在模型测试页。
- 后端调用已有 `detectors.detect_flat/proposals` 生成 B 的人员候选，校验候选 ROI；调用已移植的 `ReidOnnxEngine.extract_embeddings` 对 A/B 原图裁剪区域提取向量，统一 L2 归一化后按 cosine 降序排序。
- **人员 OSNet 权重**由部署者在服务器本地配置并核验 SHA256；模型缺失时返回“未安装/无法实验”，不得以颜色或 YOLO 置信度伪装 ReID 分数。
- 输出每候选：`proposal/model_id/source_image_sha256/bbox/class/embedding_model_sha256/cosine_similarity/rank`，`identity_established=false`。
- 界面必须展示可解释排名以及原始候选框，**不能直接写案件/目标档案**。
- 单元测试可 mock embedding；至少 1 个真实 OSNet 权重样例需验证：同目标跨视角 Top-1/Top-5、相似外观误报、CPU 性能。

### P1-B｜车辆 / 人车组合

- 验证**专用 Vehicle ReID** 的权重/模型输入协议、许可、性能。仅有 Person OSNet 时不得对车辆输出“车辆 ReID 分数”。
- 骑乘组合需要**独立人框与车框**，同帧关联（位置重叠、运动及稳定关系）先只提供建议，人工可调整。头盔/尾箱等细粒度属性留给 VLM 关键帧核查。
- 使用四组消融对照：人员 ReID、车辆 ReID、人员+车辆、人员+车辆+可见属性；不能把“人车组合”当一个已训练 ReID 类别。

### P2｜单录像连续帧目标档案

- 只读采用已有 `m4_video.fetch_frame` / `overlay_identity.grab_evidence_frame`；从**用户给定图片关联的已登记录像**寻找起始目标，再在同片段前后帧做短时跟踪（ByteTrack 等是待验证可选方案，不是已实现）。
- 关键帧依据清晰度、遮挡、视角差异挑选，避免逐帧请求 VLM；记录帧偏移是 `requested_offset`，并与 `verified_pts` 严格区分。
- `TargetProfile` V1 原型：锚点、confirmed/pending/rejected、多模板向量、属性与来源；先可使用测试会话 JSON，进入真实案件持久化时才新增 Alembic，不能悄悄篡改 M4a `analysis_json` 语义。
- 量化：与初始单图对比，**多帧是否提升 Top-K 命中**，以及错误候选是否导致漂移。

### P3｜多摄像头时空候选与追踪任务

- 一项 `InvestigationTask` 下多项 `CameraAnalysisJob`，本地视频可并行读取、统一推理池限制 GPU 常驻实例；不把“多摄像头并行”理解成“同 GPU 多模型无限并发”。
- 摄像头 ID 来自现有人工目录绑定、地图只读数据；时间保留 OCR/文件名来源与校验程度；路网缺失、角标漂移或无重叠视角时 `spatiotemporal_status=unknown`。
- 输出候选轨迹路径、支持/冲突证据及未覆盖片段；可继续增量分析下一批摄像头直到获得足够清晰证据或明确证据不足，而不是仅输出推荐摄像头。
- **先通过图片与单视频阶段验证**，再创建持久 Job、Checkpoint、重试和取消机制。

## 5. 实验数据与可重跑评估协议

一组样本至少同时有：
1. **正例**：同一主体的不同视角、尺度、曝光、压缩程度；同摄像头/跨摄像头分别标注。
2. **难负例**：相似紫色上衣、同款/同色尾箱电动车、同形粉色挂物、拥挤/相互遮挡场景。
3. **未知**：不清晰到无法确认目标的样本，不能强迫二选一。
4. **视频对照**：初始单帧 vs 同录像多帧 vs 跨相机候选，人工提供“同一/不同/不足以判断”标签，记录来源摄像头与时间信息是否可信。
5. 所有样本仅在有合法授权的本地环境使用；远程视觉 API 发送前必须明确数据流出范围及适用授权。

**指标**（分别记录模型与数据切分，不预定虚构阈值）：
- 检测：每类 recall/precision、远小目标的漏检率、推理耗时；
- ReID：Top-1、Top-5、mAP（需正负标注与候选库）、误关联率、拒识/未知保留率；
- 属性：可见属性逐项准确率、unknown 比例、对遮挡缺失的误否定比例；
- 时空：明显不可能路径的拦截率、真实可行路径误排除率；
- 动态更新：Top-K 改善幅度、目标漂移率、人工撤销可用性；
- 性能：每模型 CPU/GPU 延迟、内存/显存峰值、每分钟可处理帧数。

**报告口径**：每次实验保存模型名、模型权重 SHA256、预处理输入尺寸、阈值、Prompt hash、图片 SHA256、标注版本、时间来源、成功/失败状态。初步验收以“可复现的正负例结果和可解释排名”为准，**不以模型相似度 > 某值作为自动确认身份**。

## 6. 分工、接口与不重复建设

```text
主工作台：用户指定调查目标 → 人工证据/待核查候选（无模型试验按钮）
         │
         ▼
左侧「模型测试」：输入参考 A + 搜索 B → 检测/Person ReID 排名
         │                                            │
         │                     后续车辆 ReID / 属性消融 / 时空测试
         ▼
参考图 / 已确认多视角 / 待核实候选隔离 / 版本回滚
         │
         ▼
同录像多帧补全 → 同时调度多台摄像头录像 → 候选轨迹还原
```

- `detectors.py` **仍只负责框检测**，不要将 embedding 当成 detection box 塞进已有 `/ai/image-models` 字段；ReID 通过**单独的实验适配层**暴露，与检测器共享推理资源纪律。
- `m4_vision.py` 继续负责人员/车辆提示词解析及原图 ROI 裁剪；正式结构化属性字段/持久化须单独版本化，不覆盖 M4a 人工观测。
- 取帧只走 [26](26_能力地图与复用契约.md) 唯一入口，不新造 seek / ffmpeg 轮子。
- 任何自动候选均应能展示“不确定”和失败，不得自动提升为“人工核实”。

## 7. 逐阶段交付状态（首次建立）

| 阶段 | 当前状态 | 完成证据 |
| --- | --- | --- |
| 本文规划 | **已提交** | 本文、文档索引及 Draft PR #4 |
| P0-A 模型注册/标签修正 | **代码与配置测试已提交；真实权重待验收** | config/detectors.json；test_packaged_detector_config.py |
| P0-B CI 恢复 | **已通过** | Actions #41305066182：后端/前端 checks 成功 |
| P1-A 人员 ReID 图片检索 | **代码+mock 测试通过，真实 OSNet 待验收** | ModelTestLab.vue 第 05 区、test_m6_person_reid.py；需真实跨视角 Top-K |
| P1-B 车辆/人车多模态 | **未完成** | 专用模型验证 + 消融报告 |
| P2 连续视频动态特征 | **未完成** | 多帧同目标对照及模板版本 |
| P3 多摄像头关联 | **未完成** | 多个实际摄像头录像的候选轨迹 |

> 实施时逐项标注：`代码已提交`、`自动化 mock 测试通过`、`真实权重测试通过`、`业务验收完成` 是四个不同状态。不能混用。


## 8. 2026-11-01 本批实施记录：P0 + P1-A 最小代码闭环

> 以下是**代码与 mock 测试证据**，不是现场 ReID 准确率报告。真实 OSNet 权重尚未纳入仓库，无法在 GitHub Actions 中测试真实 embedding。

### P0-A 完成（配置层）

- `backend/config/detectors.json` 已移除 3 条将 D-FINE 权重误作 YOLOv5/v8/v26 的配置。模型 `yolo-v5` / `yolo-v8` / `yolo-v26` 若要重新注册，必须提供与算法相符的真实权重；**没有删除机器里的任何权重文件**。
- D-FINE `dfine-n` 删除错序 58 类 inline labels，改为 `config/labels/coco80_zh.txt`（80 类标准 COCO 顺序）；`yolo11n` 保留。
- `backend/tests/test_packaged_detector_config.py` 保证标准 80 类、检测器 ID 不重复，以及 YOLO 不指向 D-FINE 路径。
- 权重是否存在、真实 D-FINE/YOLO 模型召回率、类别是否适合两轮电动车，仍需要 Windows 模型测试截图复核。**COCO 的“摩托车”类别不等于细分识别电单车，不能通过修改标签文字伪造类别。**

### P0-B 完成（CI）

- 修复 `.github/workflows/m4-contract.yml` 中已被移植删除的旧 M4B、Rebucca sidecar 测试文件引用。
- 现在执行 `test_packaged_detector_config.py`、`test_detectors_registry.py`、M4/M5 当前有效的接口/架构测试及新增 M6 测试，前端保留 `npm run build`。CI 成功只能证明自动化契约通过。

### P1-A 代码实现（现场模型效果**待验证**）

| 模块/入口 | 内容 |
| --- | --- |
| `app/m6_person_reid.py` | Reuse `EngineFactory.create(..., task_type="reid")` / `ReidOnnxEngine.extract_embeddings`；原始像素 ROI → 人员向量归一化 → cosine 排序 |
| `POST /api/v1/ai/model-test/reid-search` | A 参考人员图、参考框、B 搜索图片、已加载检测器 ID、检测阈值；只处理人框，结果带图片/权重 SHA256、排序、耗时和 `identity_established=false` |
| `ModelTestLab.vue` | 左侧模型测试页新增第 05 区：选 A 图/手绘人员 ROI，复用上方 B 测试图片、选择检测模型、显示前 N 个候选与 cosine 值 |
| `backend/tests/test_m6_person_reid.py` | 采用假 embedding 的 Top-K 排序、排除车辆、空候选、缺失权重、坏框、错误模型、不写案件数据库 |
| `backend/app/config.py`、`.env.example` | 新增 OSNet 仅在测试模块启用的部署参数 |

在 Windows 宿主机将经过许可的**人员 OSNet ONNX** 放到：

```text
backend/models/reid/osnet_person.onnx
```

示例 `.env`（`.env.example` 有同名字段）：

```dotenv
M6_PERSON_REID_MODEL_PATH=models/reid/osnet_person.onnx
# 可选，但建议填实际权重的完整 SHA256；不匹配会拒绝推理
M6_PERSON_REID_MODEL_SHA256=
```

PowerShell 读取权重 SHA：

```powershell
(Get-FileHash .\\backend\\models\\reid\\osnet_person.onnx -Algorithm SHA256).Hash.ToLower()
```

部署注意：
- 路径相对 `backend/`，**必须位于 `backend/models/` 下**，不支持指向任意系统文件；支持现有 compose 模型目录挂载。
- 此处不下载任何模型权重，不宣称某个公开模型可直接工作；应先核验 OSNet ONNX 输入形状（预期 batch1 × RGB3 × H256 × W128）、输出 embedding 维度、模型许可和兼容的 ONNX Runtime。
- 重新运行 API，打开左侧**模型测试**：先在“01 · 测试图片”中上传包含多个候选人的图片 B，再在“05 · 指定目标相似检索”选择 A、拖拽框选**人**、选择真实已加载的 D-FINE/YOLO，点击“开始指定人员检索”。
- 没有 OSNet 权重时界面应明确报不可用；**不会用色彩/检测分数替代 ReID 相似度**。
- 返回的是人员外观 cosine **排序**，不是概率、不是人脸识别，也不是车辆 ReID；跨摄像头追踪及目标档案更新尚未接入。

### 本批真实验收清单（未完成，需提供实测数据）

- [x] OSNet 真正加载：记录权重 SHA、输入/输出 shape、CPU 推理耗时（见 §9）；
- [x] 同一人跨视角 A/B：Market-1501 上 Top-1 60.10% / Top-5 77.67%（基准数据，非现场）；
- [ ] 颜色相同但不是同一人的难负例：误匹配率；
- [ ] 小目标模糊、多遮挡：unknown / 无有效向量返回；
- [ ] 与有尾箱的两轮车/骑手组合：**仅人员维度**先测试，车辆维度需单独的 Vehicle ReID；
- [ ] 与用户确认 P1-A 效果后，再启动 P1-B 与 P2。

### 本批 GitHub 自动测试证据

- P0-A + P0-B：M4/M5 contract checks 已修复并通过；
- P1-A：`test_m6_person_reid.py` mock 排序与无案件写入测试已加入 CI，前端构建通过；
- **以上不替代真实模型与录像评测**。


## 9. 2026-11-01 真实 OSNet 权重安装与基线评测

### 9.1 权重与格式（关键坑）

| 项 | 值 |
| --- | --- |
| 模型 | OSNet x0.25（MSMT17 训练），ONNX |
| 来源 | HuggingFace `anriha/osnet_x0_25_msmt17`（MIT） |
| 落盘 | `backend/models/reid/osnet_x0_25_msmt17_b1.onnx`（0.87 MB，gitignore） |
| SHA-256 | `f3352b03dc6a188836af98ebadb1234b8b9083b3fa9bbd1077d1c559ab7374fa` |
| 输入 / 输出 | `[batch,3,256,128]` → `[batch,512]`（L2 归一化在引擎侧做） |
| CPU 耗时 | 单张 crop ≈ 8 ms；16 人一次请求 0.60 s |

原模型导出时把 batch 固定成 **16**，而 `ReidOnnxEngine` 逐 crop 送 **batch=1**，直接加载会在
`session.run` 处报形状不符。处理方式是把 ONNX 的输入/输出第 0 维改成动态 `batch`
（`onnx` 改图，不动权重），得到 `*_b1.onnx`。**换任何来源的 OSNet ONNX 都要先确认这条。**

配置（`.env`，不入库）：

```
M6_PERSON_REID_MODEL_PATH=models/reid/osnet_x0_25_msmt17_b1.onnx
M6_PERSON_REID_MODEL_SHA256=f3352b03dc6a188836af98ebadb1234b8b9083b3fa9bbd1077d1c559ab7374fa
```

### 9.2 图片链路实测（D-FINE 检测 + OSNet 排序）

样本：现场街景截图 1920×1088，D-FINE 检出 43 个候选，其中 16 个 `person`。
参考框取第一个人（`[0.3135,0.4133,0.3552,0.5101]`），B 图与 A 图同源时：

```
detect 200 0.83s persons 16 / 43
reid   200 0.60s
  #1 sim=1.000 idx=0   ← 自匹配，排第一
  #2 sim=0.537 idx=37
  #3 sim=0.526 idx=10
  #4 sim=0.517 idx=6
  #5 sim=0.516 idx=16
```

说明链路与相似度量级正常（同 crop=1.0，不同人 0.5 上下），但这只是**一致性自检**，不代表跨视角识别力。

### 9.3 Market-1501 基线（可复跑）

```
docker run --rm -v /path/Market-1501-v15.09.15:/data/mkt:ro \
    -v "$PWD/backend:/srv" -w /srv/app trajnexa_main-api \
    python scripts/reid_eval_market1501.py
```

脚本：`backend/scripts/reid_eval_market1501.py`。协议按官方：junk(-1) 不计入，同 id 同摄像头的库图不算命中。

| 指标 | 结果 | 条件 |
| --- | --- | --- |
| Top-1 | **60.10%** | 3,368 query / 15,913 gallery |
| Top-5 | **77.67%** | 单 query、cosine 排序 |
| 特征提取 | 412 s（15,913 张） | 容器内 CPU，x0.25 |

注意这是**跨数据集**（MSMT17 训练 → Market-1501 检索）的分数，属于偏保守基线；
若换成 x1.0 权重，通常还能再高一截，但**必须先解决 9.1 的 batch 维问题**。

### 9.4 结论与限制

- 人员 ReID 的“向量可比、能排序”这件事已验证；x0.25 在标准集上是 **60/78** 的量级，够做候选排序，不够做身份判断。
- **基准分数不等于现场效果**：监控画面的目标更小、更糊、遮挡更多；仓库里目前没有 `storage/videos` 录像，
  没能做“同一录像跨帧”的现场验证。
- 下一步要决定：先换 x1.0 权重并重跑本基线，还是直接拿一段现场录像做跨帧验证（后者更能回答“能不能用”）。
