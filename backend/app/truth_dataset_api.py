"""真值点数据集：把「GIS 视图上点出来的地物坐标 + 帧秒数」写成可被指标脚本直接消费的 JSON。

为什么需要这条接口（与 `m6_projection_metrics` 的边界一致）：

- 真值必须**独立于**被检验的投影链路。摄像头台账坐标/姿态是投影的输入，拿它当真值等于
  用 A 验证 A（本项目真实反例：`bearing_deg` 曾与 `direction` 差 187°，用摄像头坐标做真值
  完全发现不了）。因此本接口只接受卫星影像控制点 `map_control_point`、人工控制点
  `manual_control_point`，以及现场测量类 `surveyed` / `gnss_walk`；算法输出直接拒绝。
- 时刻不是猜的：录像起点由 OCR（文件名 / 画面字幕）给出，目标压在地物上的**片段内秒数**由
  点选者输入，`at = 录像起点 + 秒数`。两者分开存，便于以后换成更准的时钟基准。
- 写入前先用 `m6_projection_metrics.load_truth` 自检一遍：写进去的文件必须是 `--truth` 能吃的
  格式，不允许「存下来但跑不了」。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from app import m6_projection_metrics as metrics
from app.config import settings
from app.recording_api import fail

router = APIRouter(prefix='/api/v1')

_DATASET_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{2,60}$')
_CLOCK = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$')
_MAX_POINTS = 200


class TruthPointIn(BaseModel):
    id: str = Field(default='')
    lon: float
    lat: float
    at: str = ''
    frame_seconds: float | None = None
    source: str = 'map_control_point'
    range_class: str | None = None
    evidence: str = ''


class TruthDatasetIn(BaseModel):
    dataset_id: str = ''
    camera_uuid: str | None = None
    camera_label: str | None = None
    time_basis: str = ''
    base_time: str | None = None
    notes: str = ''
    collected_via: str = 'gis_picker'
    points: list[TruthPointIn] = Field(default_factory=list)


def _root() -> Path:
    return Path(settings.truth_datasets_dir)


def _reject(code: str, message: str, status: int = 422):
    """统一的错误出口：`{"detail": {"code", "message", "details"}}`（与 recordings 接口同形）。"""
    fail(status, code, message)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _clock(value: str) -> str:
    """只接受 `YYYY-MM-DDTHH:MM:SS`；不做时区推断，也不替人补日期。"""
    text = (value or '').strip()
    if not _CLOCK.match(text):
        _reject('truth_point_time_invalid', '时刻必须是 YYYY-MM-DDTHH:MM:SS（不带时区偏移）')
    try:
        datetime.strptime(text, '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        _reject('truth_point_time_invalid', '时刻不是合法的日期时间')
    return text


def _point(payload: TruthPointIn, index: int) -> dict:
    source = (payload.source or '').strip()
    if source in metrics.REFUSED_SOURCES:
        _reject('truth_source_is_algorithm_output',
                '算法输出（投影结果）不能当真值；请用卫星影像控制点或现场测量')
    if source not in metrics.TRUSTED_SOURCES:
        _reject('truth_source_not_allowed',
                '来源只能是 surveyed / gnss_walk / map_control_point / manual_control_point')
    if not (-180.0 <= payload.lon <= 180.0 and -90.0 <= payload.lat <= 90.0):
        _reject('truth_point_coordinates_out_of_range', '经纬度超出合法范围')
    point_id = (payload.id or '').strip() or f'T{index + 1}'
    row = {
        'id': point_id,
        'lon': round(float(payload.lon), 7),
        'lat': round(float(payload.lat), 7),
        'at': _clock(payload.at) if payload.at else None,
        'source': source,
        'range_class': (payload.range_class or '').strip() or None,
        'evidence': (payload.evidence or '').strip(),
    }
    if payload.frame_seconds is not None:
        row['frame_seconds'] = round(float(payload.frame_seconds), 3)
    return row


def _dataset_id(value: str) -> str:
    text = (value or '').strip().lower()
    if not text:
        return 'camera-truth-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    if not _DATASET_ID.match(text):
        _reject('truth_dataset_id_invalid', '数据集 id 只能用 a-z/0-9/-/_，长度 3–61')
    return text


@router.post('/spatial/camera-truth/datasets')
def save_dataset(payload: TruthDatasetIn = Body(...),
                 overwrite: bool = Query(default=False)) -> dict:
    if not payload.points:
        _reject('truth_points_missing', '至少要有 1 个真值点')
    if len(payload.points) > _MAX_POINTS:
        _reject('truth_points_too_many', f'单个数据集最多 {_MAX_POINTS} 个点')
    points = [_point(row, index) for index, row in enumerate(payload.points)]
    identifiers = [row['id'] for row in points]
    if len(set(identifiers)) != len(identifiers):
        _reject('truth_point_duplicate_id', '真值点 id 不能重复')

    dataset_id = _dataset_id(payload.dataset_id)
    document = {
        'dataset_id': dataset_id,
        'camera_uuid': payload.camera_uuid or None,
        'camera_label': payload.camera_label or None,
        'collected_at': _now(),
        'collected_via': payload.collected_via or 'gis_picker',
        'time_basis': (payload.time_basis or '').strip() or None,
        'base_time': (payload.base_time or '').strip() or None,
        'notes': (payload.notes or '').strip(),
        'points': points,
    }
    try:
        metrics.load_truth(document)
    except metrics.TruthSetError as exc:
        _reject('truth_dataset_rejected_by_metrics', f'指标模块不接受这份真值集：{exc}')

    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / f'{dataset_id}.json'
    if target.exists() and not overwrite:
        _reject('truth_dataset_exists', '同 id 的真值集已存在；确认覆盖请带 overwrite=true', 409)
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n',
                      encoding='utf-8')

    warnings = []
    if len(points) < 3:
        warnings.append('fewer_than_three_points')
    if any(not row['at'] for row in points):
        warnings.append('time_missing_on_some_points')
    if any(not row['evidence'] for row in points):
        warnings.append('evidence_missing_on_some_points')
    if not document['camera_uuid']:
        warnings.append('camera_uuid_missing')
    try:
        shown = str(target.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        shown = str(target)
    return {
        'status': 'saved',
        'dataset_id': dataset_id,
        'path': shown,
        'points': len(points),
        'sources': sorted({row['source'] for row in points}),
        'warnings': warnings,
        'note': ('已写入真值目录；可通过 `python scripts/b8_projection_metrics.py '
                 '--truth <path>` 直接消费。地址来自卫星影像/人工控制点，时刻来自录像起点 + 帧秒数。'),
    }


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


@router.get('/spatial/camera-truth/datasets')
def list_datasets() -> dict:
    root = _root()
    items = []
    if root.is_dir():
        for path in sorted(root.glob('*.json')):
            body = _read(path)
            points = body.get('points') or []
            items.append({
                'dataset_id': body.get('dataset_id') or path.stem,
                'file': path.name,
                'camera_uuid': body.get('camera_uuid'),
                'camera_label': body.get('camera_label'),
                'collected_at': body.get('collected_at'),
                'saved_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                .replace(microsecond=0).isoformat(),
                'points': len(points) if isinstance(points, list) else 0,
                'template': '.example' in path.name,
            })
    return {
        'status': 'available',
        'root': str(root),
        'datasets': items,
        'counts': {'datasets': len(items),
                   'usable': len([row for row in items if not row['template']])},
        'allowed_sources': list(metrics.TRUSTED_SOURCES),
        'refused_sources': list(metrics.REFUSED_SOURCES),
        'note': '真值来自卫星影像控制点/现场测量；算法输出（投影结果）永远不能当真值。',
    }


@router.get('/spatial/camera-truth/datasets/{dataset_id}')
def get_dataset(dataset_id: str) -> dict:
    if not _DATASET_ID.match((dataset_id or '').strip().lower()):
        _reject('truth_dataset_id_invalid', '数据集 id 只能用 a-z/0-9/-/_，长度 3–61')
    path = _root() / f'{dataset_id.strip().lower()}.json'
    if not path.is_file():
        _reject('truth_dataset_not_found', '真值集不存在', 404)
    body = _read(path)
    if not body:
        _reject('truth_dataset_unreadable', '真值集文件无法解析')
    return {'status': 'available', 'file': path.name, **body}


def resolve_path(dataset_id: str) -> Path:
    """指标脚本用：把 dataset_id 解析成磁盘路径（不做存在性判断）。"""
    return _root() / f'{dataset_id.strip().lower()}.json'