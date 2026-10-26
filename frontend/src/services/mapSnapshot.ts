/**
 * TrajNexa_Map 只读空间快照客户端。
 *
 * 主系统只消费该快照，不读 Map 的 ORM 表、不写回 GIS。坐标恒为 WGS84；
 * `fov_rings` 仅在 Map 完成姿态标定时存在，`pose_quality` 用于判断能否画视域，
 * 未知姿态不得在地图上伪造朝向或扇形。
 */

export type PoseQuality = "calibrated" | "unknown";

export type FovLevel = "identification" | "recognition" | "observation";

/** 一个视域等级下的若干多边形外环（ring 为 [lon, lat] 序列）。 */
export type FovLevelRings = { level: FovLevel; rings: [number, number][][] };

export type SnapshotCamera = {
  camera_id: number;
  index_code: string;
  external_index_code: string;
  camera_type: string;
  zone: string;
  surveillance: string;
  surveillance_type: string;
  mount: string;
  lon: number;
  lat: number;
  bearing_deg: number | null;
  fov_deg: number | null;
  range_m: number | null;
  height_m: number | null;
  pose_quality: PoseQuality;
  fov_rings: [number, number][][] | null;
  fov_levels: FovLevelRings[] | null;
};

export type MapSnapshot = {
  schema_version: string;
  crs: string;
  spatial_revision: string;
  generated_at: string;
  counts: { cameras: number; with_pose: number; with_focus: number; with_fov_levels?: number };
  cameras: SnapshotCamera[];
};

const SNAPSHOT_URL = "/map-api/api/v1/spatial/snapshot/";

let cache: MapSnapshot | null = null;

export function cachedSnapshot(): MapSnapshot | null {
  return cache;
}

export async function loadMapSnapshot(force = false): Promise<MapSnapshot> {
  if (cache && !force) return cache;
  const response = await fetch(SNAPSHOT_URL, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`map_snapshot_http_${response.status}`);
  const payload = (await response.json()) as MapSnapshot;
  if (!Array.isArray(payload.cameras)) throw new Error("map_snapshot_invalid_payload");
  cache = payload;
  return payload;
}

/** 摄像头展示名：Map 侧暂无台账名称时回退到编号，不臆造名称。 */
export function cameraCode(camera: SnapshotCamera): string {
  const code = (camera.index_code || camera.external_index_code || "").trim();
  // 部分记录把内部哈希/UUID 塞在 index_code 里，直接展示会撑爆卡片标题。
  return code && code.length <= 14 ? code : `#${camera.camera_id}`;
}

export function cameraLabel(camera: SnapshotCamera): string {
  return `镜头 ${cameraCode(camera)}`;
}
