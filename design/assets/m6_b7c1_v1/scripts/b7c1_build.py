from PIL import Image, ImageDraw
from pathlib import Path
base = Path(r"C:\\Users\\windeclipse\\AppData\\Local\\Temp\\qwen-test\\mk\\s6b")
out = Path(r"G:\\tools\\b7c1"); out.mkdir(parents=True, exist_ok=True)
items = [
 ("O01_2-2_0334", "2-2", "0334", [462,234,543,287]),
 ("O02_2-1_0285", "2-1", "0285", [764,188,832,316]),
 ("O03_2-2_0404", "2-2", "0404", [1241,140,1279,175]),
 ("O04_2-2_0700", "2-2", "0700", [929,150,969,199]),
 ("O05_1-1_0350", "1-1", "0350", [1027,115,1105,246]),
 ("O06_2-2_0504", "2-2", "0504", [471,165,519,252]),
 ("O07_2-1_0400", "2-1", "0400", [1218,185,1273,282]),
 ("O08_1-1_0400", "1-1", "0400", [342,41,402,147]),
 ("O09_1-1_0450", "1-1", "0450", [322,26,382,135]),
 ("O10_2-2_0604", "2-2", "0604", [330,175,383,272]),
]
meta = []
for name, cam, frame, box in items:
    im = Image.open(base / cam / (frame + ".jpg")).convert("RGB")
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    person = im.crop((x1, y1, x2, y2))
    person.save(out / f"{name}_person.png")
    cx1 = max(0, int(x1 - 0.9 * w)); cx2 = min(im.width, int(x2 + 0.9 * w))
    cy1 = max(0, int(y1 - 0.15 * h)); cy2 = min(im.height, int(y2 + 0.35 * h))
    ctx = im.crop((cx1, cy1, cx2, cy2))
    ctx.save(out / f"{name}_context.png")
    meta.append({"id": name, "camera": cam, "frame": frame, "box": box,
                 "person_size": list(person.size), "context_box": [cx1, cy1, cx2, cy2],
                 "context_size": list(ctx.size)})
Path(out / "crops.json").write_text(__import__("json").dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

def sheet(files, cols, tile_w, tile_h, dest):
    rows = (len(files) + cols - 1) // cols
    s = Image.new("RGB", (cols * tile_w, rows * tile_h), "white")
    d = ImageDraw.Draw(s)
    for i, p in enumerate(files):
        im = Image.open(p).convert("RGB")
        im.thumbnail((tile_w - 6, tile_h - 22))
        x = (i % cols) * tile_w; y = (i // cols) * tile_h
        s.paste(im, (x + 3, y + 20))
        d.text((x + 4, y + 4), p.stem.replace("_person", "").replace("_context", ""), fill="black")
    s.save(dest)

ids = [m["id"] for m in meta]
sheet([out / f"{i}_person.png" for i in ids], 5, 260, 300, out / "sheet_person.png")
sheet([out / f"{i}_context.png" for i in ids], 5, 300, 260, out / "sheet_context.png")
print("ok", len(meta))
