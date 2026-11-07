"""添加要素后的定位与时间框（B8-P1）。

规则（2026-11-07 定）：
- 只有「有角标时间 + 相机能唯一落到 Map 镜头」的完整截图才定位；局部截图直接跳过。
- 定位分级：L2 已审核标定（仅 WGS84 世界坐标）> L1 Map 视域模型中心 > L0 镜头位置。
  任何一级都带 quality 与不确定度，界面必须显示等级，不能当成精确位置。
- 时间框是标签：拖动只写 label_offset，ground_point 不被改写。
- 轨迹回填复用 tracking_runs，本模块只存指针与状态，不复制轨迹点。
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import map_db_spatial
from app import m6_camera_ray_projection as ray_projection
from app import m6_ground_projection as ground_projection
from app import m6_spatial_adapter as adapter
from app.config import settings
from app.models import (MediaAsset, TargetElementAnchor, TargetElementGroup,
                        TargetElementObject, TargetSpatialObservation, TrackingRun)

WGS84_CRS = ('EPSG:4326', 'WGS84', 'CRS:84')
CAPTURE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$')
LEVEL_ORDER = ('observation', 'recognition', 'identification')
MAX_RADIUS_M = 400.0
MAX_OFFSET_PX = 4000.0
# 回填窗口：截图时间前 30 秒、后 90 秒；目标仍在视野内时按 30 秒步长追加，
# 直到离开视野 / 片段结束 / 累计 15 分钟。这里只落参数，逐步追加由追踪任务执行。
TRACE_BEFORE_SECONDS = 30
TRACE_AFTER_SECONDS = 90
TRACE_STEP_SECONDS = 30
TRACE_CAP_SECONDS = 900
TRACE_ACTIVE = ('queued', 'running')


class AnchorError(ValueError):
    """稳定错误码，HTTP 层映射状态码，不抛 500。"""

    def __init__(self, code: str, message: str = ''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 镜头解析

def resolve_camera(spatial: dict, camera_uuid, camera_name) -> tuple[dict | None, str]:
    """uuid 优先；名称必须精确且唯一，命中多台直接放弃（不猜）。"""
    cameras = spatial.get('cameras') if isinstance(spatial, dict) else None
    if not isinstance(cameras, list):
        return None, 'none'
    wanted = str(camera_uuid or '').strip()
    if wanted:
        hits = [c for c in cameras if c.get('camera_uuid') == wanted]
        if len(hits) == 1:
            return hits[0], 'catalog'
    name = str(camera_name or '').strip()
    if name:
        hits = [c for c in cameras if str(c.get('display_name') or '').strip() == name]
        if len(hits) == 1:
            return hits[0], 'name_match'
        if len(hits) > 1:
            raise AnchorError('camera_name_ambiguous', f'镜头名称「{name}」命中 {len(hits)} 台，无法唯一定位')
    return None, 'none'


def _ring_points(camera: dict) -> list[tuple[float, float]]:
    """取视域多边形外环顶点（经纬度）；Map 的 geometry 可能是 {value:[...]} 包装。"""
    levels = {str(item.get('level')): item for item in (camera.get('fov_levels') or [])
              if isinstance(item, dict)}
    for level in LEVEL_ORDER:
        geometry = levels.get(level, {}).get('geometry')
        if not isinstance(geometry, dict):
            continue
        coords = geometry.get('coordinates')
        if isinstance(coords, dict):
            coords = coords.get('value')
        if geometry.get('type') == 'Polygon':
            polygons = [coords]
        else:
            polygons = coords
        if not isinstance(polygons, list):
            continue
        points: list[tuple[float, float]] = []
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                continue
            ring = polygon[0] if isinstance(polygon[0], list) and polygon[0] and \
                isinstance(polygon[0][0], (list, tuple)) else polygon
            for item in ring:
                if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                    continue
                try:
                    lon, lat = float(item[0]), float(item[1])
                except (TypeError, ValueError):
                    continue
                if -180 <= lon <= 180 and -90 <= lat <= 90:
                    points.append((lon, lat))
        if len(points) >= 3:
            return points
    return []


def _location(camera: dict) -> dict | None:
    location = camera.get('location') if isinstance(camera.get('location'), dict) else {}
    try:
        lat, lon = float(location.get('lat')), float(location.get('lon'))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {'lat': lat, 'lon': lon, 'crs': 'EPSG:4326'}


def _fov_point(camera: dict) -> dict | None:
    """视域模型多边形的顶点均值 + 最远顶点半径；不是接地点，只是「这片视域里」。"""
    points = _ring_points(camera)
    if not points:
        return None
    lon = sum(p[0] for p in points) / len(points)
    lat = sum(p[1] for p in points) / len(points)
    radius = max(adapter.haversine_m({'lon': lon, 'lat': lat}, {'lon': p[0], 'lat': p[1]})
                 for p in points)
    radius = min(MAX_RADIUS_M, max(5.0, radius))
    return {'ground_point': {'lat': round(lat, 7), 'lon': round(lon, 7), 'crs': 'EPSG:4326'},
            'uncertainty_radius_m': round(radius, 2),
            'projection_level': 'L1',
            'quality': 'fov_centroid_unverified',
            'uncertainties': ['map_fov_model_not_verified_coverage', 'no_approved_ground_calibration']}


def _calibration_point(camera_uuid: str, pixel, frame_size) -> dict | None:
    """有已审核标定且世界坐标是 WGS84 时才给 L2；否则 None（不猜坐标转换）。"""
    if pixel is None or frame_size is None:
        return None
    try:
        calibration = map_db_spatial.read_ground_calibration_for_camera(
            dsn=settings.map_db_readonly_url, camera_uuid=camera_uuid)
    except map_db_spatial.SpatialReadError:
        return None
    if not calibration:
        return None
    if str(calibration.get('world_crs') or '').upper() not in WGS84_CRS:
        return None
    try:
        result = ground_projection.project(calibration, pixel, frame_size=frame_size)
    except (ground_projection.GroundCalibrationError, TypeError, ValueError):
        return None
    point = result.get('ground_point') or {}
    if not (isinstance(point.get('x'), (int, float)) and isinstance(point.get('y'), (int, float))):
        return None
    return {'ground_point': {'lat': round(float(point['y']), 7),
                             'lon': round(float(point['x']), 7),
                             'crs': 'EPSG:4326'},
            'uncertainty_radius_m': result.get('uncertainty_radius_m'),
            'projection_level': 'L2',
            'quality': result.get('quality') or 'estimated_review_required',
            'calibration_id': result.get('calibration_id'),
            'uncertainties': result.get('uncertainties') or []}


def _ray_point(camera: dict, pixel, frame_size) -> tuple[dict | None, str | None]:
    """路线 A：截图接地点像素 + Map 相机参数 → 地面交点，等级 experimental_estimate。

    这不是正式 L2：位姿与内参没有独立测量，只能作为「实验估计」显示。
    缺任何一项必要参数就返回原因码，由上层降级到视域中心——绝不假设默认高度或俯角。
    """
    if pixel is None or frame_size is None:
        return None, 'contact_pixel_missing'
    location = _location(camera)
    if location is None:
        return None, 'camera_position_missing'
    if not camera.get('ray_projection_ready'):
        return None, 'camera_parameters_incomplete'
    params = {
        'width': frame_size[0], 'height': frame_size[1],
        'hfov_deg': camera.get('horizontal_fov_deg'),
        'pitch_deg': camera.get('tilt_deg'),
        'yaw_deg': camera.get('bearing_deg'),
        'height_m': camera.get('mount_height_m'),
        'lon': location['lon'], 'lat': location['lat'],
    }
    try:
        result = ray_projection.project(params, pixel)
    except ray_projection.ProjectionUnavailable as exc:
        return None, f'ray_{exc}'
    except (TypeError, ValueError):
        return None, 'ray_projection_invalid_parameters'
    point = result.get('ground_point') or {}
    lon, lat = point.get('x'), point.get('y')
    if not (isinstance(lon, (int, float)) and isinstance(lat, (int, float))):
        return None, 'ray_projection_invalid_point'
    try:
        radius = ray_projection.uncertainty_radius(params, pixel)
    except (TypeError, ValueError):
        radius = None
    return {
        'ground_point': {'lat': round(float(lat), 7), 'lon': round(float(lon), 7),
                         'crs': 'EPSG:4326'},
        'uncertainty_radius_m': round(float(radius), 2) if radius else None,
        'projection_level': 'L1',
        'quality': 'experimental_estimate',
        'projection_method': 'camera_ray_ground_intersection',
        'contact': {'pixel': [round(float(pixel[0]), 2), round(float(pixel[1]), 2)],
                    'frame_size': [round(float(frame_size[0]), 2),
                                   round(float(frame_size[1]), 2)],
                    'quality': 'estimated', 'source': 'box_bottom_center'},
        'range_m': result.get('range_m'),
        'uncertainties': ['pose_reported_unverified', 'intrinsics_from_hfov_assumed',
                          'ground_plane_flat_assumed',
                          'contact_point_is_box_bottom_center'],
    }, None


def _camera_fallback(camera: dict) -> dict | None:
    location = _location(camera)
    if location is None:
        return None
    range_m = camera.get('range_m')
    radius = float(range_m) if isinstance(range_m, (int, float)) and not isinstance(range_m, bool) \
        and 3 <= float(range_m) <= MAX_RADIUS_M else 40.0
    return {'ground_point': location, 'uncertainty_radius_m': round(radius, 2),
            'projection_level': 'L0',
            'quality': 'camera_position_only',
            'uncertainties': ['no_approved_ground_calibration', 'no_fov_geometry']}


def locate(spatial: dict, camera: dict, pixel, frame_size) -> dict:
    """定位优先级：已审核 L2 → 参数射线投影（实验估计）→ L1 视域中心 → L0 镜头位置。"""
    camera_uuid = camera.get('camera_uuid')
    if camera_uuid:
        found = _calibration_point(str(camera_uuid), pixel, frame_size)
        if found:
            return found
    ray, ray_error = _ray_point(camera, pixel, frame_size)
    if ray:
        return ray
    found = _fov_point(camera)
    if found:
        if ray_error:
            found['ray_error'] = ray_error
        return found
    found = _camera_fallback(camera)
    if found:
        if ray_error:
            found['ray_error'] = ray_error
        return found
    raise AnchorError('camera_position_missing', '该镜头在 Map 上没有坐标，无法定位')


# ---------------------------------------------------------------- 校验

def _pixel(value) -> list[float] | None:
    if value is None:
        return None
    if not (isinstance(value, list) and len(value) == 2):
        raise AnchorError('pixel_xy_invalid')
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise AnchorError('pixel_xy_invalid')
        out.append(float(item))
    return out


def _frame_size(value) -> list[float] | None:
    if value is None:
        return None
    if not (isinstance(value, list) and len(value) == 2):
        raise AnchorError('frame_size_invalid')
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or item <= 0:
            raise AnchorError('frame_size_invalid')
        out.append(float(item))
    return out


def _capture(value) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    if len(text) == 16:
        text = text + ':00'
    if not CAPTURE_RE.match(text):
        raise AnchorError('capture_local_invalid', '截图时间应为 YYYY-MM-DDTHH:MM:SS')
    return text


# ---------------------------------------------------------------- 读写

def _out(row: TargetElementAnchor) -> dict:
    return {'id': row.id, 'case_id': row.case_id, 'group_id': row.group_id,
            'object_id': row.object_id, 'media_id': row.media_id,
            'camera_uuid': row.camera_uuid, 'camera_label': row.camera_label,
            'camera_source': row.camera_source,
            'capture_local': row.capture_local, 'capture_source': row.capture_source,
            'capture_verified': row.capture_verified,
            'pixel_xy': row.pixel_xy, 'frame_size': row.frame_size,
            'projection_level': row.projection_level,
            'projection_quality': row.projection_quality,
            'ground_point': row.ground_point,
            'uncertainty_radius_m': row.uncertainty_radius_m,
            'projection_payload': row.projection_payload or {},
            'label_offset': row.label_offset or {},
            'trace_run_id': row.trace_run_id, 'trace_status': row.trace_status,
            'trace_window': row.trace_window,
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'updated_at': row.updated_at.isoformat() if row.updated_at else None}


def _group(db: Session, case_id: str, group_id: str) -> TargetElementGroup:
    group = db.get(TargetElementGroup, group_id)
    if group is None or group.case_id != case_id:
        raise AnchorError('target_group_not_found')
    return group


def create_anchor(db: Session, case_id: str, group_id: str, payload: dict) -> dict:
    group = _group(db, case_id, group_id)
    payload = payload if isinstance(payload, dict) else {}
    base = str(payload.get('base') or 'full')
    object_id = str(payload.get('object_id') or '').strip() or 'P1'
    objects = db.scalars(select(TargetElementObject).where(
        TargetElementObject.group_id == group_id)).all()
    row_object = next((obj for obj in objects if obj.id == object_id), None)
    if row_object is None:
        # 前端传的是组内客户端 id（P1/A1），这里按角色回退，绝不凭空建档
        row_object = next((obj for obj in objects if obj.role == 'primary'), None)
    if row_object is None:
        raise AnchorError('target_object_not_found')
    media_id = str(payload.get('media_id') or '').strip() or group.media_id
    media = db.get(MediaAsset, media_id) if media_id else None
    if media is not None and media.case_id != case_id:
        raise AnchorError('media_not_found')

    if base == 'crop':
        return {'skipped': 'crop_only',
                'reason': '局部截图不产生定位点与时间轴卡片，只写目标特征'}

    capture_local = _capture(payload.get('capture_local'))
    if capture_local is None:
        return {'skipped': 'capture_time_missing',
                'reason': '没有截图角标时间，无法在时间轴上定位'}

    try:
        spatial = map_db_spatial.read_map_spatial(dsn=settings.map_db_readonly_url)
    except map_db_spatial.SpatialReadError as exc:
        raise AnchorError('map_spatial_unavailable', str(exc)) from None
    camera, camera_source = resolve_camera(spatial, payload.get('camera_uuid'),
                                           payload.get('camera_name'))
    if camera is None:
        return {'skipped': 'camera_not_resolved',
                'reason': '截图里的镜头名没有唯一对应到 Map 镜头，只在特征栏记录'}

    pixel = _pixel(payload.get('pixel_xy'))
    frame_size = _frame_size(payload.get('frame_size'))
    located = locate(spatial, camera, pixel, frame_size)

    camera_label = str(camera.get('display_name') or '').strip() or '未命名镜头'
    row = db.scalars(select(TargetElementAnchor).where(
        TargetElementAnchor.group_id == group_id,
        TargetElementAnchor.object_id == row_object.id)).first()
    now = _utcnow()
    if row is None:
        row = TargetElementAnchor(id=str(uuid.uuid4()), case_id=case_id, group_id=group_id,
                                  object_id=row_object.id, created_at=now)
        db.add(row)
    row.media_id = media.id if media is not None else group.media_id
    row.camera_uuid = camera.get('camera_uuid')
    row.camera_label = camera_label
    row.camera_source = camera_source
    row.capture_local = capture_local
    row.capture_source = str(payload.get('capture_source') or 'osd')[:24]
    row.capture_verified = bool(payload.get('capture_verified'))
    row.pixel_xy = pixel
    row.frame_size = frame_size
    row.projection_level = located['projection_level']
    row.projection_quality = located['quality']
    row.ground_point = located['ground_point']
    row.uncertainty_radius_m = located.get('uncertainty_radius_m')
    row.projection_payload = {**located, 'camera_pose_quality': camera.get('pose_quality'),
                              'spatial_revision': spatial.get('spatial_revision'),
                              'observation_binding': row_object.m4_observation_id}
    row.trace_status = row.trace_status or 'none'
    row.updated_at = now
    db.commit()
    db.refresh(row)
    trace = start_trace(db, case_id, group_id, row)
    return {'anchor': _out(row),
            'trace': trace,
            'note': '定位点是估计结果，等级与不确定度以 projection_level / projection_quality 为准'}


def list_anchors(db: Session, case_id: str) -> dict:
    rows = db.scalars(select(TargetElementAnchor)
                      .where(TargetElementAnchor.case_id == case_id)
                      .order_by(TargetElementAnchor.created_at)).all()
    return {'items': [_out(row) for row in rows]}


def _anchor(db: Session, case_id: str, anchor_id: str) -> TargetElementAnchor:
    row = db.get(TargetElementAnchor, anchor_id)
    if row is None or row.case_id != case_id:
        raise AnchorError('element_anchor_not_found')
    return row


def patch_anchor(db: Session, case_id: str, anchor_id: str, payload: dict) -> dict:
    row = _anchor(db, case_id, anchor_id)
    payload = payload if isinstance(payload, dict) else {}
    if 'label_offset' in payload:
        raw = payload.get('label_offset') or {}
        if not isinstance(raw, dict):
            raise AnchorError('label_offset_invalid')
        offset = {}
        for key in ('dx', 'dy'):
            value = raw.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                    not math.isfinite(value) or abs(value) > MAX_OFFSET_PX:
                raise AnchorError('label_offset_invalid')
            offset[key] = round(float(value), 1)
        row.label_offset = offset
    if 'ground_point' in payload or 'pixel_xy' in payload:
        raise AnchorError('position_is_not_draggable',
                          '定位点不能被拖动改写；只允许移动时间框标签')
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return {'anchor': _out(row)}


def delete_anchor(db: Session, case_id: str, anchor_id: str) -> dict:
    """删掉一个定位点：只动锚点行，目标组、特征与截图都保留。"""
    row = _anchor(db, case_id, anchor_id)
    db.delete(row)
    db.commit()
    return {'deleted': True, 'anchor_id': anchor_id}


def delete_for_group(db: Session, group_id: str) -> None:
    for row in db.scalars(select(TargetElementAnchor).where(
            TargetElementAnchor.group_id == group_id)).all():
        db.delete(row)
    db.commit()


# ---------------------------------------------------------------- 录像回填
#
# 回填不是「重新识别目标」：它用主目标的独立观测作为追踪起点，在截图时间
# 前后各建一条追踪任务（前 30 秒 / 后 90 秒），worker 逐步产出空间点。
# 有空间点才在地图上画线；没有点就保持单点，绝不画一条假路径。

def trace_plan(row: TargetElementAnchor) -> dict:
    return {'before_seconds': TRACE_BEFORE_SECONDS,
            'after_seconds': TRACE_AFTER_SECONDS,
            'step_seconds': TRACE_STEP_SECONDS,
            'cap_seconds': TRACE_CAP_SECONDS,
            'capture_local': row.capture_local}


def start_trace(db: Session, case_id: str, group_id: str,
                row: TargetElementAnchor) -> dict:
    """按锚点建追踪任务（幂等：已在排队/运行/完成就不再建）。"""
    if row.trace_status in TRACE_ACTIVE or row.trace_status == 'ready':
        return {'status': row.trace_status, 'reused': True,
                'run_id': row.trace_run_id}
    binding = (row.projection_payload or {}).get('observation_binding')
    if not binding:
        row.trace_status = 'none'
        row.updated_at = _utcnow()
        db.commit()
        return {'status': 'none', 'reason': 'primary_observation_missing',
                'note': '主目标没有独立观测，无法作为追踪起点'}
    from app import m6_run_service
    runs: dict[str, dict] = {}
    for name, direction, budget, windows in (
            ('forward', 'forward', TRACE_AFTER_SECONDS, 3),
            ('backward', 'backward', TRACE_BEFORE_SECONDS, 2)):
        try:
            run = m6_run_service.create_run(db, case_id, {
                'observation_id': binding,
                'direction': direction,
                'research_seconds': max(45, budget + TRACE_STEP_SECONDS),
                'segment_seconds': 6.0,
                'gap_seconds': 2.0,
                'max_windows': windows,
            })
        except Exception as exc:  # 追踪服务不可用时不阻塞定位点
            code = getattr(exc, 'code', None) or type(exc).__name__
            runs[name] = {'status': 'unavailable', 'error': str(code)}
            continue
        runs[name] = {'status': run.status, 'run_id': run.id}
    forward = runs.get('forward') or {}
    row.trace_run_id = forward.get('run_id') or row.trace_run_id
    row.trace_status = 'queued' if row.trace_run_id else 'unavailable'
    row.trace_window = {**trace_plan(row), 'runs': runs}
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return {'status': row.trace_status, 'run_id': row.trace_run_id, 'runs': runs}


def _group_observation_ids(db: Session, group_id: str) -> list[str]:
    return [obj.m4_observation_id for obj in db.scalars(
        select(TargetElementObject).where(TargetElementObject.group_id == group_id)).all()
        if obj.m4_observation_id]


def trace_view(db: Session, case_id: str, group_id: str,
               row: TargetElementAnchor | None) -> dict:
    """状态 + 空间点：没有点就说没有点，不假装已经有路径。"""
    if row is None:
        return {'group_id': group_id, 'status': 'none', 'segments': [],
                'note': '该要素没有定位点，因此没有录像回填'}
    run = db.get(TrackingRun, row.trace_run_id) if row.trace_run_id else None
    observation_ids = _group_observation_ids(db, group_id)
    points = []
    if observation_ids:
        rows = db.scalars(select(TargetSpatialObservation).where(
            TargetSpatialObservation.case_id == case_id,
            TargetSpatialObservation.observation_id.in_(observation_ids))
            .order_by(TargetSpatialObservation.created_at)).all()
        points = [{'t': item.created_at.isoformat(),
                   'lat': (item.ground_point or {}).get('lat'),
                   'lon': (item.ground_point or {}).get('lon'),
                   'spatial_level': item.spatial_level,
                   'uncertainty_radius_m': item.uncertainty_radius_m}
                  for item in rows if (item.ground_point or {}).get('lat') is not None]
    status = row.trace_status or 'none'
    if run is not None:
        status = {'queued': 'queued', 'running': 'running'}.get(run.status, run.status)
    if points and status in ('queued', 'running', 'completed', 'no_points'):
        status = 'ready'
    elif not points and status in ('queued', 'running', 'completed'):
        status = 'pending' if status != 'completed' else 'no_points'
    segments = []
    if points:
        segments = [{'start': points[0]['t'], 'end': points[-1]['t'], 'points': points}]
    note = {
        'none': '尚未开始录像回填',
        'queued': '回填任务已排队，读到的空间点会逐步补上',
        'running': '正在回填：只输出可追溯估计点，不做身份结论',
        'pending': '回填进行中，暂时还没有可用空间点',
        'no_points': '回填已结束，但没有产出可用空间点（标定/视域不足）',
        'ready': '路径是可追溯估计；单点不给方向',
        'unavailable': '追踪服务不可用，保留单点定位',
    }.get(status, '回填状态未知')
    return {'group_id': group_id, 'anchor_id': row.id, 'status': status,
            'run_id': row.trace_run_id, 'run_status': run.status if run else None,
            'window': row.trace_window, 'segments': segments, 'note': note}
