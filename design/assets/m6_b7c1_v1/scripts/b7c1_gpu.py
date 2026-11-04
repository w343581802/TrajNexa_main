import json, re, sys, time, torch
from pathlib import Path
from PIL import Image
sys.path.insert(0, r"G:\\MyProject\\公益项目\\TrajNexa\\TrajNexa_main\\backend")
from wevisone_vl.engine import WeVisOneVL

ROOT = Path(r"G:\\tools\\b7c1")
ROWS = {r["id"]: r for r in json.loads((ROOT/"annotations.json").read_text(encoding="utf-8"))}
IDS = ["O02_2-1_0285_person","O01_2-2_0334_person","O03_2-2_0404_person",
       "O05_1-1_0350_person","O07_2-1_0400_person","O09_1-1_0450_person"]

PROMPT_JSON = ("只描述画面中确实可见的内容。输出一个 JSON 对象，只包含这些字段："
               "garment_color、garment_type、vehicle_color、vehicle_type、companion_present。"
               "看不到同伴写 \"no\"；被遮挡或无法判断写 \"unknown\"；其余字段无法确定也写 \"unknown\"。"
               "不要猜测，不要解释。")
PROMPT_TEXT = ("用一句话描述画面里的人：穿什么颜色、什么类型的衣服，骑或开什么颜色、什么类型的车，"
               "是否还有别人和他一起。看不清的地方直接说看不清。")

def refs_plain(raw):
    t = re.sub(r"<ref>(.*?)</ref>\s*<box>.*?</box>", r"\1", raw or "", flags=re.S)
    return re.sub(r"<[^>]+>", "", t).strip()

print("torch", torch.__version__, "cuda", torch.version.cuda, "avail", torch.cuda.is_available(), flush=True)
model = WeVisOneVL(r"G:\\tools\\wevisone-models\\2B", backend="transformers", device="cuda", dtype="bfloat16")
print("loaded", torch.cuda.memory_allocated()/1e9, "GB", flush=True)

out = []
for rid in IDS:
    row = ROWS[rid]
    image = Image.open(ROOT/row["image"]).convert("RGB")
    rec = {"id": rid, "crop": row["crop"]}
    for key, prompt in (("json", PROMPT_JSON), ("text", PROMPT_TEXT)):
        torch.cuda.reset_peak_memory_stats()
        t = time.time()
        gen = model.predict(image, task="freeform", query=prompt, decode_matting=False, max_new_tokens=192)
        rec[key] = {"raw": gen.response, "plain": refs_plain(gen.response), "latency_s": round(time.time()-t,1)}
    rec["peak_gb"] = round(torch.cuda.max_memory_allocated()/1e9, 2)
    out.append(rec)
    print(json.dumps({"id": rid, "peak_gb": rec["peak_gb"], "json": rec["json"]["plain"][:80], "text": rec["text"]["plain"][:80]}, ensure_ascii=False), flush=True)
(ROOT/"wevisone_gpu_v1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
