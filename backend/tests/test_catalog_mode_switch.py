"""Test opt-in direct DB mode and non-fallback on database unavailability."""
import json
from app import camera_catalog,map_db_catalog
from app.config import settings

A='11111111-1111-4111-8111-111111111111'
def test_opt_in_mode_and_explicit_local_path(tmp_path,monkeypatch):
    local=tmp_path/'fixture.json'
    local.write_text(json.dumps({'schema_version':'1.0','spatial_revision':'file-rev',
       'cameras':[{'camera_uuid':A,'display_name':'local','aliases':[]}]}),encoding='utf8')
    monkeypatch.setattr(settings,'map_db_readonly_url','postgresql://readonly:pwd@postgis/test')
    monkeypatch.setattr(settings,'storage_root',tmp_path)
    monkeypatch.setattr(map_db_catalog,'read_map_directory',
       lambda **k:([{'camera_uuid':A,'display_name':'live','aliases':[],'active':True}],
                   'mapdb-directory:00001','digest',{'live_read':True}))
    assert camera_catalog.load_catalog().cameras[0]['display_name']=='live'
    assert camera_catalog.load_catalog().source=='map_postgresql_readonly'
    assert camera_catalog.load_catalog(local).cameras[0]['display_name']=='local'

def test_connection_failure_does_not_use_stale_file(tmp_path,monkeypatch):
    (tmp_path/'camera_catalog.json').write_text(json.dumps({
        'schema_version':'1.0','spatial_revision':'old',
        'cameras':[{'camera_uuid':A,'display_name':'stale','aliases':[]}]}))
    monkeypatch.setattr(settings,'map_db_readonly_url','postgresql://readonly:test@postgis/db')
    monkeypatch.setattr(settings,'storage_root',tmp_path)
    def fail(**kwargs):raise map_db_catalog.MapDBCatalogError('map_db_query_unavailable')
    monkeypatch.setattr(map_db_catalog,'read_map_directory',fail)
    status=camera_catalog.catalog_status()
    assert status['status']=='unavailable'
    assert status['reason']=='map_db_query_unavailable'
    assert status['cameras']==[]
