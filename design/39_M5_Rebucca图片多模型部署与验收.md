> **已废弃（2026-11-01）**：Rebucca sidecar 已移除，检测在 api/worker 进程内完成，
> 引擎层见 [42_检测引擎移植.md](42_检测引擎移植.md)。`M5_REBUCCA_*` 环境变量与
> `backend/scripts/rebucca_image_server.py` 均已删除；本文仅作变更历史。

# 39｜M5 Rebucca 图片多模型联调（Windows / TrajNexa Demo）

> 2026-11-01。**代码/接口与模拟测试不等于真实权重验收**。与 38 架构配套的第一批图片实施。
>
> 目标：**复用 Rebucca 已有的 `EngineFactory` 与 ONNX/OpenVINO/YOLO 检测实现**；TrajNexa 在“添加目标要素”入口上同图选择多个小模型并显示候选框。用户点选后从**原始图片**裁剪 ROI，交给已配置的 Qwen3-VL 等视觉模型描述；允许修改本次提示词。现阶段**不启动多摄像头历史录像任务调度**。

## 1. 架构与运行边界

```text
TrajNexa 浏览器
  └─ AddElementModal：截图 → Rebucca 模型列表 → 选择多个检测模型 → 候选框
       │                      │
       │                      ▼
       └── TrajNexa FastAPI：案件与媒体归属/文件 SHA256/只读解码
                                  │ HTTP(JSON+JPEG)，可选 token
                                  ▼
              独立 Windows Rebucca Python 环境：rebucca_image_server.py
                   加载已配置 Rebucca EngineFactory（启动时仅加载一份）
                   /models、/health、/detect；HTTPServer 串行推理
                       ├─ D-FINE / ONNX / OpenVINO
                       ├─ YOLO / 其他 Rebucca 支持的检测器
                       └─ 每个模型独立来源/标签/分数/框；不跨模型去重
                                  │
                                  ▼
                     TrajNexa 归一检测候选（不写观测）
                                  │ 点击某一框
                                  ▼
                     已有 M4 /roi-features
                     原图裁剪 + VLM（按目标类型选择提示词或本次临时覆写）
                                  │
                                  ▼
                     人工选择“保存为目标要素”后才进入既有 M4a 观测
```

- Rebucca Python 环境只加载推理代码，不导入 Django ORM 和 Rebucca 报警业务；源文件位于它自己的路径，TrajNexa 不需安装 Rebucca 依赖。
- `backend/app/m5_rebucca_contract.py` 统一像素框；`backend/app/m5_rebucca_client.py` 是唯一图片 HTTP 适配器；`backend/app/m4_api.py` 复用现有案件图片验证，不新增第二套截图上传/媒体存储接口。
- 每次请求需显式选择 `model_ids`；只调用本机模型服务，默认关闭。无法解析/服务失败不会写任何身份结论。
- 模型检测 API 没有返回完整历史视频 PTS；不得将它当作视频轨迹证据或跨摄像头跟踪已完成。

## 2. 启动服务（先仅测试一款 D-FINE，第二款模型按本机实际配置追加）

### 2.1 前置环境

- Rebucca 仓库已安装、按其 README 在 Windows 虚拟环境安装 requirements（所需 ONNX Runtime / OpenVINO / OpenCV 等）。
- TrajNexa 已下载本分支代码、前端/后端运行正常；如走 Docker Compose，`.env` 已提供给 API 服务。
- 测试图片为有授权或脱敏的静态图片；模型权重和密钥不提交 Git。
- Rebucca 模型 `model_file` 和 `labels_source` 使用 Rebucca 内置 `app/utils/seed_defaults.py` 的 COCO 80 类顺序；**类别映射的键是中文原始标签**。

示例本地配置（例如 `D:/models/m5-image.json`，实际 Rebucca 目录自行修改）：

```json
{
  "detectors": [
    {
      "id": "dfine-n",
      "name": "Rebucca D-FINE N",
      "model_file": "static/upload/weight/dfine/dfine_n_coco_opset17_fp32_static.onnx",
      "algorithm_type": "dfine",
      "task_type": "detect",
      "inference_engine": "onnxruntime",
      "device": "cpu",
      "labels_source": "rebucca_coco80_cn",
      "conf_threshold": 0.35,
      "class_map": {
        "人": "person",
        "汽车": "vehicle",
        "公交车": "vehicle",
        "卡车": "vehicle",
        "自行车": "vehicle",
        "摩托车": "vehicle"
      }
    }
  ]
}
```

要做同图多模型对照，可在 `detectors` 再添加一项配置，指向**本机已经通过 Rebucca 测试**的 YOLO ONNX/OpenVINO 权重。选择正确的 `algorithm_type`、`inference_engine`、`labels` / `labels_file` 与对应 `class_map`；不同模型的标签不应擅自按相同数字 ID 映射。

### 2.2 直接在宿主机运行 TrajNexa（只有 loopback）

在已激活 Rebucca Python 环境的 PowerShell：

```powershell
cd D:\TrajNexa_main
python .\backend\scripts\rebucca_image_server.py `
  --rebucca-root "D:/Rebucca" `
  --config "D:/models/m5-image.json" `
  --bind 127.0.0.1 --port 8766
```

TrajNexa FastAPI 环境：

```env
M5_REBUCCA_ENABLED=true
M5_REBUCCA_BASE_URL=http://127.0.0.1:8766
M5_REBUCCA_TOKEN=
M5_REBUCCA_TIMEOUT_SECONDS=60
```

### 2.3 Windows 宿主机的 Rebucca + Docker 内 TrajNexa（本地 Demo）

Docker 内 `127.0.0.1` 指向**容器**；使用 `host.docker.internal` 才能访问 Windows 上的服务。由于服务默认只绑定 Windows 回环地址，不一定能被容器访问，这种模式要显式绑定可达接口并配置共享密钥和 Windows 防火墙限制：

```powershell
# 请自行生成不可预测的长 Token，不要把实际值提交 Git
$env:M5_REBUCCA_TOKEN = ([guid]::NewGuid()).ToString("N")
python .\backend\scripts\rebucca_image_server.py `
  --rebucca-root "D:/Rebucca" `
  --config "D:/models/m5-image.json" `
  --bind 0.0.0.0 --port 8766
```

TrajNexa 项目私有 `.env` 中设置同一 Token：

```env
M5_REBUCCA_ENABLED=true
M5_REBUCCA_BASE_URL=http://host.docker.internal:8766
M5_REBUCCA_TOKEN=<与上面完全相同的随机Token>
M5_REBUCCA_TIMEOUT_SECONDS=60
```

仅在**本机可信网络/严格防火墙限制**下这样使用；不要开放 8766 到局域网/公网。生产部署应改为受控内部网络与正式鉴权，而非依赖 Demo token。必须重建/重启使用 `.env` 的 API 服务才能看到配置。

## 3. 图片测试流程

1. 启动 Rebucca 图片服务后，打开 TrajNexa → 案件 → 添加目标要素 → 选择静态截图。
2. 在识别引擎选 **Rebucca** → 勾选一个或多个模型 → “重新识别”。可“刷新模型”查看当前服务注册的检测模型。
3. 查看不同模型的候选框，按来源标注模型名、类别和置信分数；“类别待核实”不得当作车辆自动确证。
4. 点击某一候选框（或手动框选），由已有 `/roi-features` 调用大模型描述。针对车辆默认提示词描述车身/轮数/遮挡而非人员衣着；对选中 ROI **先从原图裁剪，再对裁剪图按需缩放**。
5. 展开“调整大模型提示词”，填写本次实验的提示词，再点击“用当前提示词重新分析选中区域”。它只影响本次请求，**不会保存为版本化模板**。未来的 Prompt 工作台见 M5 规划。
6. 人工核实后才点击保存目标要素；模型检测本身不会写正式观测或轨迹。

错误/降级：未配置 Rebucca 时模型列表明确空，旧 D-FINE / Qwen 与手动框选仍可用；Rebucca 服务超时/错误必须显示为失败，不能声称“无目标”。多个模型在 6–8GB GPU 可串行运行，不要求全部同时驻留 GPU；可先 D-FINE CPU + 视觉模型 GPU。

## 4. 需要记录的验证证据

- 固定样本集：至少含人员/轿车/卡车/公交/两轮车、低照度、红外、远距离小目标、骑行者遮挡；记录图片哈希。
- 同一图片同一参数比较 Rebucca 多模型：每类实际正确目标数量、漏检/误检、框定位、耗时、模型名称/权重哈希。没有人工标注真值就只报告候选和耗时。
- 与旧 D-FINE、Qwen3-VL 2B/4B 同图对照；Rebucca 检出后测 ROI 描述前后差异（原图裁剪 vs 缩图裁剪）。
- 失败路径：模型不可用、无模型、模型运行失败、超时、坏框、服务返回不一致、案件外图片拒绝、无网络连接下人工框选仍成功。
- 将 Windows 的真实测试步骤和输出另记为实验日志，不能用 CI 的 mock 结果替代。

## 5. 当前完成范围和待办

第一批：独立 Rebucca 图片服务 + TrajNexa 图片适配器 + UI 模型选择/多模型来源 + 原图 ROI / 车辆 Prompt + 单次提示词调整 + mock 测试。

仍待实现：正式 Prompt 模板持久化/发布回退；逐帧历史视频 Worker；多 CameraAnalysisJob 调度与状态恢复；推理池按 GPU 资源执行；跨镜头证据关联。详见 38 设计文档。
