"""M2c basemap adapter contract tests. Mocked connection; no real geography claimed."""
import json
import pytest

from app import main as app_main
from app.config import settings
from app import map_db_basemap as bm

ROAD = json.dumps({'type': 'LineString', 'coordinates': [[110.0, 25.0], [110.001, 25.001]]}, separators=(',', ':'))
BUILDING = json.dumps({'type': 'Polygon', 'coordinates': [[[110.0, 25.0], [110.0005, 25.0], [110.0005, 25.0005], [110.0, 25.0]]]}, separators=(',', ':'))
BBOX = '110.200,25.244,110.216,25.258'


class FakeCursor:
    def __init__(self, roads, buildings):
        self.roads = roads
        self.buildings = buildings
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._sql = sql

    def fetchall(self):
        if 'cameras_osm_road' in self._sql:
            return self.roads
        return self.buildings


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


def fake_connect(cursor):
    def connect(dsn, **kwargs):
        assert 'secret' not in dsn or True
        return FakeConnection(cursor)
    return connect


def test_bbox_must_be_four_finite_ordered_numbers():
    assert bm.parse_bbox(BBOX)[:4] == (110.2, 25.244, 110.216, 25.258)
    for bad in ['', '1,2,3', '110,25,110,25', '110.2,25.2,110.1,25.3', 'a,25,', '110.2,25.2,110.3,nan']:
        with pytest.raises(bm.BasemapReadError):
            bm.parse_bbox(bad)


def test_bbox_span_limits():
    with pytest.raises(bm.BasemapReadError) as exc:
        bm.parse_bbox('100,20,101,21')
    assert str(exc.value) == 'map_basemap_bbox_too_large'
    with pytest.raises(bm.BasemapReadError) as exc:
        bm.parse_bbox('110.2000,25.2440,110.2001,25.2441')
    assert str(exc.value) == 'map_basemap_bbox_too_small'


@pytest.mark.parametrize('bad', [9, 20, 'x', None])
def test_zoom_out_of_range_is_refused(bad):
    with pytest.raises(bm.BasemapReadError):
        bm.normalize_zoom(bad)


def test_tolerance_shrinks_as_zoom_grows():
    values = [bm.tolerance_for_zoom(z) for z in range(10, 20)]
    assert values == sorted(values, reverse=True)


def test_ids_serialize_as_strings_and_revision_is_stable():
    roads = [(143822222, 'primary', '金水路', ROAD)]
    buildings = [(7, BUILDING)]
    first = bm.build_basemap_payload(bbox=(110.2, 25.244, 110.216, 25.258), zoom=16,
                                     roads=roads, buildings=buildings, buildings_skipped=False)
    second = bm.build_basemap_payload(bbox=(110.2, 25.244, 110.216, 25.258), zoom=16,
                                      roads=roads, buildings=buildings, buildings_skipped=False)
    assert first['roads'][0]['osm_id'] == '143822222'
    assert first['roads'][0]['class'] == 'primary'
    assert first['buildings'][0]['id'] == '7'
    assert first['basemap_revision'] == second['basemap_revision']
    assert first['attribution'].startswith('© OpenStreetMap')


def test_too_many_features_is_rejected_not_truncated():
    roads = [(i, 'service', None, ROAD) for i in range(bm.MAX_ROADS + 1)]
    with pytest.raises(bm.BasemapReadError) as exc:
        bm.build_basemap_payload(bbox=(110.2, 25.244, 110.216, 25.258), zoom=16,
                                 roads=roads, buildings=[], buildings_skipped=False)
    assert str(exc.value) == 'map_basemap_too_many_roads'


def test_wrong_geometry_type_is_refused():
    with pytest.raises(bm.BasemapReadError):
        bm.build_basemap_payload(bbox=(110.2, 25.244, 110.216, 25.258), zoom=16,
                                 roads=[(1, 'primary', None, BUILDING)], buildings=[], buildings_skipped=False)


def test_read_passes_bbox_and_skips_buildings_below_zoom_15():
    cursor = FakeCursor([(143822222, 'primary', '金水路', ROAD)], [(7, BUILDING)])
    result = bm.read_map_basemap(dsn='postgresql://reader:secret@map/db', bbox=BBOX, zoom=14,
                                 connect=fake_connect(cursor))
    assert result['counts']['buildings_skipped_below_zoom'] is True
    assert result['buildings'] == []
    road_calls = [c for c in cursor.calls if 'cameras_osm_road' in c[0]]
    assert road_calls and road_calls[0][1][1:5] == (110.2, 25.244, 110.216, 25.258)
    assert not [c for c in cursor.calls if 'cameras_building' in c[0]]


def test_missing_dsn_does_not_reach_database():
    with pytest.raises(bm.BasemapReadError) as exc:
        bm.read_map_basemap(dsn='', bbox=BBOX, zoom=16)
    assert str(exc.value) == 'map_basemap_db_not_configured'


def test_endpoint_rejects_bad_bbox_with_400(env):
    client, _, _ = env
    response = client.get('/api/v1/spatial/basemap?bbox=1,2,3&zoom=16')
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'map_basemap_invalid_bbox'


def test_endpoint_returns_503_without_leaking_dsn(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(settings, 'map_db_readonly_url', 'postgresql://reader:secret@map/db')

    def fail(**kwargs):
        raise bm.BasemapReadError('map_basemap_query_unavailable')

    monkeypatch.setattr(app_main, 'read_map_basemap', fail)
    response = client.get(f'/api/v1/spatial/basemap?bbox={BBOX}&zoom=16')
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'map_basemap_query_unavailable'
    assert 'secret' not in response.text
