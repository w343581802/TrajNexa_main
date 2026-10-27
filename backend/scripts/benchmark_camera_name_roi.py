"""Position-aware camera OSD name benchmark. Experimental; no auto device binding."""
from __future__ import annotations
import argparse, hashlib, json, re, time, unicodedata
from pathlib import Path
from collections import defaultdict
import cv2
import numpy as np

PRESETS = {
    "right_bottom": (.47, .84, 1., 1.),
    "right_bottom_tight": (.54, .90, 1., 1.),
    "bottom_context": (0., .85, 1., 1.),
}

def canonical(s):
    return re.sub(r'[\s\-_:：·—－|]+', '', unicodedata.normalize('NFKC',s).casefold())

from scripts.camera_osd_name_quality import describe, summarize_name_rows


def code_hint(raw):
    return describe(raw)['quality'] == 'structured_candidate'

def crop(image, roi):
    h,w=image.shape[:2]
    x1,y1,x2,y2=[round(v*dim) for v,dim in zip(roi,(w,h,w,h))]
    if x2-x1<30 or y2-y1<15:raise ValueError('ROI too small')
    return image[y1:y2,x1:x2].copy(),[x1,y1,x2,y2]

def rows_from_prediction(pages):
    rows=[]
    for page in pages:
        data=getattr(page,'json',page)
        if callable(data):data=data()
        if not isinstance(data,dict):continue
        d=data.get('res',data)
        if not isinstance(d,dict):continue
        texts,scores=d.get('rec_texts',[]),d.get('rec_scores',[])
        polygons=d.get('rec_polys',d.get('dt_polys',[]))
        for i,text in enumerate(texts[:60]):
            s=str(text).strip()
            if not s:continue
            pts=None
            if i<len(polygons):
                arr=np.asarray(polygons[i])
                if arr.size>=8:pts=arr.reshape(-1,2).astype(float).round(1).tolist()
            rows.append(dict(text=s[:160],model_score=float(scores[i]) if i<len(scores) else None,
                             polygon_roi=pts,camera_code_hint=code_hint(s),
                             name_quality=describe(s)['quality'],
                             name_quality_reason=describe(s)['reason']))
    return rows

def summary(rows):
    return summarize_name_rows(rows)

def run(path, predictor=None, preview_dir=None):
    if not path.is_file():raise FileNotFoundError(path)
    image=cv2.imdecode(np.fromfile(str(path),np.uint8),cv2.IMREAD_COLOR)
    if image is None:raise ValueError('invalid image')
    if predictor is None:
        from paddleocr import PaddleOCR
        predictor=PaddleOCR(use_doc_orientation_classify=False,use_doc_unwarping=False,
                            use_textline_orientation=False,engine='paddle')
    results=[]
    for preset,roi in PRESETS.items():
        patch,rect=crop(image,roi)
        if preview_dir:
            preview_dir.mkdir(parents=True,exist_ok=True)
            cv2.imwrite(str(preview_dir/f'{preset}.png'),patch)
        scaled=cv2.resize(patch,None,fx=1.6,fy=1.6,interpolation=cv2.INTER_CUBIC)
        gray=cv2.cvtColor(scaled,cv2.COLOR_BGR2GRAY)
        enhanced=cv2.cvtColor(cv2.createCLAHE(clipLimit=2.,tileGridSize=(8,8)).apply(gray),cv2.COLOR_GRAY2BGR)
        for variant,frame in [('original',patch),('upscaled_clahe',enhanced)]:
            t=time.perf_counter()
            records=rows_from_prediction(predictor.predict(frame))
            ms=round(1000*(time.perf_counter()-t),1)
            for record in records:
                record.update(preset=preset,variant=variant,roi_pixels=rect,inference_ms=ms)
                results.append(record)
    return dict(experiment='PP-OCRv6_camera_name_ROI',source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                production_ocr_not_modified=True,media_not_modified=True,
                raw_ocr_rows=results,summary=summary(results))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('image',type=Path)
    p.add_argument('--output',type=Path)
    p.add_argument('--preview-dir',type=Path)
    a=p.parse_args()
    result=run(a.image,preview_dir=a.preview_dir)
    txt=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(txt+'\n',encoding='utf8')
    print(txt)

if __name__=='__main__':main()
