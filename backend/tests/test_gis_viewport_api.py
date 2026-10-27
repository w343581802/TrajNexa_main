"""M2c live-only Map GIS viewport endpoint; does not substitute mock data."""
from app.config import settings
from app import map_db_gis

def test_invalid_bbox_is_422(env):
    client,_,_=env
    response=client.get('/api/v1/gis/viewport?bbox=bad')
    assert response.status_code==422
    assert response.json()['error']['code']=='gis_invalid_bbox'

def test_missing_map_connection_is_503(env,monkeypatch):
    client,_,_=env
    monkeypatch.setattr(settings,'map_db_readonly_url','')
    response=client.get('/api/v1/gis/viewport?bbox=110.1,25.1,110.2,25.2')
    assert response.status_code==503
    assert response.json()['error']['code']=='gis_map_db_not_configured'

def test_live_result_not_mock(env,monkeypatch):
    client,_,_=env
    monkeypatch.setattr(settings,'map_db_readonly_url','postgresql://read:fake@map/db')
    monkeypatch.setattr(map_db_gis,'read_gis_viewport',lambda **kw: {
        'status':'available','roads':{'type':'FeatureCollection','features':[]},
        'buildings':{'type':'FeatureCollection','features':[]},
        'cameras':[],'truncated':False})
    response=client.get('/api/v1/gis/viewport?bbox=110.1,25.1,110.2,25.2')
    assert response.status_code==200
    assert response.json()['cameras']==[]

def test_failure_does_not_fallback_to_fake_features(env,monkeypatch):
    client,_,_=env
    monkeypatch.setattr(settings,'map_db_readonly_url','postgresql://read:secret@map/db')
    def fail(**kw):raise map_db_gis.ViewportError('gis_map_db_query_unavailable')
    monkeypatch.setattr(map_db_gis,'read_gis_viewport',fail)
    response=client.get('/api/v1/gis/viewport?bbox=110.1,25.1,110.2,25.2')
    assert response.status_code==503
    assert response.json()['error']['code']=='gis_map_db_query_unavailable'
    assert 'secret' not in str(response.json())
