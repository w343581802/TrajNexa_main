"""Demo-only SELECT-only access to TrajNexa_Map camera registry.

Reads only public.cameras_registry. No spatial FOV or GIS model access.
Never writes, migrates, auto-confirms names, or binds a camera UUID.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from uuid import UUID
import json

MAX_CAMERAS=3000
MAX_ALIAS_BYTES=512*1024

class MapDBCatalogError(ValueError):
    """Sanitized error codes: never include the DSN/password."""

def _load_manual_aliases(path:Path|None)->dict[str,list[str]]:
    if path is None or not path.exists():
        return {}
    try:
        if path.stat().st_size>MAX_ALIAS_BYTES:
            raise MapDBCatalogError('map_db_aliases_too_large')
        document=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeError,ValueError) as exc:
        if isinstance(exc,MapDBCatalogError):raise
        raise MapDBCatalogError('map_db_aliases_invalid_json') from None
    if not isinstance(document,dict) or document.get('schema_version')!='1.0' or not isinstance(document.get('aliases_by_uuid'),dict):
        raise MapDBCatalogError('map_db_aliases_invalid_schema')
    result={}
    for key,values in document['aliases_by_uuid'].items():
        try: uid=str(UUID(key))
        except (ValueError,TypeError,AttributeError):
            raise MapDBCatalogError('map_db_aliases_invalid_uuid') from None
        if not isinstance(values,list) or len(values)>30:
            raise MapDBCatalogError('map_db_aliases_invalid_list')
        if any(not isinstance(v,str) or not 1<=len(v.strip())<=120 for v in values):
            raise MapDBCatalogError('map_db_aliases_invalid_name')
        if uid in result:
            raise MapDBCatalogError('map_db_aliases_duplicate_uuid')
        result[uid]=list(dict.fromkeys(v.strip() for v in values))
    return result

def read_map_directory(*,dsn:str,alias_path:Path|None=None,connect=None):
    """Return (entries, directory_revision, digest, diagnostics) from one RO tx."""
    if not isinstance(dsn,str) or not dsn:
        raise MapDBCatalogError('map_db_not_configured')
    aliases=_load_manual_aliases(alias_path)
    if connect is None:
        try: import psycopg
        except ImportError:
            raise MapDBCatalogError('map_db_driver_unavailable') from None
        connect=psycopg.connect
    sql=('SELECT id, name, enabled, map_camera_id, index_code, external_index_code, reviewed_at, reviewed_by '
         'FROM public.cameras_registry WHERE deleted_at IS NULL '
         'ORDER BY id LIMIT 3001')
    try:
        with connect(dsn,connect_timeout=4) as conn:
            with conn.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '4000ms'")
                cursor.execute(sql)
                rows=cursor.fetchall()
    except Exception:
        # psycopg errors may contain credentials: intentionally suppress text.
        raise MapDBCatalogError('map_db_query_unavailable') from None
    if len(rows)>MAX_CAMERAS:
        raise MapDBCatalogError('map_db_catalog_exceeds_3000')
    entries=[];seen=set();unnamed=0;reviewed=0
    for row in rows:
        if len(row)!=8:
            raise MapDBCatalogError('map_db_unexpected_row')
        uid,name,enabled,map_camera_id,index_code,external_index_code,reviewed_at,reviewed_by=row
        try:uid=str(UUID(str(uid)))
        except (TypeError,ValueError):
            raise MapDBCatalogError('map_db_unexpected_uuid') from None
        if uid in seen:
            raise MapDBCatalogError('map_db_duplicate_uuid')
        seen.add(uid)
        if not isinstance(name,str) or not name.strip():
            unnamed+=1
            continue
        if len(name.strip())>120:
            raise MapDBCatalogError('map_db_name_exceeds_catalog_limit')
        if enabled is not None and not isinstance(enabled,bool):
            raise MapDBCatalogError('map_db_unexpected_enabled')
        is_reviewed = reviewed_at is not None
        if is_reviewed:
            reviewed += 1
        entries.append({
            'camera_uuid':uid,'display_name':name.strip(),
            'aliases':aliases.get(uid,[]),'active':enabled is not False,
            'map_camera_id':map_camera_id,
            'index_code':index_code or '',
            'external_index_code':external_index_code or '',
            # 台账审核状态：绝大多数镜头从未人工审核过，名称只能算“台账写法”，不是核实结论
            'reviewed_at':reviewed_at.isoformat() if is_reviewed else None,
            'reviewed_by':reviewed_by if isinstance(reviewed_by,str) else None,
        })
    entries.sort(key=lambda i:i['camera_uuid'])
    normalized=json.dumps(entries,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    fingerprint=sha256(normalized.encode('utf-8')).hexdigest()
    revision='mapdb-directory:'+fingerprint[:16]
    diagnostics={'source':'map_postgresql_readonly','registry_count':len(rows),
                 'named_count':len(entries),'unnamed_count':unnamed,
                 'reviewed_count':reviewed,'unreviewed_count':len(entries)-reviewed,
                 'orphaned_alias_uuids':sorted(set(aliases)-seen)[:20],
                 'spatial_revision':None,'live_read':True}
    return entries,revision,fingerprint,diagnostics
