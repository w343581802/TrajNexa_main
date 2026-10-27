"""M2c: source precedence, bounded queries, read-only & no manufactured facts."""
from uuid import UUID
import json
import pytest

from app.map_db_gis import (read_gis_viewport,assemble_viewport,parse_bbox,
                            ViewportError,ROADS_SQL,BUILDINGS_SQL,CAMERAS_SQL)

GEO_LINE=json.dumps({'type':'LineString','coordinates':[[110.1,25.1],[110.2,25.2]]})
GEO_POLY=json.dumps({'type':'Polygon','coordinates':[[[110.1,25.1],[110.2,25.1],
                                    [110.2,25.2],[110.1,25.1]]]})
BBOX='110.10,25.10,110.15,25.15'

def row_line(source='osm_road',fid='osm-road:12'):
    return (fid,source,'12','中仁路','residential',GEO_LINE)

def row_building(source='osm',fid='base-building:3'):
    return (fid,source,'20','','building',GEO_POLY)

def camera(map_id=8,uuid=None):
    return (map_id,UUID(uuid) if uuid else None,'摄像头' if uuid else '',
            110.12,25.12,45,30,'fixed',None,'calibrated','verified',True,None)

def test_valid_viewport_and_json():
    a=parse_bbox(BBOX)
    out=assemble_viewport(a,[row_line()],[row_building()],[camera()],[])
    assert out['status']=='available' and not out['truncated']
    assert out['counts']['roads']==1 and out['counts']['buildings']==1
    assert out['cameras'][0]['camera_uuid'] is None
    assert out['cameras'][0]['map_camera_id']=='8'
    assert out['cameras'][0]['pose_quality']=='unknown'
    assert out['roads']['features'][0]['properties']['source_key']=='osm_road'
    assert out['spatial_revision'].startswith('viewport:')

def test_manual_edit_wins_and_never_rebuilt_as_imported():
    assert "NOT EXISTS" in ROADS_SQL and "m.source_ref=r.osm_id::text" in ROADS_SQL
    assert "m.is_deleted=FALSE" in ROADS_SQL
    assert "NOT EXISTS" in BUILDINGS_SQL and "bs.promoted_building_id=b.id" in BUILDINGS_SQL
    result=assemble_viewport(parse_bbox(BBOX),
        [row_line('manual','map-edit:abc')],
        [row_building('sinobf1','map-edit:def')],[],[])
    assert result['roads']['features'][0]['properties']['reviewed_geometry'] is True
    assert result['buildings']['features'][0]['properties']['source_key']=='sinobf1'

def test_revision_changes_on_layer_content():
    base=assemble_viewport(parse_bbox(BBOX),[row_line()],[],[],[])
    changed=assemble_viewport(parse_bbox(BBOX),[row_line(fid='osm-road:13')],[],[],[])
    assert base['spatial_revision'] != changed['spatial_revision']

@pytest.mark.parametrize('bad',[
    'xyz','110,25,110,26','110,25,109,24','200,25,201,26',
    '110,25,110.1,nan','109.0,25,109.5,25.2',
])
def test_bad_or_too_large_bbox_fails_closed(bad):
    with pytest.raises(ViewportError):
        parse_bbox(bad)

def test_reject_excess_instead_of_silent_truncation():
    from app.map_db_gis import MAX_FEATURES_PER_LAYER
    with pytest.raises(ViewportError, match='gis_too_many_roads'):
        assemble_viewport(parse_bbox(BBOX),[row_line()]*(MAX_FEATURES_PER_LAYER+1),[],[],[])

def test_non_linestring_road_rejected():
    with pytest.raises(ViewportError,match='gis_invalid_geometry'):
        assemble_viewport(parse_bbox(BBOX),[
            ('road-x','osm_road','x','','',GEO_POLY)],[],[],[])

class Cursor:
    def __init__(self,sequence):
        self.sequence=iter(sequence);self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
    def fetchall(self):return next(self.sequence)
class Conn:
    def __init__(self,rows):self.cur=Cursor(rows)
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def cursor(self):return self.cur

def test_query_is_one_ro_transaction_bbox_params_and_no_write():
    conn=Conn([[row_line()],[row_building()],[camera()],[]])
    result=read_gis_viewport(dsn='postgresql://map-readonly@map/db',
        bbox=BBOX,connect=lambda url,connect_timeout:conn)
    assert result['counts']['roads']==1
    assert conn.cur.calls[0][0].startswith('SET TRANSACTION')
    assert 'READ ONLY' in conn.cur.calls[0][0]
    assert len(conn.cur.calls)==6
    for query,parameters in conn.cur.calls[2:]:
        assert query.lstrip().startswith('WITH box')
        assert parameters==parse_bbox(BBOX)
        assert 'ST_Intersects' in query
    assert not any('UPDATE ' in q or 'DELETE FROM' in q for q,p in conn.cur.calls)

def test_db_error_never_leaks_password():
    def fail(*args,**kwargs):raise ValueError('secret_password')
    with pytest.raises(ViewportError,match='gis_map_db_query_unavailable') as e:
        read_gis_viewport(dsn='secret_password',bbox=BBOX,connect=fail)
    assert 'secret_password' not in str(e.value)


def test_unlinked_legacy_camera_name_from_map_tags_not_uuid():
    assert "cameras_cameratags" in CAMERAS_SQL
    assert "COALESCE(NULLIF(r.name,''), tags.legacy_name, '')" in CAMERAS_SQL
    cam = list(camera(uuid=None));cam[2]='来自旧地图的人工摄像头名称'
    out=assemble_viewport(parse_bbox(BBOX),[],[],[tuple(cam)],[])
    assert out['cameras'][0]['display_name']=='来自旧地图的人工摄像头名称'
    assert out['cameras'][0]['camera_uuid'] is None



def test_anchor_readonly_and_no_fabrication():
    from app.map_db_gis import read_gis_anchor
    class AnchorCursor:
        def __init__(self, row): self.row=row;self.queries=[]
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql):self.queries.append(sql)
        def fetchone(self):return self.row
    class AnchorConn:
        def __init__(self,row):self.cursor_obj=AnchorCursor(row)
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return self.cursor_obj
    db=AnchorConn((25.25,110.21))
    result=read_gis_anchor(dsn='postgresql://readonly@map/db',
                connect=lambda dsn,connect_timeout:db)
    assert result['center']==[25.25,110.21]
    assert 'READ ONLY' in db.cursor_obj.queries[0]
    assert read_gis_anchor(dsn='x',
        connect=lambda dsn,connect_timeout:AnchorConn(None))['center'] is None
