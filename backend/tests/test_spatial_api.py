"""M2b endpoint is live-only; no fictitious geometry fallback."""
from app.config import settings
from app import main as app_main
from app import map_db_spatial

def test_not_configured_returns_503(env,monkeypatch):
    client,_,_=env
    monkeypatch.setattr(settings,'map_db_readonly_url','')
    r=client.get('/api/v1/spatial/cameras')
    assert r.status_code==503
    assert r.json()['error']['code']=='map_spatial_db_not_configured'

def test_configured_success_does_not_guess_geometry(env,monkeypatch):
    client,_,_=env
    monkeypatch.setattr(settings,'map_db_readonly_url','postgresql://readonly:secret@map/db')
    # the route calls the module attribute, so patch it on the module
    monkeypatch.setattr(map_db_spatial,'read_map_spatial',lambda **k: {
        'status':'available','source':'map_postgresql_readonly',
        'spatial_revision':'mapdb-spatial:abc','counts':{'map_cameras':0},
        'cameras':[]})
    r=client.get('/api/v1/spatial/cameras')
    assert r.status_code==200
    assert r.json()['cameras']==[]

def test_database_failure_does_not_fabricate_fallback(env,monkeypatch):
    client,_,_=env
    monkeypatch.setattr(settings,'map_db_readonly_url','postgresql://readonly:secret@map/db')
    def fail(**k):raise map_db_spatial.SpatialReadError('map_spatial_query_unavailable')
    monkeypatch.setattr(map_db_spatial,'read_map_spatial',fail)
    r=client.get('/api/v1/spatial/cameras')
    assert r.status_code==503
    assert r.json()['error']['code']=='map_spatial_query_unavailable'
    assert 'secret' not in str(r.json())
