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
