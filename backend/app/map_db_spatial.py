"""M2b read-only Map GIS camera points and model-derived FOV (EPSG:4326).

Uses a separate SELECT-only Map DB role. Never writes data or confirms camera
identity, viewing direction, range or recorder availability. Registry UUIDs and
legacy GIS integer IDs remain distinct, and unlinked map cameras stay unbound.
"""
from __future__ import annotations

import hashlib
import json
import math
from uuid import UUID

MAX_CAMERAS = 3000
MAX_FOV_ROWS = 9000
MAX_GEOMETRY_BYTES = 8 * 1024 * 1024

class SpatialReadError(ValueError):
    """Public sanitized error code; database errors might contain credentials."""


POINTS_SQL = """
SELECT m.id, r.id, COALESCE(r.name, ''), ST_X(m.location), ST_Y(m.location),
       m.direction, m.angle, m.camera_type, m.effective_range_m,
       r.pose_quality, r.position_quality, r.enabled, r.horizontal_fov_deg
FROM public.cameras_camera AS m
LEFT JOIN public.cameras_registry AS r
       ON r.map_camera_id = m.id AND r.deleted_at IS NULL
ORDER BY m.id LIMIT 3001
"""
FOV_SQL = """
SELECT camera_id_id, level, with_intersection, ST_AsGeoJSON(geom, 6)
FROM public.cameras_camerafocus
WHERE scenario = 'mean' AND geom IS NOT NULL
ORDER BY camera_id_id, level LIMIT 9001
"""
VIDEO_SQL = """
SELECT DISTINCT r.map_camera_id
FROM public.cameras_snapshot AS s
JOIN public.cameras_registry AS r ON r.id = s.camera_id
WHERE s.source_type = 'video_frame'
  AND s.image <> ''
  AND r.deleted_at IS NULL
  AND r.map_camera_id IS NOT NULL
LIMIT 3001
"""
LEVELS = ('identification', 'recognition', 'observation')


def _number(raw, minimum, maximum, label):
    if raw is None:
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError):
        raise SpatialReadError(f'map_spatial_invalid_{label}') from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise SpatialReadError(f'map_spatial_invalid_{label}')
    return round(value, 6)


def _geometry(raw):
    if not isinstance(raw, str):
        raise SpatialReadError('map_spatial_invalid_geometry')
    if len(raw.encode('utf-8')) > MAX_GEOMETRY_BYTES:
        raise SpatialReadError('map_spatial_geometry_too_large')
    try:
        geom = json.loads(raw)
    except (ValueError, TypeError):
        raise SpatialReadError('map_spatial_invalid_geometry') from None
    if not isinstance(geom, dict) or geom.get('type') not in ('Polygon','MultiPolygon'):
        raise SpatialReadError('map_spatial_invalid_geometry')
    polygons = ([geom.get('coordinates')] if geom['type']=='Polygon'
                else geom.get('coordinates'))
    if not isinstance(polygons,list):
        raise SpatialReadError('map_spatial_invalid_geometry')
    npoints = 0
    for polygon in polygons:
        if not isinstance(polygon,list) or not polygon:
            raise SpatialReadError('map_spatial_invalid_geometry')
        for ring in polygon:
            if not isinstance(ring,list) or len(ring)<4:
                raise SpatialReadError('map_spatial_invalid_geometry')
            for coord in ring:
                if not isinstance(coord,list) or len(coord)<2:
                    raise SpatialReadError('map_spatial_invalid_geometry')
                _number(coord[0],-180,180,'geometry')
                _number(coord[1],-90,90,'geometry')
                npoints += 1
                if npoints>12000:
                    raise SpatialReadError('map_spatial_geometry_too_complex')
    return geom


def build_spatial_payload(point_rows, fov_rows, video_rows=()):
    if len(point_rows)>MAX_CAMERAS:
        raise SpatialReadError('map_spatial_too_many_cameras')
    if len(fov_rows)>MAX_FOV_ROWS:
        raise SpatialReadError('map_spatial_too_many_fov_rows')
    if len(video_rows)>MAX_CAMERAS:
        raise SpatialReadError('map_spatial_too_many_camera_videos')
    video_ids=set()
    for row in video_rows:
        if len(row)!=1 or not isinstance(row[0],int):
            raise SpatialReadError('map_spatial_unexpected_video_row')
        video_ids.add(row[0])
    cameras=[]; lookup={}; seen_uuid=set(); unlinked=0; null_points=0
    for row in point_rows:
        if len(row)!=13:
            raise SpatialReadError('map_spatial_unexpected_camera_row')
        (map_id,uuid_raw,name,lon,lat,bearing,tilt,camtype,range_m,
         pose_quality,position_quality,enabled,horizontal_fov)=row
        if not isinstance(map_id,int) or map_id in lookup:
            raise SpatialReadError('map_spatial_duplicate_or_invalid_map_id')
        if lon is None or lat is None:
            null_points += 1
            continue
        lon=_number(lon,-180,180,'longitude')
        lat=_number(lat,-90,90,'latitude')
        uuid=None
        if uuid_raw is not None:
            try:uuid=str(UUID(str(uuid_raw)))
            except (ValueError,TypeError):
                raise SpatialReadError('map_spatial_invalid_uuid') from None
            if uuid in seen_uuid:
                raise SpatialReadError('map_spatial_duplicate_uuid')
            seen_uuid.add(uuid)
        else:
            unlinked+=1
        bearing=_number(bearing,0,360,'bearing')
        tilt=_number(tilt,0,360,'tilt')
        range_m=_number(range_m,0,50000,'range')
        horizontal_fov=_number(horizontal_fov,0,360,'horizontal_fov')
        # The map's numeric angle is TILT. It is not the horizontal FOV.
        # Presence of values is not proof of actual camera calibration.
        quality = (pose_quality if pose_quality in
                   ('reported','estimated','calibrated','unknown') else 'unknown')
        if bearing is None or tilt is None or uuid is None:
            quality='unknown'
        item={
            'map_camera_id':str(map_id),'camera_uuid':uuid,
            'display_name':str(name or '')[:120] or None,
            'location':{'lon':lon,'lat':lat,'crs':'EPSG:4326'},
            'bearing_deg':bearing,'tilt_deg':tilt,
            'horizontal_fov_deg':horizontal_fov,
            'range_m':range_m,'camera_type':str(camtype or '')[:60],
            'pose_quality':quality,
            'position_quality':position_quality if position_quality in
                ('reported','verified','unknown') else 'unknown',
            'enabled':enabled if isinstance(enabled,bool) else None,
            'has_video':map_id in video_ids,
            'fov_levels':[],
            'fov_provenance':'map_model_geometry_not_verified_coverage',
            'map_registry_link':'linked' if uuid else 'map_only_unbound',
        }
        lookup[map_id]=item
        cameras.append(item)
    used=set(); total_geom_bytes=0
    for row in fov_rows:
        if len(row)!=4:
            raise SpatialReadError('map_spatial_unexpected_fov_row')
        map_id,level,occlusion_applied,raw=row
        if level not in LEVELS:
            continue
        if map_id not in lookup:
            continue
        marker=(map_id,level)
        if marker in used:
            raise SpatialReadError('map_spatial_duplicate_fov_level')
        used.add(marker)
        total_geom_bytes+=len(raw.encode('utf-8')) if isinstance(raw,str) else 0
        if total_geom_bytes>MAX_GEOMETRY_BYTES:
            raise SpatialReadError('map_spatial_response_too_large')
        item={'level':level,'geometry':_geometry(raw),
              'occlusion_applied':occlusion_applied is True,
              'status':'modelled_unverified'}
        lookup[map_id]['fov_levels'].append(item)
    for item in cameras:
        item['fov_levels'].sort(key=lambda x:LEVELS.index(x['level']))
    data=json.dumps(cameras,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    digest=hashlib.sha256(data.encode('utf-8')).hexdigest()
    return {'schema_version':'1.0',
            'status':'available','source':'map_postgresql_readonly',
            'crs':'EPSG:4326','spatial_revision':'mapdb-spatial:'+digest[:16],
            'sha256':digest,
            'counts':{'map_cameras':len(point_rows),
                      'displayable_points':len(cameras),
                      'registry_linked':len(cameras)-unlinked,
                      'unlinked_map_cameras':unlinked,
                      'missing_position':null_points,
                      'with_fov':sum(bool(c['fov_levels']) for c in cameras),
                      'with_video':sum(bool(c['has_video']) for c in cameras)},
            'cameras':cameras,
            'disclaimer':'Modelled FOV is not verified surveillance coverage; unknown map registry links remain unbound.'}


def read_map_spatial(*, dsn, connect=None):
    if not isinstance(dsn,str) or not dsn.strip():
        raise SpatialReadError('map_spatial_db_not_configured')
    if connect is None:
        try: import psycopg
        except ImportError:
            raise SpatialReadError('map_spatial_driver_unavailable') from None
        connect=psycopg.connect
    try:
        with connect(dsn,connect_timeout=4) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '6000ms'")
                cursor.execute(POINTS_SQL)
                points=cursor.fetchall()
                if len(points)>MAX_CAMERAS:
                    raise SpatialReadError('map_spatial_too_many_cameras')
                cursor.execute(FOV_SQL)
                fov=cursor.fetchall()
                if len(fov)>MAX_FOV_ROWS:
                    raise SpatialReadError('map_spatial_too_many_fov_rows')
                # has_video 是增量字段：旧部署可能还没给只读账号授权 cameras_snapshot。
                # 用 savepoint 降级为「全部无视频」，不能因为一个可选字段让整张地图 503。
                has_savepoint = False
                try:
                    cursor.execute('SAVEPOINT map_video')
                    has_savepoint = True
                except Exception:
                    pass
                try:
                    cursor.execute(VIDEO_SQL)
                    video = cursor.fetchall()
                except Exception:
                    video = []
                    if has_savepoint:
                        try:
                            cursor.execute('ROLLBACK TO SAVEPOINT map_video')
                        except Exception:
                            pass
                else:
                    if has_savepoint:
                        try:
                            cursor.execute('RELEASE SAVEPOINT map_video')
                        except Exception:
                            pass
                if len(video) > MAX_CAMERAS:
                    # 可选字段超限时截断并保持地图可用，不升级成整页 503
                    video = video[:MAX_CAMERAS]
    except SpatialReadError:
        raise
    except Exception:
        raise SpatialReadError('map_spatial_query_unavailable') from None
    return build_spatial_payload(points,fov,video)
