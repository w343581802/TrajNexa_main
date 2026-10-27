"""M1c read-only camera directory snapshot. OCR names are suggestions, not identity."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.camera_name_match import suggest_names

MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CAMERAS = 3000


def canonical_name(raw: str) -> str:
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', raw).casefold()).strip()


@dataclass(frozen=True)
class CameraCatalog:
    revision: str
    fingerprint: str
    cameras: list[dict]
    source: str = 'local_file'
    diagnostics: dict | None = None

    def by_uuid(self, camera_uuid: str) -> dict | None:
        return next((item for item in self.cameras if item['camera_uuid'] == camera_uuid), None)


class CatalogError(ValueError):
    pass


def catalog_path() -> Path:
    return settings.storage_root.resolve() / 'camera_catalog.json'


def load_catalog(path: Path | None = None) -> CameraCatalog:
    # Explicit test path always uses original local-file mode.
    # Map DB mode is opt-in; always SELECT-only, never copy/update Map data.
    if path is None and settings.map_db_readonly_url.strip():
        from app.map_db_catalog import read_map_directory, MapDBCatalogError
        try:
            cameras, revision, digest, diagnostics = read_map_directory(
                dsn=settings.map_db_readonly_url,
                alias_path=settings.storage_root.resolve() / 'map_camera_aliases.json')
        except MapDBCatalogError as exc:
            raise CatalogError(str(exc)) from None
        return CameraCatalog(revision, digest, cameras,
                             source='map_postgresql_readonly', diagnostics=diagnostics)
    path = path or catalog_path()
    try:
        if path.stat().st_size > MAX_CATALOG_BYTES:
            raise CatalogError('catalog_file_too_large')
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CatalogError('catalog_not_configured') from None
    except OSError:
        raise CatalogError('catalog_not_readable') from None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise CatalogError('catalog_invalid_json') from None
    if not isinstance(data, dict) or data.get('schema_version') != '1.0':
        raise CatalogError('catalog_invalid_schema')
    revision = data.get('spatial_revision')
    entries = data.get('cameras')
    if not isinstance(revision, str) or not 1 <= len(revision) <= 120 or not isinstance(entries, list) or len(entries) > MAX_CAMERAS:
        raise CatalogError('catalog_invalid_schema')
    cameras = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CatalogError('catalog_invalid_camera')
        uuid = entry.get('camera_uuid')
        name = entry.get('display_name')
        aliases = entry.get('aliases', [])
        if not isinstance(uuid, str) or not isinstance(name, str) or not isinstance(aliases, list):
            raise CatalogError('catalog_invalid_camera')
        try:
            uuid = str(UUID(uuid))
        except ValueError:
            raise CatalogError('catalog_invalid_uuid') from None
        if uuid in seen or not (1 <= len(name.strip()) <= 120) or len(aliases) > 30:
            raise CatalogError('catalog_invalid_camera')
        if any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 120 for x in aliases):
            raise CatalogError('catalog_invalid_alias')
        if entry.get('active', True) is not True and entry.get('active') is not False:
            raise CatalogError('catalog_invalid_camera')
        seen.add(uuid)
        cameras.append({'camera_uuid': uuid, 'display_name': name.strip(),
                        'aliases': [x.strip() for x in aliases],
                        'active': entry.get('active', True)})
    return CameraCatalog(revision, hashlib.sha256(raw).hexdigest(), cameras)


def catalog_status() -> dict:
    try:
        catalog = load_catalog()
    except CatalogError as exc:
        return {'status': 'unavailable', 'reason': str(exc),
                'spatial_revision': None, 'catalog_sha256': None, 'cameras': []}
    return {'status': 'available', 'reason': None,
            'spatial_revision': catalog.revision,
            'catalog_sha256': catalog.fingerprint, 'cameras': catalog.cameras,
            'source': catalog.source, 'diagnostics': catalog.diagnostics or {}}


def annotate_camera_report(report: dict, catalog: CameraCatalog | None = None) -> dict:
    camera = report['camera']
    camera['camera_uuid'] = None
    camera['catalog_revision'] = None
    camera['catalog_sha256'] = None
    camera['directory_candidates'] = []
    camera['name_suggestions'] = []
    if catalog is None:
        try:
            catalog = load_catalog()
        except CatalogError as exc:
            camera['directory_status'] = str(exc)
            return report
    camera['catalog_revision'] = catalog.revision
    camera['catalog_sha256'] = catalog.fingerprint
    # A quality-reviewed ROI must never fall back to full-width OCR guesses.
    if camera.get('name_roi_analysis') is not None:
        raw = camera.get('name_candidates_for_directory') or []
        if not raw:
            camera['directory_status'] = 'name_requires_review'
            return report
    else:
        raw = [camera.get('overlay_name_raw'), *(camera.get('camera_candidates') or [])]
    names = {canonical_name(x) for x in raw if isinstance(x, str) and canonical_name(x)}
    if not names:
        camera['directory_status'] = 'overlay_name_unavailable'
        return report
    # 精确匹配之外再给相似建议：OCR 常见错字（0/O、1/l）用同形字折叠后的编辑相似度兜底。
    # 建议只用于人工挑选，绝不写 camera_uuid、也不改 match_status。
    camera['name_suggestions'] = suggest_names(
        [x for x in raw if isinstance(x, str)], catalog.cameras)
    matches = []
    for item in catalog.cameras:
        if not item['active']:
            continue
        values = [item['display_name'], *item['aliases']]
        matched = [x for x in values if canonical_name(x) in names]
        if matched:
            matches.append({'camera_uuid': item['camera_uuid'],
                            'display_name': item['display_name'],
                            'matched_alias': matched[0],
                            'match_method': 'normalized_exact'})
    camera['directory_candidates'] = matches[:50]
    if camera.get('match_status') == 'conflicting_names':
        camera['directory_status'] = 'conflicting_overlay_names'
    elif not matches:
        camera['directory_status'] = 'no_exact_match'
    elif len(matches) == 1:
        camera['directory_status'] = 'single_candidate_unconfirmed'
    else:
        camera['directory_status'] = 'ambiguous_alias'
    return report
