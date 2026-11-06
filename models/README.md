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


## RF-DETR Small / Medium / Large（小模型管理）

TrajNexa 的 RF-DETR 使用**独立 PyTorch 推理适配器**，不复用 D-FINE 的 ONNX 双输入协议。
三个变体作为**独立检测器**注册，可以在模型管理页和模型测试页随时切换，也可一次选多个进行同图对比。
权重不入库，未安装对应权重或依赖时显示“未就绪”，不会阻止其他检测器运行。

| 模型 | 注册 ID | PyTorch 类 | 原生输入尺寸 | 配置中的权重相对路径 |
|---|---|---|---|---|
| Small | `rfdetr-s` | `RFDETRSmall` | 512×512 | `models/rfdetr/rf-detr-small.pth` |
| Medium | `rfdetr-m` | `RFDETRMedium` | 576×576 | `models/rfdetr/rf-detr-medium.pth` |
| Large | `rfdetr-l` | `RFDETRLarge` | 704×704 | `models/rfdetr/rf-detr-large-2026.pth` |

> 注意：RF-DETR 官方文档当前 Large 权重版本使用 `rf-detr-large-2026.pth`。
> 旧版 `rf-detr-large.pth` 可能属于不同架构版本，**不能**直接改名冒充。
> 实际使用权重时须保证模型类、软件包版本和权重来源相匹配。

### 安装与准备本地权重

在可信任的联网机器上执行：

```bash
cd backend
python -m pip install -r requirements-rfdetr.txt
python - <<'PY'
import os
from pathlib import Path
from rfdetr import RFDETRSmall, RFDETRMedium, RFDETRLarge

# 官方默认缓存地址为 ~/.roboflow/models，RF_HOME 可以覆盖。
folder = Path("models/rfdetr").resolve()
folder.mkdir(parents=True, exist_ok=True)
os.environ["RF_HOME"] = str(folder)

for cls in (RFDETRSmall, RFDETRMedium, RFDETRLarge):
    print("准备", cls.__name__)
    cls()  # 第一次构造时，官方库自动下载相应预训练权重

print("本地缓存的权重：")
for p in folder.rglob("*.pth"):
    print(p)
PY
```

检查上面列出的实际文件名，确保与 `backend/config/detectors.json` 一致。
如果官方发布的权重文件名称变更，应该校验其模型版本后**修改配置路径**，而不是直接重命名不匹配的旧权重。
API/Worker 推理加载使用**本地绝对文件路径**，运行期间不负责自动下载。

### Docker 与切换

默认镜像继续使用 D-FINE/ONNX，不默认安装 RF-DETR/PyTorch。
要启用 RF-DETR 推理，请在仓库根目录执行：

```bash
docker compose -f compose.yaml -f compose.rfdetr.yaml up --build -d
```

- 在“小模型管理”中点“重载配置”，确认对应模型显示“启用”，随后点击“测试”或“设默认”。
- “模型测试”支持 D-FINE 和 RF-DETR 三个变体的同图对比（最多同时选择 5 个小模型）。
- 点击“设默认”只改变浏览器实验偏好，不会修改正式研判任务的检测器；主系统服务端默认仍为 `dfine-n`。
- 如果某型号显示“未就绪”，检查本地文件路径、PyTorch/RF-DETR 依赖、模型变体和设备是否匹配。

### 契约与限制

- `model_variant` 是独立参数：`small`、`medium`、`large`，对应固定原生输入尺寸；
  新建检测器时会校验型号、分辨率、`.pth`、引擎和任务类型，防止误用 Small 解析 Medium/Large。
- 三个预置模型都是 COCO 80 类的普通目标检测模型；暂不包含分割、自定义类别检测。
- 输出的框和类别仍只是待核实候选，不产生身份确认。
- GPU/CUDA 需要安装匹配驱动的 PyTorch 构建；CPU 大模型推理可能较慢。
- 代码和模型权重许可证需分别核验，当前官方将 RF-DETR Small/Medium/Large 检测权重归入 Apache 2.0。
- 回归可执行 `cd backend && pytest -q`；真实模型正确率和吞吐量必须在部署设备和合法测试集上另行验证。
