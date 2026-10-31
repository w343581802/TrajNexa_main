# 32｜M4d Rebucca 模块依赖与风险审计 V0.1

> 2026-10-31｜状态：**源码静态审计**，未进行模型权重推理、视频端到端测试或模块移植。
> 来源：可读取的公开镜像 https://github.com/feixintianxia/beixiaocai_rebucca ；上游原仓库 https://github.com/beixiaocai/rebucca ；镜像与原仓库的提交一致性尚未核实，不能将镜像源码视为已锁定上游版本。

## 1. 已读源码与提取判断

| 文件 | 依赖与行为 | 结论 |
| --- | --- | --- |
| `app/utils/LLMUtils.py` | 仅依赖 Python 标准库 base64 和 openai SDK；OpenAI-compatible chat.completions 传 JPEG data URL + prompt，返回纯文本 | **最容易独立提取**；但功能很薄，可参考设计写更合适的适配器 |
| `app/analysis/engines/base.py` | 标准库，定义 BaseEngine、DetectionResult，接口 `load/detect/info` | 可参考接口，直接复制的收益有限 |
| `app/analysis/engines/onnx_engine.py` | 依赖 base、yolo_postprocess、onnxruntime、cv2、numpy；`detect` 返回 list[DetectionResult] | 仅适用于其 YOLO 处理链；**不可当 D-FINE 即插即用引擎** |
| `app/analysis/engines/yolo_postprocess.py` | numpy、cv2，按 YOLO 模型/任务解析，含 NMS、坐标缩放 | 可做 YOLO 实验对照；不替换 M4b D-FINE |
| `app/analysis/tracker.py` | 标准库 logging；轻量贪心 IoU 关联 | 可独立提取做单镜头基线，但存在身份切换风险 |
| `app/analysis/pipeline.py` | FrameSource、MotionDetector、IoUTracker、规则与事件回调；混合实时流、节流、快照、报警 | 不整文件移植；只参考异步队列与分析调度设计 |
| `LICENSE` | MIT，要求复制或实质部分使用时保留版权及许可声明 | 源文件移植必须带许可和来源记录；第三方依赖/权重另查 |

## 2. 源码层面的关键风险（需要测试确认影响）
1. **LLM 仅返回文本**：`infer` 取 `choices[0].message.content`，未返回请求 ID、模型标识、token、延迟、失败原因；`check_happen` 通过逗号分隔关键词做子串匹配，不能作为可靠的事实判定或身份关联。应保留原始响应及未知/拒判。
2. **OnnxEngine 无法直接替换 D-FINE**：它将输出交给 `yolo_postprocess.decode_outputs`，按 YOLO5/8/11/26 解析；M4b 的 D-FINE 输入/输出契约不同。
3. **推理失败与空结果混淆**：`OnnxEngine.detect` 在 not-ready、空帧、异常时均可能返回 `[]`，需要适配器将“模型不可用/推理失败”和“成功推理无候选”严格区分。
4. **跟踪器为贪心 IoU**：`IoUTracker.update` 没有显式排除已匹配 track 再次被本帧另一检测匹配；存在多个检测共享一个 track_id 的可能，必须先写回归测试。无 ReID、无跨镜头关联。
5. **后处理 NMS 需单测**：`_nms` 传入的 boxes 为 xyxy，而 OpenCV `NMSBoxes` 常见输入约定为 xywh；需要核实实际坐标语义，避免错误抑制。此处是代码风险判断，未实测。
6. **实时与历史不同**：`CameraPipeline` 用 deque(maxlen=2) 丢旧帧适合低延迟实时流；历史录像离线分析不能默认丢帧，否则影响证据完整性。
7. **镜像版本风险**：目前读取的是 `feixintianxia/beixiaocai_rebucca`，需取得并记录其 commit SHA，核对原仓库历史后才能作为移植基线。

## 3. 下一次工程改动的最小范围
- 不直接复制 `pipeline.py`、`biz_rules.py` 或 Django ORM。
- 先写 TrajNexa 内部**VLM 实验适配器**：输入只读 JPEG 字节、模型配置、提示词与来源信息；输出结构化调用记录、文本、错误/拒判状态；不自动提交观测、不修改 Map。
- 保留 M4b D-FINE 与 M4c `overlay_identity.grab_evidence_frame`。
- 单镜头 IoUTracker 仅作离线基线，修复同帧重复匹配后再接入。
- 使用脱敏样本测试：失败与空检测区分、错误 API key、超时、非 JPEG、空响应、拒判、模型版本留痕、人工审核边界。
- 完成许可台账、源 commit 锁定及环境配置后再开始代码移植。

## 4. 复用优先级结论
**P0：VLM 适配器（参考 LLMUtils）→ P1：IoUTracker 单镜头基线（测试后可提取）→ P2：YOLO ONNX 引擎作为可选对照 → P3：实时 pipeline 仅参考设计。**

本结论仅是源码静态审计结果，不表示已运行、已验收或具备跨镜头连续追踪能力。
