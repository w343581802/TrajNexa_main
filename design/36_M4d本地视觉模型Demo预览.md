# 36｜M4d Demo 本地视觉模型预览

## 范围
M4c 已登记录像 → 指定偏移关键帧 → M4d 本地 OpenAI-compatible chat/completions → 只读视觉属性候选。前端“录像关键帧检查”面板新增“分析当前帧属性”按钮。

## 环境变量
- `M4D_ENABLED=true`：显式启用；默认关闭。
- `M4D_MODEL=<本地视觉模型名称>`：必填。
- `M4D_BASE_URL=http://127.0.0.1:11434/v1`：本地模型服务，必须为 localhost / loopback HTTP 并带端口；默认该值。
- `M4D_TIMEOUT_SECONDS=30`：请求超时，限制为 0–120 秒。

本地服务需支持 OpenAI 风格 `POST /v1/chat/completions`，接收 `image_url` 中的 JPEG data URL 并返回 JSON 字符串。容器部署时，127.0.0.1 指后端容器自身；模型服务需要位于同一网络命名空间，当前配置不支持访问其它主机。不开启时接口返回 503。

## 输出约束
仅接受 clothing_upper / clothing_lower / carried_item / visible_motion 四类属性候选；模型返回异常、拒绝、截断、非严格 JSON 时不生成有效候选。返回 `identity_established=false`、`requires_human_review=true`。不写入数据库、不改轨迹、不自动确认同一人。时间偏移为 seek 请求值，不是核实过的 PTS。

## 测试边界
CI 使用假客户端和模拟录像帧，不验证真实模型精度、视频编解码、现场部署连通性。后续应做真实模型和录像的人工验收。
