"""Demo-only SELECT-only read of Map OSM roads and buildings for the GIS view.

Roads and buildings are real OSM geometry stored in TrajNexa_Map. Queries are
bbox-scoped, simplified by zoom, and rejected (never truncated) when a view
returns too much data. The response is ODbL data, so attribution is mandatory.
"""

from __future__ import annotations

import hashlib
import json
import math

MAX_ROADS = 6000
MAX_BUILDINGS = 6000
MAX_GEOMETRY_BYTES = 8 * 1024 * 1024
MAX_BBOX_SPAN = 0.5
MIN_BBOX_SPAN = 0.0004
MIN_BUILDING_ZOOM = 15
MIN_ZOOM = 10
MAX_ZOOM = 19
ATTRIBUTION = '© OpenStreetMap contributors (ODbL)'

# tolerance in degrees: ~111km/deg, so 0.0001 deg is roughly 11 m
TOLERANCE = {
    10: 0.0012, 11: 0.0012, 12: 0.001, 13: 0.0006, 14: 0.00035,
    15: 0.0002, 16: 0.00012, 17: 0.00006, 18: 0.00003, 19: 0.000015,
}

ROADS_SQL = """
SELECT osm_id, highway, name,
       ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, %s), 6)
FROM public.cameras_osm_road
WHERE geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
ORDER BY osm_id LIMIT %s
"""

BUILDINGS_SQL = """
SELECT id, ST_AsGeoJSON(ST_SimplifyPreserveTopology(geom, %s), 6)
FROM public.cameras_building
WHERE geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)
ORDER BY id LIMIT %s
"""


class BasemapReadError(ValueError):
    """Public sanitized error code; database errors might contain credentials."""


def parse_bbox(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise BasemapReadError('map_basemap_invalid_bbox')
    parts = raw.split(',')
    if len(parts) != 4:
        raise BasemapReadError('map_basemap_invalid_bbox')
    try:
        west, south, east, north = (float(p) for p in parts)
    except (TypeError, ValueError):
        raise BasemapReadError('map_basemap_invalid_bbox') from None
    for value in (west, south, east, north):
        if not math.isfinite(value):
            raise BasemapReadError('map_basemap_invalid_bbox')
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise BasemapReadError('map_basemap_invalid_bbox')
    span_lon = east - west
    span_lat = north - south
    if span_lon > MAX_BBOX_SPAN or span_lat > MAX_BBOX_SPAN:
        raise BasemapReadError('map_basemap_bbox_too_large')
    if span_lon < MIN_BBOX_SPAN or span_lat < MIN_BBOX_SPAN:
        raise BasemapReadError('map_basemap_bbox_too_small')
    return west, south, east, north, span_lon, span_lat


def normalize_zoom(raw):
    try:
        zoom = int(raw)
    except (TypeError, ValueError):
        raise BasemapReadError('map_basemap_invalid_zoom') from None
    if not MIN_ZOOM <= zoom <= MAX_ZOOM:
        raise BasemapReadError('map_basemap_invalid_zoom')
    return zoom


def tolerance_for_zoom(zoom):
    return TOLERANCE[max(MIN_ZOOM, min(MAX_ZOOM, zoom))]


def _geometry(raw, allowed):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_GEOMETRY_BYTES:
        raise BasemapReadError('map_basemap_invalid_geometry')
    try:
        geom = json.loads(raw)
    except (ValueError, TypeError):
        raise BasemapReadError('map_basemap_invalid_geometry') from None
    if not isinstance(geom, dict) or geom.get('type') not in allowed:
        raise BasemapReadError('map_basemap_invalid_geometry')
    coords = geom.get('coordinates')
    if coords is None:
        raise BasemapReadError('map_basemap_invalid_geometry')
    return geom


def _count_points(geom):
    total = 0
    stack = [geom.get('coordinates')]
    while stack:
        item = stack.pop()
        if not isinstance(item, list):
            raise BasemapReadError('map_basemap_invalid_geometry')
        if item and isinstance(item[0], (int, float)):
            if len(item) < 2:
                raise BasemapReadError('map_basemap_invalid_geometry')
            total += 1
            if total > 20000:
                raise BasemapReadError('map_basemap_feature_too_complex')
            continue
        stack.extend(item)
    return total


def build_basemap_payload(*, bbox, zoom, roads, buildings, buildings_skipped):
    if len(roads) > MAX_ROADS:
        raise BasemapReadError('map_basemap_too_many_roads')
    if len(buildings) > MAX_BUILDINGS:
        raise BasemapReadError('map_basemap_too_many_buildings')
    road_items = []
    for row in roads:
        if len(row) != 4:
            raise BasemapReadError('map_basemap_unexpected_road_row')
        osm_id, highway, name, raw = row
        if not isinstance(osm_id, int):
            raise BasemapReadError('map_basemap_invalid_road_id')
        geom = _geometry(raw, ('LineString', 'MultiLineString'))
        _count_points(geom)
        road_items.append({
            'osm_id': str(osm_id),
            'class': str(highway or '')[:32] or None,
            'name': str(name or '')[:120] or None,
            'geometry': geom,
        })
    building_items = []
    for row in buildings:
        if len(row) != 2:
            raise BasemapReadError('map_basemap_unexpected_building_row')
        building_id, raw = row
        if not isinstance(building_id, int):
            raise BasemapReadError('map_basemap_invalid_building_id')
        geom = _geometry(raw, ('Polygon', 'MultiPolygon'))
        _count_points(geom)
        building_items.append({'id': str(building_id), 'geometry': geom})
    payload = {
        'schema_version': '1.0',
        'status': 'available',
        'source': 'map_postgresql_readonly',
        'crs': 'EPSG:4326',
        'bbox': list(bbox),
        'zoom': zoom,
        'tolerance_deg': tolerance_for_zoom(zoom),
        'attribution': ATTRIBUTION,
        'counts': {
            'roads': len(road_items),
            'buildings': len(building_items),
            'buildings_skipped_below_zoom': buildings_skipped,
        },
        'roads': road_items,
        'buildings': building_items,
        'disclaimer': (
            'Roads and buildings are OSM data from the Map ledger; camera FOV is '
            'modelled geometry and does not prove real coverage.'
        ),
    }
    encoded = json.dumps(
        {'roads': road_items, 'buildings': building_items},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    )
    if len(encoded.encode('utf-8')) > MAX_GEOMETRY_BYTES:
        raise BasemapReadError('map_basemap_response_too_large')
    digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    return payload | {'basemap_revision': 'mapdb-basemap:' + digest[:16], 'sha256': digest}


def read_map_basemap(*, dsn, bbox, zoom, connect=None):
    # validate the request before touching configuration: bad input is a 400, not a 503
    west, south, east, north, _, _ = parse_bbox(bbox)
    zoom = normalize_zoom(zoom)
    if not isinstance(dsn, str) or not dsn.strip():
        raise BasemapReadError('map_basemap_db_not_configured')
    tolerance = tolerance_for_zoom(zoom)
    if connect is None:
        try:
            import psycopg
        except ImportError:
            raise BasemapReadError('map_basemap_driver_unavailable') from None
        connect = psycopg.connect
    buildings_skipped = zoom < MIN_BUILDING_ZOOM
    try:
        with connect(dsn, connect_timeout=4) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '6000ms'")
                cursor.execute(ROADS_SQL, (tolerance, west, south, east, north, MAX_ROADS + 1))
                roads = cursor.fetchall()
                if len(roads) > MAX_ROADS:
                    raise BasemapReadError('map_basemap_too_many_roads')
                buildings = []
                if not buildings_skipped:
                    cursor.execute(BUILDINGS_SQL, (tolerance, west, south, east, north, MAX_BUILDINGS + 1))
                    buildings = cursor.fetchall()
                    if len(buildings) > MAX_BUILDINGS:
                        raise BasemapReadError('map_basemap_too_many_buildings')
    except BasemapReadError:
        raise
    except Exception:
        raise BasemapReadError('map_basemap_query_unavailable') from None
    return build_basemap_payload(
        bbox=(west, south, east, north), zoom=zoom,
        roads=roads, buildings=buildings, buildings_skipped=buildings_skipped,
    )
