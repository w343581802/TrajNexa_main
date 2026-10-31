"""Minimal main-system HTTP API. No TrajNexa_Map tables or endpoints are reused."""
from hashlib import sha256
from datetime import datetime
from uuid import uuid4
import os

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Case, MediaAsset, AnalysisJob, InspectionReport, CameraBindingDecision, CameraViewReference, ViewComparisonRecord
from app import case_service
from app import recording_api
from app import m4_api
from app import m4_video_api
from app import m4d_api
from app import video_naming_api
from app.media import detect_signature, safe_basename
from app.config import settings
from app.camera_catalog import load_catalog, CatalogError, catalog_status
from app import map_db_spatial
from app import map_db_gis
from app.view_match import media_path, _decode_image, check_reference_view, ViewInputError

app = FastAPI(title='TrajNexa Demo M0', version='0.1.0')
# 录像资源：只读目录浏览 + 目录登记 + 扫描结果（见 app/recording_api.py）
app.include_router(recording_api.router)
app.include_router(m4_api.router)
app.include_router(m4_video_api.router)
app.include_router(m4d_api.router)
app.include_router(video_naming_api.router)

class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    case_id: str | None
    kind: str
    original_name: str
    mime_detected: str
    sha256: str
    size_bytes: int
    ingested_at: datetime

class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    media_id: str
    module: str
    status: str
    attempt: int
    progress: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None

class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_id: str
    media_id: str
    schema_version: str
    module_version: str
    result_json: dict
    created_at: datetime

@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail
    else:
        error = {'code': 'http_error', 'message': str(detail), 'details': None}
    return JSONResponse(status_code=exc.status_code, content={'error': error})

def fail(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message, 'details': None})

@app.get('/api/v1/health')
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
        return {'status': 'ok', 'component': 'trajnexa_main_m0'}
    except Exception:
        return JSONResponse(status_code=503, content={'status': 'degraded'})


# ---------------------------- 案件（Case） ----------------------------
# 案件是主系统组织研判的入口：只持有「上传后的要素文件」。
# 录像资源不在主系统内，删除案件时不会删除录像（见 case_service.release_recording_resources）。

class CaseInput(BaseModel):
    name: str
    description: str = ''

class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    code: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    media_count: int = 0
    media_bytes: int = 0

class CaseDeleteOut(BaseModel):
    id: str
    code: str
    name: str
    media_files_removed: int
    bytes_removed: int
    media_files_missing: int
    recordings_preserved: bool
    message: str

@app.get('/api/v1/cases', response_model=list[CaseOut])
def list_cases(db: Session = Depends(get_db)):
    """案件列表，按创建时间倒序；附带归属素材数量与占用字节数。"""
    rows = db.execute(
        select(Case, func.count(MediaAsset.id), func.coalesce(func.sum(MediaAsset.size_bytes), 0))
        .outerjoin(MediaAsset, MediaAsset.case_id == Case.id)
        .group_by(Case.id)
        .order_by(Case.created_at.desc())
    ).all()
    return [
        CaseOut(id=case.id, code=case.code, name=case.name, description=case.description,
                created_at=case.created_at, updated_at=case.updated_at,
                media_count=int(count), media_bytes=int(total))
        for case, count, total in rows
    ]

@app.post('/api/v1/cases', response_model=CaseOut, status_code=201)
def create_case(body: CaseInput, db: Session = Depends(get_db)):
    name = body.name.strip()
    description = body.description.strip()
    if not case_service.NAME_MIN <= len(name) <= case_service.NAME_MAX:
        fail(422, 'case_name_invalid',
             f'案件名称需为 {case_service.NAME_MIN}–{case_service.NAME_MAX} 个字符')
    if len(description) > case_service.DESCRIPTION_MAX:
        fail(422, 'case_description_too_long',
             f'案件说明最多 {case_service.DESCRIPTION_MAX} 个字符')
    case = case_service.create_case(db, name=name, description=description)
    return CaseOut(id=case.id, code=case.code, name=case.name, description=case.description,
                   created_at=case.created_at, updated_at=case.updated_at,
                   media_count=0, media_bytes=0)

@app.delete('/api/v1/cases/{case_id}', response_model=CaseDeleteOut)
def delete_case(case_id: str, db: Session = Depends(get_db)):
    """删除案件：只清理该案件上传后的要素文件；录像资源保持不动。"""
    case = db.get(Case, case_id)
    if case is None:
        fail(404, 'not_found', '案件不存在')
    snapshot = {'id': case.id, 'code': case.code, 'name': case.name}
    stats = case_service.purge_case_media(db, case)
    message = '已删除案件并清理该案件上传的要素文件；录像资源未做任何改动'
    if stats['media_files_missing']:
        message += f"（{stats['media_files_missing']} 个文件在磁盘上已不存在）"
    return CaseDeleteOut(**snapshot, media_files_removed=stats['media_files_removed'],
                         bytes_removed=stats['bytes_removed'],
                         media_files_missing=stats['media_files_missing'],
                         recordings_preserved=True, message=message)

@app.post('/api/v1/media', response_model=MediaOut, status_code=201)
async def upload_media(file: UploadFile = File(...), case_id: str | None = Form(None),
                       db: Session = Depends(get_db)):
    media_id = str(uuid4())
    if case_id:
        if db.get(Case, case_id) is None:
            await file.close()
            fail(422, 'unknown_case', '上传素材需要归属到已存在的案件')
    storage_root = settings.storage_root.resolve()
    storage_dir = storage_root / 'originals'
    storage_dir.mkdir(parents=True, exist_ok=True)
    temporary = storage_dir / f'.{media_id}.partial'
    length = 0
    digest = sha256()
    header = b''
    try:
        with temporary.open('xb') as dst:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                length += len(chunk)
                if length > settings.max_upload_bytes:
                    fail(413, 'file_too_large', '超过 Demo 配置的文件大小限制')
                if len(header) < 16:
                    header += chunk[:16 - len(header)]
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if not length:
            fail(415, 'empty_media', '无法接收空文件')
        info = detect_signature(header)
        if info is None:
            fail(415, 'unsupported_media', '无法识别的图像/视频签名，拒绝上传')
        kind, mime, suffix = info
        final = storage_dir / f'{media_id}{suffix}'
        temporary.replace(final)
        relpath = final.relative_to(storage_root).as_posix()
        asset = MediaAsset(id=media_id, case_id=case_id or None, kind=kind,
                           original_name=safe_basename(file.filename),
                           mime_detected=mime, sha256=digest.hexdigest(),
                           storage_relpath=relpath, size_bytes=length)
        try:
            db.add(asset)
            db.commit()
            db.refresh(asset)
        except Exception:
            db.rollback()
            final.unlink(missing_ok=True)
            raise
        return asset
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()

@app.get('/api/v1/media', response_model=list[MediaOut])
def list_media(case_id: str | None = None, db: Session = Depends(get_db)):
    """列出素材；传入 case_id 只看该案件的要素文件，不传则看全部（含未归属）。"""
    query = select(MediaAsset)
    if case_id:
        query = query.where(MediaAsset.case_id == case_id)
    return list(db.scalars(query.order_by(MediaAsset.ingested_at.desc()).limit(100)))

@app.get('/api/v1/media/{media_id}', response_model=MediaOut)
def get_media(media_id: str, db: Session = Depends(get_db)):
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        fail(404, 'not_found', '素材不存在')
    return asset

@app.get('/api/v1/media/{media_id}/content')
def media_content(media_id: str, db: Session = Depends(get_db)):
    """只读回放原始画面：供工作台预览与人工核对角标证据，不做任何改写。"""
    asset = db.get(MediaAsset, media_id)
    if asset is None:
        fail(404, 'not_found', '素材不存在')
    root = settings.storage_root.resolve()
    file = (root / asset.storage_relpath).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        fail(404, 'not_found', '素材文件不存在')
    return FileResponse(file, media_type=asset.mime_detected,
                        headers={'Cache-Control': 'private, max-age=600'})

@app.post('/api/v1/media/{media_id}/inspect', response_model=JobOut, status_code=202)
def inspect_media(media_id: str, db: Session = Depends(get_db)):
    if not db.get(MediaAsset, media_id):
        fail(404, 'not_found', '素材不存在')
    job = AnalysisJob(id=str(uuid4()), media_id=media_id, module='media_inspector_m1d', status='queued', attempt=0, progress=0)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job

@app.get('/api/v1/jobs/{job_id}', response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(AnalysisJob, job_id)
    if job is None:
        fail(404, 'not_found', '作业不存在')
    return job

@app.post('/api/v1/jobs/{job_id}/retry', response_model=JobOut)
def retry_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(AnalysisJob, job_id)
    if job is None:
        fail(404, 'not_found', '作业不存在')
    if job.status != 'failed':
        fail(409, 'invalid_job_state', '仅允许重试失败作业')
    job.status = 'queued'
    job.progress = 0
    job.error_code = job.error_message = None
    job.started_at = job.finished_at = None
    db.commit()
    db.refresh(job)
    return job

@app.get('/api/v1/inspections/{inspection_id}', response_model=ReportOut)
def get_report(inspection_id: str, db: Session = Depends(get_db)):
    report = db.get(InspectionReport, inspection_id)
    if report is None:
        fail(404, 'not_found', '报告不存在')
    return report

@app.get('/api/v1/jobs/{job_id}/report', response_model=ReportOut)
def get_job_report(job_id: str, db: Session = Depends(get_db)):
    report = db.scalar(select(InspectionReport).where(InspectionReport.job_id == job_id))
    if report is None:
        fail(404, 'not_found', '尚无报告')
    return report

# M1c: this demo API is loopback-only; these manual confirmations are NOT identity findings.
class BindingInput(BaseModel):
    decision: str
    camera_uuid: str | None = None
    note: str

class BindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    inspection_id: str
    decision: str
    camera_uuid: str | None
    camera_name_snapshot: str | None
    catalog_revision: str | None
    catalog_sha256: str | None
    matched_from_ocr: bool
    note: str
    created_at: datetime

@app.get('/api/v1/cameras/catalog')
def cameras_catalog():
    """Read-only display of the snapshot. No TrajNexa_Map write operations."""
    return catalog_status()

@app.get('/api/v1/inspections/{inspection_id}/camera-bindings', response_model=list[BindingOut])
def camera_binding_history(inspection_id: str, db: Session = Depends(get_db)):
    if db.get(InspectionReport, inspection_id) is None:
        fail(404, 'not_found', '预检报告不存在')
    return list(db.scalars(select(CameraBindingDecision)
                .where(CameraBindingDecision.inspection_id == inspection_id)
                .order_by(CameraBindingDecision.created_at, CameraBindingDecision.id)))

@app.post('/api/v1/inspections/{inspection_id}/camera-bindings',
          response_model=BindingOut, status_code=201)
def add_camera_binding(inspection_id: str, body: BindingInput, db: Session = Depends(get_db)):
    """Human decision appended to separate audit table. Existing report stays untouched."""
    report = db.get(InspectionReport, inspection_id)
    if report is None:
        fail(404, 'not_found', '预检报告不存在')
    note = body.note.strip()
    if len(note) < 8 or len(note) > 500:
        fail(422, 'reason_required', '请填写至少 8 个字符的核实依据（最多 500 字符）')
    if body.decision not in ('confirm', 'revoke'):
        fail(422, 'invalid_decision', '仅支持 confirm 或 revoke')
    candidate = None
    catalog = None
    if body.decision == 'confirm':
        if not body.camera_uuid:
            fail(422, 'camera_required', '人工确认时必须指定摄像头 UUID')
        try:
            catalog = load_catalog()
        except CatalogError:
            fail(409, 'catalog_unavailable', '缺少有效的摄像头目录快照，不能确认绑定')
        candidate = catalog.by_uuid(body.camera_uuid)
        if candidate is None or candidate['active'] is not True:
            fail(422, 'unknown_camera', '摄像头 UUID 不在当前有效目录中')
    elif body.camera_uuid is not None:
        fail(422, 'camera_must_be_null', '撤销绑定无需传入摄像头 UUID')
    # Only the source report's suggestions determine whether OCR suggested this UUID.
    suggestions = (report.result_json.get('camera') or {}).get('directory_candidates', [])
    from_ocr = bool(candidate and any(x.get('camera_uuid') == candidate['camera_uuid'] for x in suggestions))
    item = CameraBindingDecision(id=str(uuid4()), inspection_id=inspection_id,
        decision=body.decision, camera_uuid=candidate['camera_uuid'] if candidate else None,
        camera_name_snapshot=candidate['display_name'] if candidate else None,
        catalog_revision=catalog.revision if catalog else None,
        catalog_sha256=catalog.fingerprint if catalog else None,
        matched_from_ocr=from_ocr, note=note)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# M1d compares fixed background only after a human-confirmed camera binding.
class ViewReferenceInput(BaseModel):
    media_id: str
    note: str

class ViewReferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    camera_uuid: str
    media_id: str
    catalog_revision: str
    catalog_sha256: str
    media_sha256: str
    note: str
    created_at: datetime

class ViewCheckInput(BaseModel):
    reference_id: str

class ViewCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    inspection_id: str
    reference_id: str
    camera_uuid: str
    catalog_revision: str
    binding_decision_id: str
    result_json: dict
    created_at: datetime

@app.post('/api/v1/cameras/{camera_uuid}/view-references',
          response_model=ViewReferenceOut, status_code=201)
def add_view_reference(camera_uuid: str, body: ViewReferenceInput,
                       db: Session = Depends(get_db)):
    """Register an existing uploaded test image as a manually designated reference."""
    try:
        catalog = load_catalog()
    except CatalogError:
        fail(409, 'catalog_unavailable', '缺少有效的摄像头目录快照')
    camera = catalog.by_uuid(camera_uuid)
    if camera is None or camera['active'] is not True:
        fail(422, 'invalid_camera', '摄像头不在当前有效目录中')
    note = body.note.strip()
    if not 8 <= len(note) <= 500:
        fail(422, 'note_required', '请填写至少 8 个字符的参考画面来源说明')
    media = db.get(MediaAsset, body.media_id)
    if media is None or media.kind != 'image':
        fail(422, 'reference_image_required', '请选择已经上传的图片素材')
    try:
        img = _decode_image(media_path(media))
    except ViewInputError:
        fail(409, 'reference_file_invalid', '参考图片原件不存在或 SHA-256 不匹配')
    if img is None or min(img.shape[:2]) < 64 or img.shape[0] * img.shape[1] > 28_000_000:
        fail(422, 'reference_image_invalid', '参考图片无效或尺寸超出支持范围')
    ref = CameraViewReference(id=str(uuid4()), camera_uuid=camera_uuid,
        media_id=media.id, catalog_revision=catalog.revision,
        catalog_sha256=catalog.fingerprint, media_sha256=media.sha256, note=note)
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return ref

@app.get('/api/v1/cameras/{camera_uuid}/view-references', response_model=list[ViewReferenceOut])
def get_view_references(camera_uuid: str, db: Session = Depends(get_db)):
    return list(db.scalars(select(CameraViewReference)
        .where(CameraViewReference.camera_uuid == camera_uuid)
        .order_by(CameraViewReference.created_at, CameraViewReference.id)))

@app.get('/api/v1/inspections/{inspection_id}/view-checks', response_model=list[ViewCheckOut])
def list_view_checks(inspection_id: str, db: Session = Depends(get_db)):
    if not db.get(InspectionReport, inspection_id):
        fail(404, 'inspection_not_found', '预检报告不存在')
    return list(db.scalars(select(ViewComparisonRecord)
        .where(ViewComparisonRecord.inspection_id == inspection_id)
        .order_by(ViewComparisonRecord.created_at, ViewComparisonRecord.id)))

@app.post('/api/v1/inspections/{inspection_id}/view-checks',
          response_model=ViewCheckOut, status_code=201)
def create_view_check(inspection_id: str, body: ViewCheckInput,
                      db: Session = Depends(get_db)):
    inspection = db.get(InspectionReport, inspection_id)
    if inspection is None:
        fail(404, 'inspection_not_found', '预检报告不存在')
    reference = db.get(CameraViewReference, body.reference_id)
    if reference is None:
        fail(404, 'reference_not_found', '参考画面不存在')
    # Current human binding is the last decision, not the first confirmation.
    binding = db.scalar(select(CameraBindingDecision)
        .where(CameraBindingDecision.inspection_id == inspection_id)
        .order_by(CameraBindingDecision.created_at.desc(), CameraBindingDecision.id.desc())
        .limit(1))
    if binding is None or binding.decision != 'confirm':
        fail(409, 'camera_binding_required', '请先人工确认当前素材的摄像头')
    if binding.camera_uuid != reference.camera_uuid:
        fail(409, 'camera_binding_mismatch', '参考画面与当前绑定摄像头不一致')
    try:
        catalog = load_catalog()
    except CatalogError:
        fail(409, 'catalog_unavailable', '缺少有效摄像头目录')
    camera = catalog.by_uuid(reference.camera_uuid)
    if camera is None or not camera['active']:
        fail(409, 'camera_inactive', '当前摄像头无有效目录记录')
    if (catalog.fingerprint != reference.catalog_sha256 or
            catalog.fingerprint != binding.catalog_sha256):
        fail(409, 'catalog_revision_changed', '摄像头目录已变更，需重新审核绑定和参考画面')
    observed = db.get(MediaAsset, inspection.media_id)
    reference_media = db.get(MediaAsset, reference.media_id)
    if observed is None or reference_media is None:
        fail(409, 'media_unavailable', '原始媒体记录不存在')
    try:
        result = check_reference_view(reference_media, observed, inspection.result_json)
    except ViewInputError:
        fail(409, 'media_hash_mismatch_or_missing', '媒体原件不存在或哈希校验失败')
    result['reference_id'] = reference.id
    result['camera_uuid'] = reference.camera_uuid
    result['catalog_revision'] = catalog.revision
    result['binding_decision_id'] = binding.id
    result['requires_human_review'] = True
    comparison = ViewComparisonRecord(id=str(uuid4()), inspection_id=inspection_id,
        reference_id=reference.id, camera_uuid=reference.camera_uuid,
        catalog_revision=catalog.revision, binding_decision_id=binding.id, result_json=result)
    db.add(comparison)
    db.commit()
    db.refresh(comparison)
    return comparison


# M2b: read-only GIS camera points and modelled FOV from TrajNexa_Map.
# No map writes, fake fallback geometry, or automatic UUID binding.
@app.get('/api/v1/spatial/cameras')
def get_map_spatial_cameras():
    try:
        return map_db_spatial.read_map_spatial(dsn=settings.map_db_readonly_url)
    except map_db_spatial.SpatialReadError as exc:
        fail(503, str(exc), 'Map 空间数据当前不可读取；原始数据未修改')


# M2c: real view-scoped, source-aware GIS. OSM baseline + Map manual revisions.
# Data never written to the Map database and no artificial road/camera geometry.
@app.get('/api/v1/gis/viewport')
def map_gis_viewport(bbox: str):
    try:
        map_db_gis.parse_bbox(bbox)
    except map_db_gis.ViewportError as exc:
        fail(422,str(exc),'请使用有效的小范围地图视野（west,south,east,north）')
    try:
        return map_db_gis.read_gis_viewport(dsn=settings.map_db_readonly_url,bbox=bbox)
    except map_db_gis.ViewportError as exc:
        code=str(exc)
        fail(422 if 'zoom_in' in code else 503,code,
             'Map GIS 数据暂不可读取，或当前范围过大；没有使用模拟地图数据')


@app.get('/api/v1/gis/anchor')
def map_gis_anchor():
    try:
        return map_db_gis.read_gis_anchor(dsn=settings.map_db_readonly_url)
    except map_db_gis.ViewportError as exc:
        fail(503,str(exc),'Map 镜头定位暂不可用')
