import json, re, sys, time
from pathlib import Path
from PIL import Image
sys.path.insert(0, r"G:\\MyProject\\公益项目\\TrajNexa\\TrajNexa_main\\backend")
from wevisone_vl.engine import WeVisOneVL
ROOT = Path(r"G:\\tools\\b7c1")
ROWS = json.loads((ROOT/"annotations.json").read_text(encoding="utf-8"))
model = WeVisOneVL(r"G:\\tools\\wevisone-models\\2B", backend="transformers")
out = []
for row in ROWS:
    image = Image.open(ROOT/row["image"]).convert("RGB")
    t = time.time()
    gen = model.predict(image, task="freeform", query="describe the image with bounding boxes in detail", decode_matting=False, max_new_tokens=192)
    plain = re.sub(r"<ref>.*?</ref>\s*<box>.*?</box>", "", gen.response or "", flags=re.S).strip()
    out.append({"id": row["id"], "raw": gen.response, "plain": plain, "latency_s": round(time.time()-t,1)})
    print(row["id"], repr(plain[:100]), flush=True)
(ROOT/"wevisone_caption_v1.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
