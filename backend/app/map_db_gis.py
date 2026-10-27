"""M2c viewport-bound read-only GIS: OSM baselines + manual revisions/tombstones.

All layers come from TrajNexa_Map PostgreSQL in ONE repeatable-read transaction.
No edits, no write-back, no inference of camera origin, coverage or movement.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from app.map_db_spatial import build_spatial_payload, SpatialReadError

MAX_FEATURES_PER_LAYER = 400
MAX_CAMERAS = 500
MAX_FOV = 1500
MAX_JSON_BYTES = 8 * 1024 * 1024

class ViewportError(ValueError):
    """Safe public error code, intentionally excludes database credentials."""


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    """Restrict geographic extent so accidental national scans fail closed."""
    try:
        parts = value.split(',')
        if len(parts) != 4:
            raise ValueError()
        w, s, e, n = (float(part) for part in parts)
        if not all(math.isfinite(x) for x in (w, s, e, n)):
            raise ValueError()
        if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
            raise ValueError()
    except (AttributeError, TypeError, ValueError):
        raise ViewportError('gis_invalid_bbox') from None
    if e - w > .18 or n - s > .18:
        raise ViewportError('gis_bbox_too_wide_zoom_in')
    return (w, s, e, n)


# Import baselines are never modified by manual edits. Any revision/tombstone
# suppresses its original source, including when a revision moved outside
# the viewport. Edits are added from the separate MapFeature table only
# when their final geometry is visible and not deleted.
ROADS_SQL = """
WITH box AS (SELECT ST_MakeEnvelope(%s,%s,%s,%s,4326) AS geom),
features AS (
 SELECT 'osm-road:' || r.osm_id::text AS fid, 'osm_road'::text AS source_key,
        r.osm_id::text AS source_ref, r.name, r.highway::text AS category,
        ST_AsGeoJSON(r.geom,6) AS geo
 FROM public.cameras_osm_road r, box
 WHERE r.geom && box.geom AND ST_Intersects(r.geom,box.geom)
   AND NOT EXISTS (
      SELECT 1 FROM public.cameras_map_feature m
      WHERE m.source_key='osm_road' AND m.source_ref=r.osm_id::text
   )
 UNION ALL
 SELECT 'map-edit:' || m.id::text, m.source_key, m.source_ref, m.name,
        m.kind::text, ST_AsGeoJSON(m.geom,6)
 FROM public.cameras_map_feature m, box
 WHERE m.kind='road' AND m.is_deleted=FALSE
   AND m.source_key IN ('osm_road','manual')
   AND m.geom && box.geom AND ST_Intersects(m.geom,box.geom)
)
SELECT fid, source_key, source_ref, name, category, geo
FROM features ORDER BY fid LIMIT 401
"""

BUILDINGS_SQL = """
WITH box AS (SELECT ST_MakeEnvelope(%s,%s,%s,%s,4326) AS geom),
origins AS (
 SELECT b.id, b.geom,
        CASE WHEN bs.id IS NOT NULL THEN 'sinobf1'
             WHEN b.osm_id <= -9000000000 THEN 'promoted'
             ELSE 'osm' END AS source_key,
        CASE WHEN bs.id IS NOT NULL THEN bs.id::text
             ELSE b.osm_id::text END AS source_ref
 FROM public.cameras_building b
 LEFT JOIN public.cameras_building_source bs
        ON bs.promoted_building_id=b.id
 CROSS JOIN box
 WHERE b.geom && box.geom AND ST_Intersects(b.geom,box.geom)
),
features AS (
 SELECT 'base-building:' || o.id::text AS fid, o.source_key, o.source_ref,
        ''::text AS name, 'building'::text AS category,
        ST_AsGeoJSON(o.geom,6) AS geo
 FROM origins o
 WHERE NOT EXISTS (
      SELECT 1 FROM public.cameras_map_feature m
      WHERE m.kind='building' AND m.source_key=o.source_key AND m.source_ref=o.source_ref
 )
 UNION ALL
 SELECT 'map-edit:' || m.id::text, m.source_key, m.source_ref, m.name,
        m.kind::text, ST_AsGeoJSON(m.geom,6)
 FROM public.cameras_map_feature m, box
 WHERE m.kind='building' AND m.is_deleted=FALSE
   AND m.source_key IN ('osm','sinobf1','promoted','manual')
   AND m.geom && box.geom AND ST_Intersects(m.geom,box.geom)
)
SELECT fid, source_key, source_ref, name, category, geo
FROM features ORDER BY fid LIMIT 401
"""

CAMERAS_SQL = """
WITH box AS (SELECT ST_MakeEnvelope(%s,%s,%s,%s,4326) AS geom)
SELECT m.id, r.id, COALESCE(NULLIF(r.name,''), tags.legacy_name, ''), ST_X(m.location), ST_Y(m.location),
       m.direction, m.angle, m.camera_type, m.effective_range_m,
       r.pose_quality, r.position_quality, r.enabled, r.horizontal_fov_deg
FROM public.cameras_camera m
LEFT JOIN public.cameras_registry r
       ON r.map_camera_id=m.id AND r.deleted_at IS NULL
LEFT JOIN LATERAL (
  SELECT MAX(t.value) AS legacy_name
  FROM public.cameras_cameratags t
  WHERE t.camera_id_id=m.id AND t.name='name'
) tags ON TRUE
CROSS JOIN box
WHERE m.location && box.geom AND ST_Intersects(m.location,box.geom)
ORDER BY m.id LIMIT 501
"""

FOV_SQL = """
WITH box AS (SELECT ST_MakeEnvelope(%s,%s,%s,%s,4326) AS geom)
SELECT cf.camera_id_id, cf.level, cf.with_intersection, ST_AsGeoJSON(cf.geom,6)
FROM public.cameras_camerafocus cf
JOIN public.cameras_camera m ON m.id=cf.camera_id_id
CROSS JOIN box
WHERE cf.scenario='mean' AND cf.geom IS NOT NULL
  AND m.location && box.geom AND ST_Intersects(m.location,box.geom)
ORDER BY cf.camera_id_id, cf.level LIMIT 1501
"""


def _feature_collection(rows: list[tuple], *, layer: str) -> dict[str, Any]:
    if len(rows) > MAX_FEATURES_PER_LAYER:
        raise ViewportError('gis_too_many_' + layer + '_zoom_in')
    features = []
    for row in rows:
        if len(row) != 6:
            raise ViewportError('gis_invalid_layer_row')
        fid, source_key, source_ref, name, category, raw = row
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_JSON_BYTES:
            raise ViewportError('gis_invalid_geometry')
        try:
            geometry = json.loads(raw)
        except (TypeError, ValueError):
            raise ViewportError('gis_invalid_geometry') from None
        required_types = ('LineString','MultiLineString') if layer=='roads' else ('Polygon','MultiPolygon')
        if not isinstance(geometry, dict) or geometry.get('type') not in required_types:
            raise ViewportError('gis_invalid_geometry')
        features.append({
            'type':'Feature','id':str(fid),
            'geometry':geometry,
            'properties':{'kind':'road' if layer=='roads' else 'building',
                          'name':str(name or '')[:200],
                          'category':str(category or '')[:80],
                          'source_key':str(source_key or '')[:32],
                          'source_ref':str(source_ref or '')[:100],
                          'reviewed_geometry':str(fid).startswith('map-edit:')},
        })
    return {'type':'FeatureCollection','features':features}


def assemble_viewport(bbox, roads, buildings, cameras, fov):
    if len(cameras)>MAX_CAMERAS:
        raise ViewportError('gis_too_many_cameras_zoom_in')
    if len(fov)>MAX_FOV:
        raise ViewportError('gis_too_many_fov_zoom_in')
    roads_fc = _feature_collection(roads,layer='roads')
    buildings_fc = _feature_collection(buildings,layer='buildings')
    try:
        spatial = build_spatial_payload(cameras,fov)
    except SpatialReadError:
        raise ViewportError('gis_invalid_camera_geometry') from None
    result = {
        'schema_version':'1.0','status':'available',
        'source':'map_postgresql_readonly','crs':'EPSG:4326',
        'bbox':list(bbox),'truncated':False,
        'roads':roads_fc,'buildings':buildings_fc,
        'cameras':spatial['cameras'],
        'counts':{'roads':len(roads_fc['features']),
                  'buildings':len(buildings_fc['features']),
                  **spatial['counts']},
        'warnings':['read_only_modelled_fov_not_verified',
                    'map_source_baselines_with_manual_overlay_priority'],
    }
    packed=json.dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    if len(packed.encode('utf-8'))>MAX_JSON_BYTES:
        raise ViewportError('gis_payload_too_large_zoom_in')
    result['spatial_revision']='viewport:'+hashlib.sha256(packed.encode('utf-8')).hexdigest()[:16]
    return result


def read_gis_viewport(*,dsn:str,bbox:str,connect=None):
    bounds=parse_bbox(bbox)
    if not isinstance(dsn,str) or not dsn.strip():
        raise ViewportError('gis_map_db_not_configured')
    if connect is None:
        try:import psycopg
        except ImportError:
            raise ViewportError('gis_postgresql_driver_unavailable') from None
        connect=psycopg.connect
    try:
        with connect(dsn,connect_timeout=4) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '6000ms'")
                results=[]
                for sql, maximum in ((ROADS_SQL,MAX_FEATURES_PER_LAYER),
                                     (BUILDINGS_SQL,MAX_FEATURES_PER_LAYER),
                                     (CAMERAS_SQL,MAX_CAMERAS),(FOV_SQL,MAX_FOV)):
                    cursor.execute(sql,bounds)
                    rows=cursor.fetchall()
                    if len(rows)>maximum:
                        raise ViewportError('gis_viewport_limit_exceeded_zoom_in')
                    results.append(rows)
    except ViewportError:
        raise
    except Exception:
        # Do not leak database URL, credentials, SQL or internal table names.
        raise ViewportError('gis_map_db_query_unavailable') from None
    return assemble_viewport(bounds,*results)


ANCHOR_SQL = """
SELECT ST_Y(location), ST_X(location)
FROM public.cameras_camera
WHERE location IS NOT NULL
ORDER BY id LIMIT 1
"""

def read_gis_anchor(*, dsn: str, connect=None) -> dict:
    """First real Map camera as an initial viewport hint, never a device bind."""
    if not isinstance(dsn,str) or not dsn.strip():
        raise ViewportError('gis_map_db_not_configured')
    if connect is None:
        try: import psycopg
        except ImportError:
            raise ViewportError('gis_postgresql_driver_unavailable') from None
        connect=psycopg.connect
    try:
        with connect(dsn,connect_timeout=4) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '4000ms'")
                cursor.execute(ANCHOR_SQL)
                row=cursor.fetchone()
    except Exception:
        raise ViewportError('gis_map_db_query_unavailable') from None
    if row is None:
        return {'status':'available','center':None,'crs':'EPSG:4326',
                'source':'map_postgresql_readonly'}
    if len(row)!=2:
        raise ViewportError('gis_invalid_anchor')
    lat,lon=row
    try:
        if lat is None or lon is None or not math.isfinite(float(lat)) or not math.isfinite(float(lon)):
            raise ValueError()
        if not -90 <= float(lat) <=90 or not -180 <= float(lon) <=180:
            raise ValueError()
    except (TypeError, ValueError):
        raise ViewportError('gis_invalid_anchor') from None
    return {'status':'available','center':[round(float(lat),6),round(float(lon),6)],
            'crs':'EPSG:4326','source':'map_postgresql_readonly'}
