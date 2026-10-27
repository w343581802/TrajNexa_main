"""No real database required: ensure PostgreSQL direct adapter fails closed."""
import json
from uuid import UUID
import pytest
from app.map_db_catalog import read_map_directory,MapDBCatalogError

A='11111111-1111-4111-8111-111111111111'
B='22222222-2222-4222-8222-222222222222'
DSN='postgresql://readonly:example@postgis:5432/trajnexa_map'
class Cursor:
    def __init__(self,rows):self.rows=rows;self.queries=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql):self.queries.append(sql)
    def fetchall(self):return self.rows
class Connection:
    def __init__(self,rows):self.cur=Cursor(rows)
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def cursor(self):return self.cur
def read(rows,alias_path=None):
    conn=Connection(rows)
    def connect(dsn,connect_timeout):
        assert dsn==DSN and connect_timeout<=5
        return conn
    result=read_map_directory(dsn=DSN,alias_path=alias_path,connect=connect)
    assert 'READ ONLY' in conn.cur.queries[0]
    assert conn.cur.queries[-1].startswith('SELECT ')
    assert 'deleted_at IS NULL' in conn.cur.queries[-1]
    return result

def test_uuids_status_and_revision():
    entries,rev,digest,diag=read([
        (UUID(A),'3-DX-中仁路98号-球',True,100,'index','ext'),
        (UUID(B),'其他摄像头',False,None,'','')])
    assert len(entries)==2 and entries[0]['camera_uuid']==A
    assert entries[0]['map_camera_id']==100 and not entries[1]['active']
    assert rev.startswith('mapdb-directory:') and len(digest)==64
    assert diag['spatial_revision'] is None

def test_unnamed_not_fabricated():
    entries,_,_,diag=read([(UUID(A),'',None,None,'','')])
    assert entries==[] and diag['unnamed_count']==1

def test_rename_changes_catalog_revision():
    assert read([(UUID(A),'名称A',True,None,'','')])[1] != read([(UUID(A),'名称B',True,None,'','')])[1]

def test_aliases_overlay_without_overwriting_live_name(tmp_path):
    file=tmp_path/'map_camera_aliases.json'
    file.write_text(json.dumps({'schema_version':'1.0','aliases_by_uuid':{
        A:['旧名','旧名','历史名'],B:['旧设备名']}},ensure_ascii=False),encoding='utf8')
    entries,_,_,diag=read([(UUID(A),'现名',None,None,'','')],file)
    assert entries[0]['display_name']=='现名'
    assert entries[0]['aliases']==['旧名','历史名']
    assert diag['orphaned_alias_uuids']==[B]

def test_invalid_aliases_rejected(tmp_path):
    file=tmp_path/'aliases.json'
    file.write_text('{"schema_version":"1.0","aliases_by_uuid":{"oops":["X"]}}')
    with pytest.raises(MapDBCatalogError,match='map_db_aliases_invalid_uuid'):
        read([(UUID(A),'名称',True,None,'','')],file)

def test_no_silent_truncation():
    rows=[(UUID(int=n+1),'A',True,None,'','') for n in range(3001)]
    with pytest.raises(MapDBCatalogError,match='map_db_catalog_exceeds_3000'):
        read(rows)

def test_error_does_not_leak_database_password():
    def fail(**kwargs):raise RuntimeError('secret_password')
    with pytest.raises(MapDBCatalogError,match='map_db_query_unavailable') as exc:
        read_map_directory(dsn='password=secret_password',connect=fail)
    assert 'secret_password' not in str(exc.value)

def test_duplicate_uuid_rejected():
    with pytest.raises(MapDBCatalogError,match='map_db_duplicate_uuid'):
        read([(UUID(A),'A',True,None,'',''),(UUID(A),'B',True,None,'','')])
