import argparse, base64, json, re, sys, time
from pathlib import Path
from PIL import Image

ROOT = Path(r"G:\\tools\\b7c1")
ROWS = json.loads((ROOT/"annotations_reviewed.json").read_text(encoding="utf-8"))

PROMPT_JSON = ("Describe only what is visibly present. Output a JSON object with exactly these fields: "
               "garment_color, garment_type, vehicle_color, vehicle_type, companion_present. "
               "Write \"no\" only if no companion is visible; write \"unknown\" if occluded or cannot be determined; "
               "write \"unknown\" for any other field you cannot determine. Do not guess. Do not explain.")
PROMPT_TEXT = ("In one sentence, describe the person's clothing color and type, the vehicle's color and type, "
               "and whether anyone is accompanying them. Say \"cannot tell\" when it is unclear.")

def refs_plain(raw):
    t = re.sub(r"<ref>(.*?)</ref>\s*<box>.*?</box>", r"\1", raw or "", flags=re.S)
    return re.sub(r"<[^>]+>", "", t).strip()

def run_wevisone(model_path):
    sys.path.insert(0, r"G:\\MyProject\\公益项目\\TrajNexa\\TrajNexa_main\\backend")
    from wevisone_vl.engine import WeVisOneVL
    model = WeVisOneVL(model_path, backend="transformers", device="cuda", dtype="bfloat16")
    out = []
    for row in ROWS:
        image = Image.open(ROOT/row["image"]).convert("RGB")
        rec = {"id": row["id"], "crop": row["crop"], "category": row["category"]}
        for key, prompt in (("json", PROMPT_JSON), ("text", PROMPT_TEXT)):
            t = time.time()
            gen = model.predict(image, task="freeform", query=prompt, decode_matting=False, max_new_tokens=192)
            rec[key] = {"raw": gen.response, "plain": refs_plain(gen.response), "latency_s": round(time.time()-t,1)}
        out.append(rec)
        print(json.dumps({"id": row["id"], "json": rec["json"]["plain"][:90]}, ensure_ascii=False), flush=True)
    (ROOT/"wevisone_en_v1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

def run_qwen():
    import requests
    out = []
    for row in ROWS:
        data = base64.b64encode((ROOT/row["image"]).read_bytes()).decode()
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
        print(json.dumps({"id": row["id"], "json": rec["json"]["plain"][:80]}, ensure_ascii=False), flush=True)
    (ROOT/"qwen_en_v1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--model", required=True); ap.add_argument("--path")
    a = ap.parse_args()
    if a.model == "qwen": run_qwen()
    else: run_wevisone(a.path)
