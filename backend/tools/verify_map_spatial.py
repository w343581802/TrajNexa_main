"""M2b/M2c 真实环境验收：主系统读到的镜头点位、UUID、FOV 是否与 Map 台账一致。

用法（在 api 容器里跑，复用已挂载的只读 DSN）::

    docker exec trajnexa_main-api python tools/verify_map_spatial.py

判定口径：
- 点位集合与台账逐条对齐；
- UUID 只能来自 cameras_registry.id，不得凭空生成，也不得与 map_camera_id 错配；
- FOV 逐顶点比对 ST_AsGeoJSON(geom, 6)，面积、外接框、镜头顶点位置一并核对；
- 任何一项不符即退出码 1，方便挂到 CI。

本脚本只读；它不修改 Map 数据，也不对真实覆盖能力下结论。
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.map_db_spatial import LEVELS, read_map_spatial  # noqa: E402

POINTS = """
SELECT m.id, ST_X(m.location), ST_Y(m.location), m.direction, m.angle,
       m.effective_range_m, m.camera_type
FROM public.cameras_camera AS m ORDER BY m.id
"""
REGISTRY = """
SELECT r.id::text, r.map_camera_id, r.name, r.pose_quality
FROM public.cameras_registry AS r
WHERE r.map_camera_id IS NOT NULL AND r.deleted_at IS NULL
"""
# tilt_deg 没有授予只读角色（主系统用 cameras_camera.angle）；只有拿到权限时才做冲突比对
REGISTRY_TILT = """
SELECT r.map_camera_id::text, r.tilt_deg
FROM public.cameras_registry AS r
WHERE r.map_camera_id IS NOT NULL AND r.deleted_at IS NULL AND r.tilt_deg IS NOT NULL
"""
FOCUS = """
SELECT f.camera_id_id, f.level, f.with_intersection,
       ST_AsGeoJSON(f.geom, 6), ST_Area(f.geom::geography),
       ST_NPoints(f.geom)
FROM public.cameras_camerafocus AS f
WHERE f.scenario = 'mean' AND f.geom IS NOT NULL
"""


def rings(geometry):
    polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    return [[[round(x, 6), round(y, 6)] for x, y in ring] for polygon in polygons for ring in polygon]


def bbox(geometry):
    xs = [p[0] for poly in rings(geometry) for p in poly]
    ys = [p[1] for poly in rings(geometry) for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def metres(a, b):
    radius = 6371008.8
    d_lat = math.radians(b[1] - a[1])
    d_lon = math.radians(b[0] - a[0])
    h = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(a[1])) * math.cos(math.radians(b[1])) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def main() -> int:
    dsn = os.environ.get('MAP_DB_READONLY_URL', '').strip()
    if not dsn:
        print('FAIL: MAP_DB_READONLY_URL 未配置，无法与 Map 台账比对')
        return 1
    import psycopg

    with psycopg.connect(dsn, connect_timeout=10, options='-c default_transaction_read_only=on') as conn:
        with conn.cursor() as cur:
            cur.execute(POINTS)
            points = {str(row[0]): row for row in cur.fetchall()}
            cur.execute(REGISTRY)
            registry = {str(row[1]): row for row in cur.fetchall()}
            cur.execute(FOCUS)
            focus = {(str(row[0]), row[1]): row for row in cur.fetchall()}
            try:
                cur.execute(REGISTRY_TILT)
                tilt_deg = {str(row[0]): float(row[1]) for row in cur.fetchall()}
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                tilt_deg = None

    payload = read_map_spatial(dsn=dsn)
    cameras = {camera['map_camera_id']: camera for camera in payload['cameras']}
    failures: list[str] = []

    def check(ok: bool, label: str, detail: str = '') -> None:
        print(('PASS ' if ok else 'FAIL ') + label + (f' — {detail}' if detail else ''))
        if not ok:
            failures.append(label)

    check(payload['status'] == 'available', '状态 available', payload['status'])
    check(set(cameras) == set(points), '点位集合与 cameras_camera 一致',
          f"{len(cameras)} vs {len(points)}")

    bad_pos = []
    bad_pose = []
    for map_id, camera in cameras.items():
        row = points[map_id]
        lon, lat = camera['location']['lon'], camera['location']['lat']
        if abs(lon - float(row[1])) > 1e-6 or abs(lat - float(row[2])) > 1e-6:
            bad_pos.append(map_id)
        for api_value, db_value, label in (
            (camera['bearing_deg'], row[3], 'direction'),
            (camera['tilt_deg'], row[4], 'angle'),
            (camera['range_m'], row[5], 'effective_range_m'),
        ):
            if db_value is None and api_value is None:
                continue
            if db_value is None or api_value is None or abs(float(api_value) - float(db_value)) > 0.001:
                bad_pose.append(f'{map_id}.{label}')
    check(not bad_pos, '坐标与台账逐条一致（≤1e-6°）', ','.join(bad_pos[:5]))
    check(not bad_pose, '朝向/俯角/量程与台账一致', ','.join(bad_pose[:5]))

    linked = {map_id: camera['camera_uuid'] for map_id, camera in cameras.items() if camera['camera_uuid']}
    mismatched = [map_id for map_id, uuid in linked.items()
                  if map_id not in registry or registry[map_id][0] != uuid]
    invented = [map_id for map_id in linked if map_id not in registry]
    check(not mismatched, 'UUID 与 cameras_registry.id 对应', ','.join(mismatched[:5]))
    check(not invented, '不存在凭空生成的 UUID', ','.join(invented[:5]))
    check(payload['counts']['registry_linked'] == len(linked), '关联数与 UUID 数一致',
          f"{payload['counts']['registry_linked']} vs {len(linked)}")
    absent = sorted(set(registry) - set(cameras))
    check(not absent, '台账有 map_camera_id 时 GIS 必有点位', ','.join(absent[:5]))

    api_levels = {(map_id, level['level']): level for map_id, camera in cameras.items()
                  for level in camera['fov_levels']}
    check(set(api_levels) == set(focus), 'FOV 集合与 cameras_camerafocus(mean) 一致',
          f"{len(api_levels)} vs {len(focus)}")

    geo_bad, area_bad, apex_bad = [], [], []
    for key, level in api_levels.items():
        row = focus.get(key)
        if row is None:
            geo_bad.append(str(key))
            continue
        stored = json.loads(row[3])
        if rings(stored) != rings(level['geometry']):
            geo_bad.append(f'{key[0]}/{key[1]}')
            continue
        if bbox(stored) != bbox(level['geometry']):
            geo_bad.append(f'{key[0]}/{key[1]}:bbox')
        drawn_area = _area_m2(level['geometry'])
        if abs(drawn_area - float(row[4])) > max(1.0, float(row[4]) * 0.01):
            area_bad.append(f'{key[0]}/{key[1]} {drawn_area:.1f}/{float(row[4]):.1f}')
        camera = cameras[key[0]]
        apex = min(metres((camera['location']['lon'], camera['location']['lat']), point)
                   for point in rings(level['geometry'])[0])
        if apex > 8:
            apex_bad.append(f'{key[0]}/{key[1]} {apex:.1f}m')
    check(not geo_bad, 'FOV 顶点与外接框与台账完全一致', ','.join(geo_bad[:5]))
    check(not area_bad, 'FOV 面积与 ST_Area 一致', ','.join(area_bad[:5]))
    check(not apex_bad, 'FOV 顶点落在镜头处（≤8 m）', ','.join(apex_bad[:5]))

    duplicates = [map_id for map_id, camera in cameras.items()
                  if len({json.dumps(rings(l['geometry'])) for l in camera['fov_levels']}) < len(camera['fov_levels'])]
    if duplicates:
        print(f'NOTE 台账数据缺陷：{",".join(duplicates)} 的多个 FOV 等级几何完全相同')

    if tilt_deg is None:
        print('SKIP 台账 tilt_deg 未授予只读角色，跳过 angle/tilt 冲突比对')
    else:
        tilt_conflicts = [map_id for map_id, camera in cameras.items()
                          if map_id in tilt_deg and camera['tilt_deg'] is not None
                          and abs(tilt_deg[map_id] - camera['tilt_deg']) > 1]
        if tilt_conflicts:
            print('NOTE 台账数据缺陷：%s 的 cameras_camera.angle 与 cameras_registry.tilt_deg 不一致'
                  % ','.join(tilt_conflicts))

    unknown_fov = [map_id for map_id, camera in cameras.items()
                   if camera['fov_levels'] and camera['pose_quality'] != 'calibrated']
    if unknown_fov:
        print('NOTE %s 有模型 FOV 但位姿未标定，界面按 modelled_unverified 展示'
              % ','.join(unknown_fov))

    print(f"\n汇总：{len(cameras)} 个点位 / {len(linked)} 个台账关联 / {len(api_levels)} 个 FOV 面 / "
          f"{len(failures)} 项失败")
    return 1 if failures else 0


def _area_m2(geometry) -> float:
    """等距圆柱近似面积，仅用于和 ST_Area(geography) 做量级比对。"""
    total = 0.0
    for polygon in ([geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']):
        ring = polygon[0]
        lat0 = math.radians(sum(point[1] for point in ring) / len(ring))
        kx, ky = 111320.0 * math.cos(lat0), 110540.0
        area = 0.0
        for i in range(len(ring) - 1):
            x1, y1 = ring[i][0] * kx, ring[i][1] * ky
            x2, y2 = ring[i + 1][0] * kx, ring[i + 1][1] * ky
            area += x1 * y2 - x2 * y1
        total += abs(area / 2)
    return total


if __name__ == '__main__':
    raise SystemExit(main())
