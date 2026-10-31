# 33｜M4d VLM 实验适配器实施记录 V0.1

> 状态：实验分支代码已提交；测试待 CI/本地执行确认；尚未接入 HTTP、数据库、前端或真实模型。

## 目标与来源
- 参考 Rebucca `app/utils/LLMUtils.py` 的 OpenAI-compatible 图像调用方式；未直接复制上游源码。
- 实现 `backend/app/m4d_vlm.py` 的 `review_jpeg`，通过注入的兼容客户端调用，避免引入 Django 或 Rebucca 报警模块。
- 当前未新增 OpenAI SDK 依赖；真实调用时由上层显式提供已配置客户端。禁止向未获批准的远端服务发送真实监控图像。
- 源码参考仓库：https://github.com/feixintianxia/beixiaocai_rebucca/blob/main/app/utils/LLMUtils.py （镜像与原上游 commit 一致性待核对）。

## 边界
- 无 API 路由、无数据库写入、无任务调度；仅是纯调用适配器。
- 调用者必须先校验案件、授权、来源、隐私和留存策略；实验函数自身不声称提供权限隔离。
- 只接受字节层 JPEG 标记与大小限制；不等于完整图片解码/安全验证。
- 返回 completed/refused/empty/error、模型名、图像哈希、请求 ID、可用时的 token 使用信息；不自动生成确认观测或身份判定。
- 异常只返回异常类型，避免把可能包含密钥/请求数据的异常文本写入记录。
- 保留 M4b D-FINE、M4c 共享取帧器和 TrajNexa_Map 职责边界。

## 测试
```bash
cd backend
python -m pytest -q tests/test_m4d_vlm.py
```
使用 FakeClient，不访问网络、不需要模型权重、不证明真实 VLM 效果。需后续补充 SDK 集成、超时、费用、脱敏素材与拒判质量测试。

## 下一步
1. 确认单测在项目 CI 运行成功。
2. 增加上游来源版本、依赖与许可台账。
3. 设计任务授权与案件归属验证后，再添加实验 API。
4. 实测前先确认模型服务部署边界和隐私处理，不得公网开放未经认证的接口。
