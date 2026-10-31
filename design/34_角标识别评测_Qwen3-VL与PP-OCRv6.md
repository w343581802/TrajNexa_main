# 34 角标识别评测：Qwen3-VL 2B（Ollama） vs PP-OCRv6

> 2026-10-31 · 目的：判断「本地视觉大模型」在主系统角标读取这件事上值不值得用，
> 值多少、用在哪一步。所有数字都能用 `backend/scripts/overlay_eval.py` 复跑。

## 1. 为什么要测

主系统的「录像资源」靠画面角标拿到摄像头名称和时间（[03](03_M1b_角标OCR与多帧一致性_实施验证.md)、
[25](25_路径输入与角标识别封装.md)），一直只有 PP-OCRv6 一条路。它的问题是**依赖固定 ROI**：
名称/时间条带的位置一换、或同一画面里还有省市水印，就得再调一轮 ROI（见 09–15 这一串实验）。
Qwen3-VL 这类模型不依赖固定 ROI，理论上能一次看全画面，所以要先在同一批真实帧上量出可比数字。

## 2. 环境

| 项 | 值 |
| --- | --- |
| Ollama | 0.40.13（便携版解压到 `G:\tools\ollama`，手工 `ollama serve`，不装系统服务） |
| 模型 | `qwen3-vl:2b`，1.9 GB，存 `G:\tools\ollama\models` |
| 显卡 | RTX 3060 Laptop 6 GB，实测常驻 3.4 GB、显存峰值 4.8 GB、GPU 利用率 90–100% |
| PP-OCRv6 | 复用主系统 `app.overlay_ocr`（`read_overlay_texts` + `parse_overlay_rows`），跑在 `trajnexa_main-worker` 镜像里 |

## 3. 数据

21 张真实抽帧（`storage/recording-frames/*-v2.jpg`，1920×1080），来自 2 个案件 51 段录像里
已经出过帧的那些。期望值取台账的 `camera_name` / `start_local`。

**期望值本身不是金标准**：台账名称有一部分就是从文件名或 OCR 来的。这次就撞到三例台账与画面
不一致，其中 `3-D-…E41栋-向西` 放大 4 倍后能看到 `D` 后还有一个 `X`（与车身暗边重叠，很容易漏），
所以那一帧「PP-OCRv6 与台账一致、Qwen3-VL 不一致」其实是**模型读对了**。下面同时给
「按台账算」和「人工核过的口径」。

## 4. 结果（21 帧，同一份清单）

| 配置 | 名称全等 | 近似(≥0.9) | 错 | 时间命中(±2s) | 名称耗时中位 |
| --- | --- | --- | --- | --- | --- |
| PP-OCRv6（项目默认，原图） | **14** | 1 | 6 | 12/21 | **1.38 s** |
| Qwen3-VL 原图 @1280 | **14** | 4 | 3 | **18/21** | 2.19 s |
| Qwen3-VL 下缘 ROI @1280 | **14** | 5 | 2 | 0/21 | 2.49 s |
| Qwen3-VL 右下 ROI @1280 | 12 | 6 | 3 | 0/21 | 2.34 s |
| Qwen3-VL 原图 1920 + ctx16k | 10 | 5 | 6 | 19/21 | 6.0 s |

逐帧并集：**两个引擎的「名称全等」并集是 17/21**（PP 对 Qwen 错的 3 例、Qwen 对 PP 错的 3 例，
互不重叠）。也就是说两条路互补，而不是谁替代谁。

## 5. 结论

1. **名称读得一样准（14/21），但错法不同。** PP-OCRv6 的错主要是「读不出」（5 例空），
   Qwen3-VL 的错主要是「读错一点」（`E40栋` → `40F`、把上一帧的名称复述过来）。
   对「人工核对」这个用法，前者的空比后者的错更好处理；对「不许漏」的场景则相反。
2. **时间上 Qwen3-VL 明显更好：18/21 vs 12/21。** PP 有 9 帧解析不出时间（ROI 没覆盖到或
   字形切错），Qwen 只有 2 帧偏差超过 2 秒。
3. **缩图到 1280 是关键：又快又准。** 1920 原图切块后 5.7k–8.8k tokens，默认 `num_ctx`
   直接 400 (`exceeds the available context size`)；缩到 1280 后延迟从 6.0 s 降到 2.2 s，
   全等从 10 提到 14。
4. **ROI 只对名称有用，对时间有害。** 裁下缘条带把「错」从 3 降到 2，但时间命中直接掉到 0
   ——时间条带在画面上缘，被裁掉了。所以正确做法是**名称用下缘 ROI、时间用原图**，而不是一刀切。
5. **仍不适合做权威来源。** 2B 模型有明确幻觉（第 21 帧复述了第 20 帧的名称）与漏字，
   输出只能进「建议 / 待人工确认」，与 C6「人工结论只进不退」一致。

## 6. 落地建议

- 默认引擎不动，仍用 PP-OCRv6；在「识别不出」（name 为空）时用视觉模型补一票，标成
  `suggestion` 而不是 `confirmed`。
- 时间优先用视觉模型（18/21 对 12/21），但同样要保留原 OCR 结果做交叉，不一致时提示人工。
- 名称读取的推荐配置：缩到 1280 宽 + 下缘 ROI + `num_ctx ≥ 8192` + `temperature 0` + 严格提示词
  （显式要求忽略「广西桂林临桂」这类省市水印，否则水印会被读进名称里）。
- 服务端接入前先解决部署：便携版 Ollama 不随系统启动，需要写进部署脚本或 compose 服务。

## 7. 复跑

```powershell
# 1) 生成清单（要 api 在跑）
python backend/scripts/overlay_eval.py manifest --api http://127.0.0.1:8010 \
    --out storage/validation/overlay_manifest.json

# 2) PP-OCRv6（在 OCR 镜像里）
docker compose run --rm --no-deps `
    -v "<repo>/backend:/srv" -v "<repo>/storage:/storage" worker `
    python scripts/overlay_eval.py run --engine ppocr `
    --manifest /storage/validation/overlay_manifest.json `
    --frames-dir /storage/recording-frames --out /storage/validation/ppocr_eval.json

# 3) Qwen3-VL（宿主机，Ollama 需已启动）
python backend/scripts/overlay_eval.py run --engine qwen3vl --model qwen3-vl:2b `
    --roi none --scale 1280 --manifest storage/validation/overlay_manifest.json `
    --out storage/validation/qwen3vl_full_1280.json
```

`run` 也支持 `--roi bottom|bottom-right`、`--limit`、`--num-ctx`；结果 JSON 里每帧都带
原始文本、判定、相似度、耗时，便于逐帧比对。清单只存文件名，所以宿主机和容器里都能跑。
