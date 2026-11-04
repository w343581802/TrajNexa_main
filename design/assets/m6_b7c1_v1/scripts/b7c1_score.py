import json, re, statistics
from pathlib import Path

ROOT = Path(r"G:\\tools\\b7c1")
ROWS = {r["id"]: r for r in json.loads((ROOT/"annotations.json").read_text(encoding="utf-8"))}

COLORS = [("light blue","blue"),("dark green","green"),("dark grey","grey"),("dark gray","grey"),
          ("浅蓝","blue"),("淡蓝","blue"),("墨绿","green"),("深绿","green"),("米白","white"),
          ("white","white"),("白","white"),("black","black"),("黑","black"),("grey","grey"),("gray","grey"),("灰","grey"),
          ("red","red"),("红","red"),("yellow","yellow"),("黄","yellow"),("blue","blue"),("蓝","blue"),
          ("green","green"),("绿","green"),("purple","purple"),("紫","purple"),("dark","black"),("深色","black")]
GARMENT_TYPES = [("t-shirt","t_shirt"),("tshirt","t_shirt"),("T恤","t_shirt"),("短袖","t_shirt"),
                 ("jacket","outerwear"),("外套","outerwear"),("夹克","outerwear"),("coat","outerwear"),
                 ("cardigan","outerwear"),("开衫","outerwear"),("sweater","outerwear"),("毛衣","outerwear"),("卫衣","outerwear"),
                 ("shirt","shirt"),("衬衫","shirt"),("上衣","shirt")]
VEHICLE_TYPES = [("tricycle","tricycle"),("三轮","tricycle"),("three-wheel","tricycle"),
                 ("scooter","two_wheeler"),("踏板","two_wheeler"),("电动车","two_wheeler"),("电瓶车","two_wheeler"),
                 ("motorcycle","two_wheeler"),("摩托","two_wheeler"),("motorbike","two_wheeler")]

def norm_color(text):
    if not text: return "unknown"
    low = str(text).lower()
    if "unknown" in low or "看不清" in low or "无法" in low: return "unknown"
    for key, val in COLORS:
        if key in low: return val
    return "unknown"

def norm_garment(text):
    low = str(text or "").lower()
    if "unknown" in low or "看不清" in low: return "unknown"
    for key, val in GARMENT_TYPES:
        if key in low: return val
    return "unknown"

def norm_vehicle(text):
    low = str(text or "").lower()
    if "not_applicable" in low or low.strip() in ("none","no") or "无车" in low or "步行" in low: return "not_applicable"
    if "unknown" in low or "看不清" in low: return "unknown"
    for key, val in VEHICLE_TYPES:
        if key in low: return val
    return "unknown"

def norm_companion(text):
    low = str(text or "").lower()
    if "unknown" in low or "看不清" in low or "无法判断" in low: return "unknown"
    if low.strip() in ("yes","true","是"): return "yes"
    if low.strip() in ("no","false","否"): return "no"
    if any(k in low for k in ["没有其他人","没有别人","只有一人","只有他一人","只有她一人","独自","单独","仅一人","画面中没有"]): return "no"
    if low.strip() in ("no","false"): return "no"
    if any(k in low for k in ["一起","同乘","后座","车前","带着","载着","两人","二人","怀里抱","抱着","companion"]): return "yes"
    return "unknown"

def refs_plain(raw):
    text = re.sub(r"<ref>(.*?)</ref>\s*<box>.*?</box>", r"\1", raw or "", flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()

def parse_json(text):
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m: return None
    try: return json.loads(m.group(0))
    except Exception: return None

def fields_from_json(text):
    body = parse_json(text)
    if not isinstance(body, dict): return None
    return {"garment_color": norm_color(body.get("garment_color")), "garment_type": norm_garment(body.get("garment_type")),
            "vehicle_color": norm_color(body.get("vehicle_color")), "vehicle_type": norm_vehicle(body.get("vehicle_type")),
            "companion_present": norm_companion(body.get("companion_present"))}

def fields_from_text(text):
    low = (text or "").lower()
    f = {"garment_color":"unknown","garment_type":"unknown","vehicle_color":"unknown","vehicle_type":"unknown","companion_present":"unknown"}
    gm = re.search(r"(wearing|wear|dressed in|穿|上衣|外套|jacket|shirt|top)[^.!?。，,]{0,40}", low)
    if gm:
        f["garment_color"] = norm_color(gm.group(0))
        f["garment_type"] = norm_garment(gm.group(0))
    if f["garment_type"] == "unknown":
        f["garment_type"] = norm_garment(low)
    vm = re.search(r"(riding|rides|on a|on an|骑|驾驶|骑着|坐着|scooter|motorcycle|电动车|摩托)[^.!?。，,]{0,40}", low)
    if vm:
        f["vehicle_type"] = norm_vehicle(vm.group(0))
        window = vm.group(0)
        if gm: window = window.replace(gm.group(0), "")
        f["vehicle_color"] = norm_color(window)
    if f["vehicle_type"] == "unknown":
        f["vehicle_type"] = norm_vehicle(low)
    f["companion_present"] = norm_companion(low)
    return f

FIELDS = ["garment_color","garment_type","vehicle_color","vehicle_type","companion_present"]

def score(rows, fields_of):
    stats = {f:{"n":0,"hit":0,"unknown_n":0,"unknown_hit":0,"halluc":0,"na_n":0,"na_hit":0} for f in FIELDS}
    for row in rows:
        truth = row["truth"]; parsed = fields_of(row)
        if parsed is None: continue
        for f in FIELDS:
            t = truth[f]; s = stats[f]; pv = parsed.get(f,"unknown")
            if t["status"] == "known":
                s["n"] += 1
                tv = t["value"]
                tvn = norm_color(tv) if "color" in f else (norm_garment(tv) if f=="garment_type" else (norm_vehicle(tv) if f=="vehicle_type" else norm_companion(tv)))
                if pv == tvn: s["hit"] += 1
            elif t["status"] == "unknown":
                s["unknown_n"] += 1
                if pv == "unknown": s["unknown_hit"] += 1
                else: s["halluc"] += 1
            elif t["status"] == "not_applicable":
                s["na_n"] += 1
                if pv in ("not_applicable","unknown"): s["na_hit"] += 1
    return stats

def summary(stats):
    out = {}
    for f,s in stats.items():
        out[f] = {"accuracy": round(s["hit"]/s["n"],3) if s["n"] else None, "known_n": s["n"],
                  "unknown_recall": round(s["unknown_hit"]/s["unknown_n"],3) if s["unknown_n"] else None,
                  "unknown_n": s["unknown_n"],
                  "hallucination": round(s["halluc"]/s["unknown_n"],3) if s["unknown_n"] else None}
    return out

def load(path, mode=None):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for rec in data:
        row = dict(ROWS[rec["id"]]); row["model"] = rec; out.append(row)
    return out

result = {}
for name, path in [("wevisone","wevisone_v1.json"),("qwen","qwen_v1.json")]:
    p = ROOT/path
    if not p.exists(): continue
    rows = load(p)
    for crop in ("person","context","all"):
        subset = [r for r in rows if crop=="all" or r["crop"]==crop]
        result[f"{name}_{crop}"] = {
            "json_ok": sum(1 for r in subset if fields_from_json(r["model"]["json"]["raw"]) is not None),
            "n": len(subset),
            "json_score": summary(score(subset, lambda r: fields_from_json(r["model"]["json"]["raw"]))),
            "text_score": summary(score(subset, lambda r: fields_from_text(refs_plain(r["model"]["text"]["raw"])))),
            "latency_json": round(statistics.mean([r["model"]["json"]["latency_s"] for r in subset]),1),
            "latency_text": round(statistics.mean([r["model"]["text"]["latency_s"] for r in subset]),1),
        }

cap_path = ROOT/"wevisone_caption_v1.json"
if cap_path.exists():
    cap = {r["id"]: r for r in json.loads(cap_path.read_text(encoding="utf-8"))}
    rows = []
    for rid, rec in cap.items():
        row = dict(ROWS[rid]); row["cap"] = rec; rows.append(row)
    for crop in ("person","context","all"):
        subset = [r for r in rows if crop=="all" or r["crop"]==crop]
        result[f"wevisone_caption_{crop}"] = {"n": len(subset),
            "text_score": summary(score(subset, lambda r: fields_from_text(refs_plain(r["cap"]["raw"])))),
            "latency_text": round(statistics.mean([r["cap"]["latency_s"] for r in subset]),1)}

(ROOT/"scores.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k:{"json_ok":v.get("json_ok"),"n":v["n"]} for k,v in result.items()}, ensure_ascii=False))
