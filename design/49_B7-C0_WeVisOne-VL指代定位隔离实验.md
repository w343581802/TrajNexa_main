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
