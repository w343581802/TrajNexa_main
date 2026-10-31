# 31｜M4d Rebucca 源码提取审计清单 V0.1

> 日期：2026-10-31｜状态：部分源码确认；尚未移植/运行测试
> 决策基准：`design/30_Rebucca技术验证与集成决策.md` V1.2
> 上游：https://github.com/beixiaocai/rebucca/tree/main （待锁定精确 commit）

## 1. 已核实的上游文件和事实
| 路径 | 已核实信息 | 初步复用判断 | 下一项验证 |
| --- | --- | --- | --- |
| `app/analysis/detector.py` | ObjectDetector 为兼容 shim，委托 `engines.onnx_engine.OnnxEngine` | 不移植旧 shim | 审计 OnnxEngine 输入/输出/依赖 |
| `app/analysis/engines/` | 引擎目录存在 | 优先提取候选 | EngineFactory、ONNX/其他引擎的调用链 |
| `app/analysis/pipeline.py` | CameraPipeline 负责解码与分析线程、检测器、IoUTracker、FrameSource；约 1009 行 | 不整文件移植 | 提取任务循环的必要抽象 |
| `app/analysis/tracker.py` | IoUTracker 被 CameraPipeline 引用 | 可选实验 | 输入输出、遮挡、ID switch 与局限 |
| `app/analysis/frames.py` | FrameSource 被 CameraPipeline 引用 | 默认不移植 | 与 TrajNexa 共享抽帧器对照 |
| `app/utils/LLMUtils.py` | pipeline 内按需实例化 LLMUtils，调用 infer 与 check_happen | 优先审计 | 是否依赖 Django/配置/报警规则 |
| `app/analysis/biz_rules.py` | 负责业务规则匹配、报警上下文 | 不默认移植 | 确认边界 |
| `app/services/alarm_service.py` | 文件存在 | 不默认移植 | 暂无 |
| `app/services/cross_camera_service.py` | 文件存在；名称不能证明满足本项目跨镜头需求 | 仅审计 | 核查算法/数据/边界 |

来源链接：
- https://github.com/beixiaocai/rebucca/blob/main/app/analysis/detector.py
- https://github.com/beixiaocai/rebucca/blob/main/app/analysis/pipeline.py
- https://github.com/beixiaocai/rebucca/tree/main/app/analysis
- https://github.com/beixiaocai/rebucca/tree/main/app/services

## 2. 关键调用关系（源码确认）
```text
CameraPipeline
 ├── FrameSource(rtsp_url, target_fps)
 ├── IoUTracker()
 ├── detector / detectors[]
 ├── biz_rules.matched_rules_for_track()
 └── _llm_verify_track(frame, track, biz_rule)
      ├── 裁剪 track bbox → JPEG
      ├── LLMUtils(...).infer(prompt, jpeg)
      ├── LLMUtils.check_happen(result, validate)
      └── 返回布尔值，供业务报警规则使用
```
在 `pipeline.py` 中，LLM 复核对相同 track/rule 存在 6 秒节流，区域 LLM 检查存在 8 秒节流；这些是上游实现细节，不应照搬为 TrajNexa 的证据判定策略。

## 3. M4d 目标接口（设计草案，尚未实现）
- 输入：案件归属的只读帧或授权测试图片，原件哈希、录像片段 ID、请求偏移、模型版本。
- 小模型输出：候选框、类别、分数、原图坐标、失败原因，不自动生成已确认观测。
- VLM 输出：可观察属性、未知/拒判字段、原始模型响应、调用参数、模型标识；不能只保存上游 `check_happen` 的布尔值。
- Tracklet 输出：单镜头短时局部编号、帧范围、关联质量、可撤销状态；不视为跨镜头身份。
- 复用 `overlay_identity.grab_evidence_frame`，不新建默认视频取帧器。
- 不调用报警服务，不改写 TrajNexa_Map。

## 4. 第一批工程任务与验收
1. 锁定 Rebucca 上游 commit、LICENSE、第三方模型/二进制许可，保留来源台账。
2. 审查 `engines/onnx_engine.py`、`engines/__init__.py`、`utils/LLMUtils.py`、`tracker.py` 的完整源码和 import 依赖。
3. 做无 Django 数据库的独立导入测试；无法独立运行则明确记录所需依赖，不盲目复制。
4. 使用脱敏静态帧测试结构化检测结果和 VLM 拒判；与 M4b 同样本对照。
5. 增加回归测试：无权重时显式失败、帧不可用时不等于无目标、人工审核不被模型覆盖。
6. 通过后再决定第一个真正移植的文件；目前状态均为“待移植”。

## 5. 治理规则
每个移植文件必须登记：上游路径与 commit、许可、直接/间接依赖、移植路径、改动差异、测试命令、回退方式。不要提交模型权重、真实视频、密码或原始敏感图像。不得把上游 README 的功能描述标记为本地实测。
