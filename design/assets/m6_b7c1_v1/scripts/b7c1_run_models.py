import argparse, base64, json, sys, time
from pathlib import Path
from PIL import Image

ROOT = Path(r"G:\\tools\\b7c1")
ROWS = json.loads((ROOT / "annotations.json").read_text(encoding="utf-8"))

PROMPT_JSON = ("只描述画面中确实可见的内容。输出一个 JSON 对象，只包含这些字段：" 
               "garment_color、garment_type、vehicle_color、vehicle_type、companion_present。" 
               "看不到同伴写 \"no\"；被遮挡或无法判断写 \"unknown\"；其余字段无法确定也写 \"unknown\"。" 
               "不要猜测，不要解释。")
PROMPT_TEXT = ("用一句话描述画面里的人：穿什么颜色、什么类型的衣服，骑或开什么颜色、什么类型的车，"
               "是否还有别人和他一起。看不清的地方直接说看不清。")

def strip_refs(text):
    import re
    return re.sub(r"<ref>.*?</ref>\s*<box>.*?</box>", "", text or "", flags=re.S).strip()

def run_wevisone(model_path, tag):
    sys.path.insert(0, r"G:\\MyProject\\公益项目\\TrajNexa\\TrajNexa_main\\backend")
    from wevisone_vl.engine import WeVisOneVL
    model = WeVisOneVL(model_path, backend="transformers")
    out = []
    for row in ROWS:
        image = Image.open(ROOT / row["image"]).convert("RGB")
        rec = {"id": row["id"], "crop": row["crop"], "category": row["category"]}
        for key, prompt in (("json", PROMPT_JSON), ("text", PROMPT_TEXT)):
            t = time.time()
            gen = model.predict(image, task="freeform", query=prompt, decode_matting=False, max_new_tokens=192)
            rec[key] = {"raw": gen.response, "plain": strip_refs(gen.response), "latency_s": round(time.time()-t,1)}
        out.append(rec)
        print(json.dumps({"id": row["id"], "json_plain": rec["json"]["plain"][:120], "text_plain": rec["text"]["plain"][:120]}, ensure_ascii=False), flush=True)
    (ROOT / f"wevisone_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

def run_qwen(tag):
    import requests
    out = []
    for row in ROWS:
        data = base64.b64encode((ROOT / row["image"]).read_bytes()).decode()
        rec = {"id": row["id"], "crop": row["crop"], "category": row["category"]}
        for key, prompt in (("json", PROMPT_JSON), ("text", PROMPT_TEXT)):
            payload = {"model": "qwen3-vl:4b-instruct", "stream": False, "think": False,
                       "options": {"temperature": 0, "num_predict": 256},
                       "messages": [{"role": "user", "content": prompt, "images": [data]}]}
            t = time.time()
            r = requests.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=300).json()
            content = r["message"]["content"]
            rec[key] = {"raw": content, "plain": content, "latency_s": round(time.time()-t,1)}
        out.append(rec)
        print(json.dumps({"id": row["id"], "json_plain": rec["json"]["plain"][:100]}, ensure_ascii=False), flush=True)
    (ROOT / f"qwen_{tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--model", required=True); ap.add_argument("--path"); ap.add_argument("--tag", default="v1")
    a = ap.parse_args()
    if a.model == "qwen":
        run_qwen(a.tag)
    else:
        run_wevisone(a.path, a.tag)
