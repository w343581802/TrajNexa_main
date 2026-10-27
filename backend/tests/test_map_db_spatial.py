"""GIS Map DB adapter contract tests. No real database nor geography claimed."""
import json
from uuid import UUID
import pytest
from app.map_db_spatial import build_spatial_payload,read_map_spatial,SpatialReadError

A='11111111-1111-4111-8111-111111111111'
POLY=json.dumps({'type':'Polygon','coordinates':[[[110,25],[110.001,25],
    [110.001,25.001],[110,25]]]},separators=(',',':'))

def point(map_id=1,uuid=A,bearing=90,tilt=35,pose='calibrated',lat=25):
    return (map_id,UUID(uuid) if uuid else None,'测试镜头',110.0,lat,
            bearing,tilt,'fixed',80.0,pose,'verified',True,75.0)

def test_position_and_tilt_not_confused_with_fov():
    x=build_spatial_payload([point()],[(1,'observation',False,POLY)])
    cam=x['cameras'][0]
    assert cam['camera_uuid']==A
    assert cam['map_camera_id']=='1'
    assert cam['tilt_deg']==35 and cam['horizontal_fov_deg']==75
    assert cam['bearing_deg']==90
    assert cam['pose_quality']=='calibrated'
    assert cam['fov_levels'][0]['status']=='modelled_unverified'
    assert cam['fov_levels'][0]['occlusion_applied'] is False
    assert x['counts']['with_fov']==1

def test_map_only_camera_never_gets_invented_uuid():
    x=build_spatial_payload([point(uuid=None)],[])
    cam=x['cameras'][0]
    assert cam['camera_uuid'] is None
    assert cam['map_registry_link']=='map_only_unbound'
    assert cam['pose_quality']=='unknown'
    assert cam['fov_levels']==[]
    assert x['counts']['unlinked_map_cameras']==1

def test_unverified_registry_pose_stays_unverified():
    cam=build_spatial_payload([point(pose='reported')],[])['cameras'][0]
    assert cam['pose_quality']=='reported'

@pytest.mark.parametrize('bad',[-300,400,float('nan')])
def test_invalid_longitude_is_refused(bad):
    row=list(point());row[3]=bad
    with pytest.raises(SpatialReadError):
        build_spatial_payload([tuple(row)],[])

def test_geometry_invalid_is_refused_not_replaced():
    with pytest.raises(SpatialReadError,match='map_spatial_invalid_geometry'):
        build_spatial_payload([point()],[(1,'observation',True,'{"type":"Point","coordinates":[0,0]}')])

def test_duplicate_uuid_or_geom_level_is_rejected():
    with pytest.raises(SpatialReadError,match='map_spatial_duplicate_uuid'):
        build_spatial_payload([point(1),point(2)],[])
    with pytest.raises(SpatialReadError,match='map_spatial_duplicate_fov_level'):
        build_spatial_payload([point()],[(1,'observation',True,POLY),(1,'observation',True,POLY)])

def test_revision_changes_if_geometry_or_pose_changes():
    a=build_spatial_payload([point()],[])['spatial_revision']
    b=build_spatial_payload([point(tilt=37)],[])['spatial_revision']
    assert a!=b

def test_empty_database_is_explicit_empty_not_mock():
    x=build_spatial_payload([],[])
    assert x['status']=='available'
    assert x['counts']['map_cameras']==0 and x['cameras']==[]

class Cursor:
    def __init__(self):self.calls=[]
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def execute(self,sql):self.calls.append(sql)
    def fetchall(self):
        return [point()] if 'ST_X' in self.calls[-1] else [(1,'recognition',True,POLY)]
class Conn:
    def __init__(self):self.cur=Cursor()
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def cursor(self):return self.cur

def test_queries_are_readonly_and_one_repeatable_read_transaction():
    conn=Conn()
    result=read_map_spatial(dsn='postgresql://readonly:test@map/db',
        connect=lambda dsn,connect_timeout:conn)
    calls=conn.cur.calls
    assert calls[0].startswith('SET TRANSACTION') and 'READ ONLY' in calls[0]
    assert len([x for x in calls if x.lstrip().startswith('SELECT')])==2
    assert 'ST_AsGeoJSON' in calls[-1]
    assert result['cameras'][0]['fov_levels'][0]['occlusion_applied'] is True

def test_database_exception_is_sanitized():
    def bad(*a,**k):raise RuntimeError('private_password')
    with pytest.raises(SpatialReadError,match='map_spatial_query_unavailable') as e:
        read_map_spatial(dsn='private_password',connect=bad)
    assert 'private_password' not in str(e.value)
