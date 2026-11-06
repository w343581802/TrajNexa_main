# models/ 本地权重说明

这个目录只放**本地**推理权重，不入库（`.gitignore` 已忽略 `models/`、`*.onnx`）。

## D-FINE（`/detect?engine=dfine`）

当前 demo 使用的是 COCO nano 版 ONNX：

```powershell
mkdir models
curl.exe -L --fail -o models/dfine.onnx `
  "https://huggingface.co/onnx-community/dfine_n_coco-ONNX/resolve/main/onnx/model.onnx"
(Get-FileHash models/dfine.onnx -Algorithm SHA256).Hash.ToLower()
```

- 文件：`models/dfine.onnx`（14.6 MB，fp32）
- SHA-256：`0f684f409618ee8a822410e754a29caa817d1aa16283ce89cad936d0a48e2f35`
- 接口契约（`backend/app/m4_detection.py`）：输入 `images [1,3,640,640] float32` +
  `orig_target_sizes [1,2] int64`，输出 `labels / boxes / scores`；COCO 类别里只映射
  person、bicycle、car、motorcycle、bus、truck 到 `person` / `vehicle`。
- 容器内路径固定为 `/models/dfine.onnx`（`compose.yaml` 把 `./models` 只读挂到 `/models`）。
- `.env` 需要同时设置 `M4B_ENABLED=true`、`M4B_MODEL_PATH`、`M4B_MODEL_SHA256`
  （哈希不匹配会返回 `m4b-model-digest-mismatch`，不会静默使用别的权重）。

## Qwen3-VL（`/detect?engine=qwen3vl`）

不在这个目录，走本机 Ollama：

```powershell
$env:OLLAMA_MODELS='G:\tools\ollama\models'
& 'G:\tools\ollama\app\ollama.exe' pull qwen3-vl:2b-instruct
& 'G:\tools\ollama\app\ollama.exe' serve      # 另开一个窗口常驻
```

`.env` 对应 `VISION_ENABLED=true`、`VISION_BASE_URL=http://host.docker.internal:11434`、
`VISION_MODEL=qwen3-vl:2b-instruct`。

> 注意：`qwen3-vl:2b`（thinking 变体）会把 token 全花在推理上、`content` 为空，
> 接口会返回 `m4d-model-thinking-only`，请使用 `*-instruct` 变体。


## RF-DETR Small（小模型管理中的 `rfdetr-s`）

RF-DETR 采用**独立 PyTorch 推理适配器**，不复用 D-FINE 的 ONNX 双输入/三输出契约。
此仓库预配置了 `rfdetr-s`，但没有提交任何权重：缺少依赖或权重时，
模型管理页会显示“未就绪”，不会阻止已安装的 D-FINE/YOLO 工作。

### 准备本地权重

在**可信任、有网络的开发机器**上执行（权重可能较大）：

```bash
cd backend
python -m pip install -r requirements-rfdetr.txt
python - <<'PY'
import os
from pathlib import Path
from rfdetr import RFDETRSmall

folder = Path("models/rfdetr").resolve()
folder.mkdir(parents=True, exist_ok=True)
os.environ["RF_HOME"] = str(folder)
RFDETRSmall(device="cpu")  # 仅准备权重；首次运行需要联网获取官方预训练权重
print("已缓存的权重：", [str(p) for p in folder.rglob("*.pth")])
PY
```

将找到的**官方 RF-DETR Small COCO 权重**放在
`backend/models/rfdetr/rf-detr-small.pth`（不能拿 Medium/Large 权重冒充 Small）。
若官方缓存文件不在该路径，复制文件至该路径，或核对
`backend/config/detectors.json` 中 `rfdetr-s.model_file` 的路径。
本地仅接受 `.pth`；无需也不会从 API 自动联网下载。

### 使用

- 默认 Docker 镜像继续使用 D-FINE，**不会额外安装 PyTorch/RF-DETR**。
  若需在容器中启用 RF-DETR，回到仓库根目录运行：
  `docker compose -f compose.yaml -f compose.rfdetr.yaml up --build -d`
  （不清空数据卷）。`backend/models` 会按已有配置挂载到 API/Worker。
- “小模型管理” → “重载配置”：确认 `rfdetr-s` 已就绪；然后可点击“设默认”
  或“测试”切到 RF-DETR，也能随时切回 `dfine-n`。
- “模型测试”页面可选 D-FINE 和 RF-DETR 对同一张图作并列比较，输出仍是待核实候选框。
- 这里的“设默认”是**浏览器中的测试偏好**，不会自动更改主系统任务的默认检测器。
  主系统默认仍由 `backend/config/model_capabilities.json`
  （及部署配置）控制，目前仍是 `dfine-n`。

### 使用范围与注意事项

- 仅适配 `RFDETRSmall` + 512×512 + COCO 80 类检测；
  其他变体、分割任务、微调类数变更需独立验证，不应直接改配置尺寸。
- PyTorch CPU 推理可能较慢；GPU/CUDA 需要使用与设备驱动兼容的 PyTorch 环境。
- 权重和模型许可证应分别核验；RF-DETR Small 官方预训练系列为 Apache 2.0，
  具体条件以其发布版本的 LICENSE 为准。
- 工作台正常业务默认不变，模型输出不得自动等同于人员身份确认。
- 通过 `cd backend && pytest -q` 回归；真实权重推理需要在实际设备另行验收。
