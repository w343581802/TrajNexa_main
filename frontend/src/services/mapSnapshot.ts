/**
 * TrajNexa_Map 只读空间数据客户端（主系统直连 Map 只读库，M2b 接口）。
 *
 * 主系统只消费 `GET /api/v1/spatial/cameras`，不读 Map 的 ORM 表、不写回 GIS。
 * 空间真值仍来自主系统后端；只有参考截帧的图片字节经 `/map-api` 读 Map 的公开只读内容接口。
 * 坐标恒为 WGS84；`pose_quality` 用于判断能否画视域，未知姿态不得在地图上伪造朝向或扇形。
 * FOV 是 Map 的模型几何，不是实测覆盖。
 */

export type PoseQuality = "calibrated" | "unknown";

export type FovLevel = "identification" | "recognition" | "observation";

/** 一个视域等级下的若干多边形外环（ring 为 [lon, lat] 序列）。 */
export type FovLevelRings = { level: FovLevel; rings: [number, number][][] };

export type SnapshotCamera = {
  camera_id: number;
  camera_uuid: string | null;
  index_code: string;
  external_index_code: string;
  display_name: string;
  camera_type: string;
  zone: string;
  surveillance: string;
  surveillance_type: string;
  mount: string;
  lon: number;
  lat: number;
  bearing_deg: number | null;
  fov_deg: number | null;
  /** 台账没录 FOV 时后端按业务默认 80° 补齐，这里标明来源，UI 不能说成实测。 */
  fov_source: "map_camera" | "default_80" | null;
  range_m: number | null;
  height_m: number | null;
  /** 路线 A（相机参数射线投影）所需参数是否齐全；缺一项就只能退视域中心。 */
  ray_ready: boolean;
  pose_quality: PoseQuality;
  /** Map 侧是否有录像参考帧（source_type=video_frame）；没有时主系统保持灰色。 */
  has_video: boolean;
  /** 参考帧定位；仅用于显示原图，不参与身份或空间结论。 */
  snapshot: { camera_uuid: string; image_id: string } | null;
  fov_rings: [number, number][][] | null;
  fov_levels: FovLevelRings[] | null;
};

export type MapSnapshot = {
  schema_version: string;
  crs: string;
  spatial_revision: string;
  generated_at: string;
  counts: { cameras: number; with_pose: number; with_focus: number; with_fov_levels?: number; with_video?: number };
  cameras: SnapshotCamera[];
};

/** 主系统 M2b 接口的原始返回（只取本文件需要的字段）。 */
type SpatialCamera = {
  map_camera_id: string;
  camera_uuid: string | null;
  display_name: string | null;
  location: { lon: number; lat: number };
  bearing_deg: number | null;
  tilt_deg: number | null;
  horizontal_fov_deg: number | null;
  horizontal_fov_source?: "map_camera" | "default_80" | null;
  mount_height_m?: number | null;
  ray_projection_ready?: boolean;
  bearing_source?: string;
  range_m: number | null;
  camera_type: string;
  pose_quality: string;
  has_video?: boolean;
  snapshot?: { camera_uuid: string; image_id: string } | null;
  fov_levels: { level: string; geometry: { type: string; coordinates: unknown } }[];
};

type SpatialResponse = {
  status: string;
  spatial_revision: string;
  counts: Record<string, number>;
  cameras: SpatialCamera[];
};

const SPATIAL_URL = "/api/v1/spatial/cameras";

let cache: MapSnapshot | null = null;

function ringsOf(geometry: { type: string; coordinates: unknown }): [number, number][][] {
  const polygons = geometry.type === "Polygon"
    ? [geometry.coordinates as number[][][]]
    : (geometry.coordinates as number[][][][]);
  return polygons.map((polygon) => (polygon[0] ?? []).map(([lon, lat]) => [lon, lat] as [number, number]));
}

function toSnapshotCamera(camera: SpatialCamera): SnapshotCamera | null {
  const id = Number(camera.map_camera_id);
  if (!Number.isFinite(id)) return null;
  const calibrated = camera.pose_quality === "calibrated";
  const levels = (camera.fov_levels ?? [])
    .filter((level) => level.geometry && Array.isArray(level.geometry.coordinates))
    .map((level) => ({ level: level.level as FovLevel, rings: ringsOf(level.geometry) }));
  return {
    camera_id: id,
    camera_uuid: camera.camera_uuid,
    index_code: "",
    external_index_code: "",
    display_name: camera.display_name ?? "",
    camera_type: camera.camera_type ?? "",
    zone: "",
    surveillance: "",
    surveillance_type: "camera",
    mount: "",
    lon: camera.location.lon,
    lat: camera.location.lat,
    bearing_deg: camera.bearing_deg,
    fov_deg: camera.horizontal_fov_deg,
    fov_source: camera.horizontal_fov_source ?? null,
    range_m: camera.range_m,
    height_m: camera.mount_height_m ?? null,
    ray_ready: Boolean(camera.ray_projection_ready),
    // 只有台账标明 calibrated 才允许画视域；其余一律按未知姿态处理，不臆造扇形
    pose_quality: calibrated ? "calibrated" : "unknown",
    has_video: Boolean(camera.has_video),
    snapshot: camera.snapshot ?? null,
    fov_rings: null,
    fov_levels: calibrated && levels.length ? levels : null,
  };
}

export async function loadMapSnapshot(force = false): Promise<MapSnapshot> {
  if (cache && !force) return cache;
  const response = await fetch(SPATIAL_URL, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`map_spatial_http_${response.status}`);
  const payload = (await response.json()) as SpatialResponse;
  if (!Array.isArray(payload.cameras)) throw new Error("map_spatial_invalid_payload");
  const cameras = payload.cameras
    .map(toSnapshotCamera)
    .filter((camera): camera is SnapshotCamera => camera !== null);
  const withFov = cameras.filter((camera) => camera.fov_levels?.length).length;
  cache = {
    schema_version: "1.0",
    crs: "EPSG:4326",
    spatial_revision: payload.spatial_revision,
    generated_at: new Date().toISOString(),
    counts: {
      cameras: cameras.length,
      with_pose: cameras.filter((camera) => camera.pose_quality === "calibrated").length,
      with_focus: withFov,
      with_fov_levels: withFov,
      with_video: cameras.filter((camera) => camera.has_video).length,
    },
    cameras,
  };
  return cache;
}

/** 摄像头展示名：优先台账/角标名称，缺失时回退到编号，不臆造名称。 */
export function cameraCode(camera: SnapshotCamera): string {
  const name = (camera.display_name || camera.index_code || camera.external_index_code || "").trim();
  return name && name.length <= 24 ? name : `#${camera.camera_id}`;
}

export function cameraLabel(camera: SnapshotCamera): string {
  return `镜头 ${cameraCode(camera)}`;
}
