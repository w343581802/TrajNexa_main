# B7-C0｜WeVisOne-VL 指代定位隔离实验（2026-11-04）

## 目标与当前状态

试用腾讯官方的 \`tencent/WeVisOne-VL-2B\` / \`-4B\`，优先验证“文字描述 → 监控帧中的目标框”，不替换 D-FINE、OSNet、ByteTrack，不触碰已有人工审核和候选排序。**本提交仅提供实验适配器、离线解析测试与 GPU 推理探针；尚无正式 WeVisOne-VL 实际推理成绩。**

参考：
- 官方仓库：https://github.com/Tencent/WeVisOne-VL
- 官方模型：https://huggingface.co/tencent/WeVisOne-VL-2B 、https://huggingface.co/tencent/WeVisOne-VL-4B
- 官方推理：\`from wevisone_vl.engine import WeVisOneVL\`，\`model.predict(image, task="referring", query=..., decode_matting=False)\`
- 官方文本结构：\`<ref>描述</ref><box><100><200><400><700></box>\` 或 \`None\`。坐标为 0–1000 归一化 token；**没有可用的置信分数**。

## 文件

- \`backend/app/wevisone_grounding.py\`：解析官方 referring token，返回归一化与原图像素 \`xyxy\` 框，所有结果保留 \`identity_established=false\`，不会凭空附加置信分数。
- \`backend/scripts/experimental/try_wevisone_vl.py\`：独立、本机推理探针；不经主系统 API 上传任何帧，也不写 Truth Registry。
- \`backend/tests/test_wevisone_grounding.py\`：只测试解析和证据边界，不需要 GPU 或模型权重。

## 运行方式（有 CUDA GPU 的实验机）

建议使用官方 README 的**独立** Python 3.10 环境，安装官方仓库和它锁定的依赖，避免直接修改 TrajNexa 主服务依赖。

先在实验环境中从仓库根目录运行（按实际镜头帧地址替换 \`<frame>\`）：

\`\`\`bash
cd backend
python -m scripts.experimental.try_wevisone_vl \
  --image <frame.jpg> \
  --query "浅蓝色上衣，骑浅色踏板车，车前坐着小孩的人" \
  --model tencent/WeVisOne-VL-2B \
  --backend transformers \
  --out /tmp/wevisone_2b_result.json \
  --annotated-out /tmp/wevisone_2b_boxes.png
\`\`\`

如显存允许，再用相同帧和提示词将 \`--model\` 改成 \`tencent/WeVisOne-VL-4B\`。

无 GPU 时只测试结果解析（**不是模型推理**）：

\`\`\`bash
printf '<ref>rider</ref><box><100><200><400><700></box>' > /tmp/wevisone_fixture.txt
python -m scripts.experimental.try_wevisone_vl \
  --image <frame.jpg> --query "浅蓝色上衣骑车人" \
  --fixture-response /tmp/wevisone_fixture.txt \
  --out /tmp/wevisone_fixture_result.json
\`\`\`

输出 \`mode=parser_fixture_only\` 时不得当成真实实验结果。

## 验证数据与通过条件

1. **正例**：青衣三路真实人工标注观测的原始帧（2-1 #232、2-2 #434、1-1 #225）。每条分别用同一提示词预测；评价是否覆盖人工标注框、IoU、漏检、多实例混淆、每张耗时。
2. **负例**：同一时段不包含指定青衣骑手的画面，测试误检和 \`None\` 拒绝能力。
3. **干扰例**：同画面中的其他骑手、白色车辆、单独儿童以及低画质小目标（如 2-2 #403）。评估是否误指其他个体。
4. **对照**：Qwen3-VL 及现有 D-FINE 检测、OSNet 排序均不修改，记录使用相同帧与输入的差异。模型定位能力与 ReID 身份关联能力分别评价。

**通过条件**：定位框与人工观测对应且在负例中能正确拒绝；保留错误示例和耗时/设备信息。不能以官方基准替代监控实测。WeVisOne 产出的是候选定位，不能把它当成已人工确认的轨迹，也不能使用现有 OSNet \`0.08\` margin 作为指代模型的门槛。

## 当前未接入的部分

- 不作为 M6 调度或综合身份分数来源，不更改默认能力配置。
- 不建立新的长期 VLM 服务、不替换现有 M5 注册表。
- 尚未实现 \`model_capabilities.invoke("visual_grounding")\` 的生产 Provider；要在 B7-B 的模型安全切换/授权/实测回退完善后再接。
- 不验证 alpha matte / 分割（需单独加载像素解码器并评估资源成本）。

## 环境与数据安全

此实验设计为**本机推理**；不自动把监控图片传给公共 Hugging Face Space 或第三方 API。若后续选择远程推理，必须有独立的授权、访问控制、数据保留策略和审计记录。

当前运行环境没有 CUDA GPU、没有 WeVisOne 模型权重；因此本批仅能验证适配器契约，不能发布“2B/4B 在青衣样本上的准确率”结论。

## 2026-11-04 本机 2B 实测（CPU）

权重 4.88 GB 下载完成（实际用时 13.6 分钟，峰值远高于单流测试），`torch 2.11.0+cpu`、`transformers 5.12.1`。
同一句自由文本查询 `person riding a two-wheeler`，1280×720：

- 2-2/0334：1 框 `[463,226,540,335]`，与 D-FINE 人体框 IoU **0.482**，解码 25.4 s；
- 2-1/0285：1 框 `[767,188,840,310]`，与参照框 IoU **0.461**，23.3 s；
- 1-1/0350：2 框 `[1024,107,1103,243]`（青衣目标）与 `[202,23,237,91]`（远处另一骑手），23.6 s；
- 负例查询 `person riding an elephant`：原始输出 `<ref>…</ref><box>none</box>`，记为 `model_reported_absent`，21.1 s。

**拒绝语义修正**：官方文档写裸 `None`，2B 实际输出 `<box>none</box>`；解析器原先会 `invalid_box_tokens` 失败关闭。
现两种都算模型声称“没有”，保留 `rejection_literal`，格式错误仍为 `parse_failed`，两者不混。

结论边界：框无分数、无身份，不参与排序；1-1 的多框说明类别查询不能单独锁定目标。4B 未下载、未推理。
## 2026-11-04 4B 实测（CPU bf16）与 2B 对比

4B 权重 9.21 GB。引擎在 CPU 上强制 float32（约 18 GB，直接 OOM），实验环境改为保留 bf16 后才跑起来，加载 13.4 s。

| 用例 | 2B | 4B |
| --- | --- | --- |
| 2-2/0334 中性 | 463,226,540,335（IoU 0.482）25.4 s | 463,225,540,324（IoU 0.524）193.1 s |
| 2-1/0285 中性 | 767,188,840,310（IoU 0.461）23.3 s | 768,188,841,308（IoU 0.479）169.5 s |
| 1-1/0350 中性 | 目标 + 远处另一骑手，2 框 | 同样的 2 框 |
| 2-2/0404 中性 | 框到远处另一骑手 1101,180,1126,238 | 同一骑手 1100,178,1123,243 |
| 2-2/0334 不存在目标 | `<box>none</box>` → absent | 同样 `<box>none</box>` → absent |
| 2-2/0334 细描述 | 与中性同框 | 与中性同框 |

结论：这批监控帧上 4B 没有可测出的定位优势，CPU 慢约 7 倍；细描述不改变定位结果。
官方 benchmark 提升不能外推到小目标监控场景。原始结果 `assets/m6_s6_20e_wevisone_4b_probe.json`。
## 与 Qwen3-VL-4B 的同任务对比（2026-11-04）

同一批帧、同一句 `person riding a two-wheeler`：WeVisOne-2B IoU 0.482/0.461，WeVisOne-4B 0.524/0.479，
Qwen3-VL-4B 0.344/0.354 且会多框；1-1/0350 上 Qwen 首次输出重复、加 stop 后目标框与 WeVisOne 几乎重合但多 3 个误框。
Qwen 速度快是因为跑在 GPU（0.3–5.3 s），WeVisOne 这几轮在 CPU（23–193 s），不能直接比速度。
结论：定位选 WeVisOne，属性仍用 Qwen；两者分工。