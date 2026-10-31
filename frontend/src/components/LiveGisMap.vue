<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { FeatureCollection, GeoJsonObject } from "geojson";
import { mapTiles } from "../services/basemapTiles";

/** M2c: separate live GIS map. Never reuse demo coordinates as real GIS data. */
type Camera = {
  map_camera_id: string;
  camera_uuid: string | null;
  display_name: string | null;
  location: { lon: number; lat: number };
  bearing_deg: number | null; tilt_deg: number | null;
  pose_quality: string; fov_levels: {
    level: string; geometry: GeoJsonObject; occlusion_applied: boolean;
  }[];
};
type View = {
  status: "available"; spatial_revision: string; truncated: false;
  roads: FeatureCollection; buildings: FeatureCollection;
  cameras: Camera[];
  counts: Record<string, number>;
};

const host = ref<HTMLElement | null>(null);
const loading = ref(false);
const error = ref("");
const status = ref("尚未请求地图数据");
const counts = ref<Record<string, number>>({});
/** 全库镜头清单只用于计数与搜索定位，不参与当前视野的绘制 */
const allCameras = ref<Camera[]>([]);
const libraryTotal = ref(0);
const libraryFov = ref(0);
const query = ref("");
const matches = computed(() => {
  const text = query.value.trim().toLowerCase();
  if (!text) return [];
  return allCameras.value
    .filter((cam) => `${cam.display_name ?? ""} ${cam.map_camera_id} ${cam.camera_uuid ?? ""}`.toLowerCase().includes(text))
    .slice(0, 6);
});
const useExternalTiles = ref(false);
const locating = ref(false);
let userMoved = false;
let map: L.Map | null = null;
let roadLayer: L.GeoJSON | null = null;
let buildingLayer: L.GeoJSON | null = null;
let cameraLayer: L.LayerGroup | null = null;
let fovLayer: L.LayerGroup | null = null;
let externalTiles: L.TileLayer | null = null;
let active: AbortController | null = null;
let version = 0;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let zoomInRetries = 0;

function safePopup(camera: Camera): HTMLElement {
  // Never interpolate untrusted OCR, registry name or notes into HTML.
  const el = document.createElement("div");
  const lines = [
    ["设备名称", camera.display_name || "未登记"],
    ["地图 ID", camera.map_camera_id],
    ["台账 UUID", camera.camera_uuid || "未关联"],
    ["坐标", String(camera.location.lat) + ", " + String(camera.location.lon)],
    ["朝向", camera.bearing_deg == null ? "未知" : String(camera.bearing_deg) + "°"],
    ["俯角", camera.tilt_deg == null ? "未知" : String(camera.tilt_deg) + "°"],
    ["位姿状态", camera.pose_quality],
    ["模型视域", camera.fov_levels.length ? "存在（未经实测验证）" : "未提供"],
  ];
  for (const [key,value] of lines) {
    const p = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = key + "：";
    p.append(strong, document.createTextNode(value));
    el.appendChild(p);
  }
  return el;
}

function draw(result: View) {
  if (!map || !roadLayer || !buildingLayer || !cameraLayer || !fovLayer) return;
  roadLayer.clearLayers();
  buildingLayer.clearLayers();
  cameraLayer.clearLayers();
  fovLayer.clearLayers();
  buildingLayer.addData(result.buildings);
  roadLayer.addData(result.roads);
  for (const camera of result.cameras) {
    const point: L.LatLngExpression = [camera.location.lat,camera.location.lon];
    const marker = L.circleMarker(point,{
      radius:6,weight:2,color:"#07111b",fillOpacity:.95,
      fillColor:camera.camera_uuid ? "#5caaff" : "#e4b15c",
    }).bindPopup(safePopup(camera));
    cameraLayer.addLayer(marker);
    // The Map ORM geometry is modelled FOV, never an empirically measured range.
    for (const fov of camera.fov_levels) {
      const tone = fov.level === "identification" ? "#529de3"
        : fov.level === "recognition" ? "#25b99a" : "#dfb65b";
      fovLayer.addLayer(L.geoJSON(fov.geometry,{
        style: { color:tone,weight:1.2,fillColor:tone,fillOpacity:.12,
                 dashArray:fov.occlusion_applied ? undefined : "5 5" },
      }));
    }
  }
  counts.value = result.counts;
  status.value = "Map 只读 · " + result.spatial_revision + " · 数据按视野加载";
  zoomInRetries = 0;
  error.value = "";
}

async function refresh() {
  if (!map) return;
  active?.abort();
  const request = new AbortController();
  active = request;
  const current = ++version;
  const b = map.getBounds();
  const bbox = [
    b.getWest(),b.getSouth(),b.getEast(),b.getNorth(),
  ].map((n) => n.toFixed(6)).join(",");
  loading.value = true;
  error.value = "";
  try {
    const response = await fetch("/api/v1/gis/viewport?bbox=" + encodeURIComponent(bbox),{
      signal:request.signal,cache:"no-store",
    });
    const payload = await response.json();
    if (!response.ok || payload.status !== "available") {
      throw new Error(payload?.error?.code || "gis_unavailable");
    }
    if (current !== version || request.signal.aborted) return;
    if (payload.truncated || !payload.roads?.features || !payload.buildings?.features
        || !Array.isArray(payload.cameras)) throw new Error("gis_invalid_response");
    draw(payload as View);
  } catch (exception) {
    if (request.signal.aborted || current !== version) return;
    const code = exception instanceof Error ? exception.message : "";
    // 视野内要素过多时自动放大一级重取，避免把“超出上限”直接甩给用户
    if (code.includes("zoom_in") && map && map.getZoom() < 19 && zoomInRetries < 2) {
      zoomInRetries += 1;
      loading.value = false;
      status.value = "当前视野要素过多，已自动放大一级";
      map.setZoom(map.getZoom() + 1);
      return;
    }
    error.value = code || "gis_request_failed";
    // Do not leave stale features under a new viewport as if they were current.
    roadLayer?.clearLayers();
    buildingLayer?.clearLayers();
    cameraLayer?.clearLayers();
    fovLayer?.clearLayers();
    counts.value = {};
    status.value = "地图当前视野读取失败（未使用模拟数据）";
  } finally {
    if (current === version) loading.value = false;
  }
}
function scheduleRefresh() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => void refresh(),230);
}
async function locateFirstMapCamera() {
  if (!map) return;
  locating.value = true;
  try {
    const response = await fetch("/api/v1/gis/anchor",{cache:"no-store"});
    const obj = await response.json();
    if (!response.ok) throw new Error(obj?.error?.code || "gis_anchor_unavailable");
    const center = obj?.center;
    if (Array.isArray(center) && center.length === 2 &&
        Number.isFinite(center[0]) && Number.isFinite(center[1]) && map && !userMoved) {
      map.setView([center[0],center[1]],16);
    }
  } catch {
    // Initial viewport remains an explicit browsing coordinate, never fake data.
    if (!userMoved) status.value="地图镜头定位不可用，可手动移动视野";
  } finally {
    locating.value=false;
    if (map) void refresh();
  }
}

async function loadLibrary() {
  try {
    const response = await fetch("/api/v1/spatial/cameras", { cache: "no-store" });
    if (!response.ok) return;
    const body = await response.json();
    if (body?.status !== "available" || !Array.isArray(body.cameras)) return;
    allCameras.value = body.cameras as Camera[];
    libraryTotal.value = Number(body.counts?.displayable_points ?? body.cameras.length);
    libraryFov.value = Number(body.counts?.with_fov ?? 0);
  } catch {
    // 计数只是导航辅助；拿不到就不显示，绝不用估算数字填充
  }
}
function pickCamera(camera: Camera) {
  if (!map) return;
  query.value = "";
  userMoved = true;
  map.setView([camera.location.lat, camera.location.lon], 18);
}

function setExternalTiles() {
  if (!map) return;
  if (externalTiles) {
    map.removeLayer(externalTiles);
    externalTiles = null;
  }
  if (!useExternalTiles.value) return;
  // OSM 原色瓦片，靠 .is-dark-base（styles/map.css）压暗，和业务示意图保持一致
  externalTiles = mapTiles();
  externalTiles.addTo(map);
  externalTiles.bringToBack();
}
onMounted(() => {
  if (!host.value) return;
  const last = sessionStorage.getItem("trajnexa_m2c_view");
  let center: L.LatLngExpression = [25.2562,110.2118];
  let zoom = 15;
  if (last) {
    try {
      const values = JSON.parse(last) as number[];
      if (Array.isArray(values) && values.length === 3 &&
          Number.isFinite(values[0]) && Number.isFinite(values[1]) &&
          Number.isFinite(values[2]) && Math.abs(values[0])<=90 && Math.abs(values[1])<=180) {
        center = [values[0],values[1]];
        zoom = Math.max(13,Math.min(19,values[2]));
      }
    } catch { /* ignore corrupt browser preference */ }
  }
  map = L.map(host.value,{ zoomControl:true,preferCanvas:true,
    minZoom:12,maxZoom:19 }).setView(center,zoom);
  // 署名统一放在图例里，避免与右下角视图切换控件重叠
  map.attributionControl.setPrefix(false);
  void loadLibrary();
  buildingLayer = L.geoJSON(undefined,{
    style:(feature) => ({
      color:feature?.properties?.reviewed_geometry ? "#a182ff" : "#46617b",
      weight:1,fillColor:"#304c67",fillOpacity:.18,
    }),
  });
  roadLayer = L.geoJSON(undefined,{
    style:(feature) => ({
      color:feature?.properties?.reviewed_geometry ? "#b9a2fd" : "#72879c",
      weight:feature?.properties?.reviewed_geometry ? 3.2 : 2.2,
      opacity:.85,
    }),
  });
  cameraLayer = L.layerGroup();
  fovLayer = L.layerGroup();
  buildingLayer.addTo(map);
  roadLayer.addTo(map);
  fovLayer.addTo(map);
  cameraLayer.addTo(map);
  L.control.layers({},{
    "OSM + 人工建筑":buildingLayer,
    "OSM + 人工路网":roadLayer,
    "Map 模型 FOV":fovLayer,
    "摄像头点位":cameraLayer,
  },{collapsed:false,position:"topright"}).addTo(map);
  map.on("dragstart",() => { userMoved = true; });
  map.on("moveend zoomend",() => {
    if (!map) return;
    const c=map.getCenter();
    sessionStorage.setItem("trajnexa_m2c_view",JSON.stringify([c.lat,c.lng,map.getZoom()]));
    scheduleRefresh();
  });
  if (!last) void locateFirstMapCamera();
  else void refresh();
});
onUnmounted(() => {
  active?.abort();
  if (debounceTimer) clearTimeout(debounceTimer);
  map?.remove();
  map = null;
});
</script>

<template>
  <section class="gis-live" :class="{ 'is-dark-base': useExternalTiles }">
    <div class="gis-status">
      <b>TrajNexa · 真实 GIS</b>
      <span>{{ loading ? "加载视野中…" : status }}</span>
      <span v-if="counts.displayable_points !== undefined">
        视野内 {{ counts.displayable_points }} · 道路 {{ counts.roads }} · 建筑 {{ counts.buildings }}
      </span>
      <div class="gis-search">
        <input
          v-model="query"
          type="search"
          placeholder="搜索镜头：名称 / Map ID / UUID"
          aria-label="搜索 Map 镜头并定位"
        />
        <ul v-if="matches.length">
          <li v-for="cam in matches" :key="cam.map_camera_id">
            <button type="button" @click="pickCamera(cam)">
              <span>{{ cam.display_name || "未登记名称" }}</span>
              <span class="dim">#{{ cam.map_camera_id }}</span>
              <span class="dim">{{ cam.fov_levels.length ? "有模型 FOV" : "仅点位" }}</span>
            </button>
          </li>
        </ul>
      </div>
      <label title="启用后浏览器将直接请求外部暗色瓦片服务器">
        <input v-model="useExternalTiles" type="checkbox" @change="setExternalTiles" />
        在线暗色瓦片（可选）
      </label>
      <button type="button" :disabled="locating" @click="userMoved = false; locateFirstMapCamera()">定位 Map 镜头</button>
      <button type="button" :disabled="loading" @click="refresh">刷新</button>
      <span v-if="error" class="gis-error">读取失败：{{ error }}。请缩小视野或检查 Map 只读数据库权限。</span>
    </div>
    <div ref="host" class="leaflet-canvas" aria-label="TrajNexa Map 的只读摄像头、道路、建筑与模型视域" />
    <div class="gis-legend">
      <span>● 蓝：关联台账 UUID</span><span>● 橙：仅地图点位</span>
      <span>紫：人工编辑要素</span><span>FOV 是模型推算，非实测覆盖</span>
      <span v-if="libraryTotal">库内 {{ libraryTotal }} 个镜头 · {{ libraryFov }} 个有模型 FOV</span>
      <span>底图 © OpenStreetMap 贡献者 · ODbL</span>
    </div>
  </section>
</template>

<style scoped>
.gis-live{position:relative;min-width:0;min-height:0;display:flex;flex-direction:column;background:#0c1723;color:#dbe9f6;border:1px solid #26394d}
.leaflet-canvas{flex:1;width:100%;min-height:0;background:#0b1724}
.gis-status{min-height:41px;display:flex;gap:11px;padding:8px 12px;align-items:center;flex-wrap:wrap;font-size:10px;z-index:800;background:#102136}
.gis-status b{font-size:12px}.gis-status label{display:flex;align-items:center;gap:4px}
.gis-status button{color:#dbe9f6;border:1px solid #44617a;border-radius:4px;background:#20374f;padding:4px 8px}
.gis-search{position:relative;margin-left:auto}
.gis-search input{width:220px;color:#dbe9f6;background:#0d1d2c;border:1px solid #44617a;border-radius:4px;padding:4px 7px;font-size:10px}
.gis-search ul{position:absolute;right:0;top:calc(100% + 4px);width:300px;margin:0;padding:4px;list-style:none;background:#13283c;border:1px solid #3b5a76;border-radius:6px;box-shadow:0 10px 24px rgba(3,9,16,.6);z-index:1200}
.gis-search li+li{margin-top:2px}
.gis-search li button{display:flex;gap:8px;align-items:baseline;width:100%;text-align:left;background:transparent;border:0;padding:5px 6px;border-radius:4px;cursor:pointer}
.gis-search li button:hover{background:#1b3a55}
.gis-search .dim{color:#8fa9bf;font-size:10px;margin-left:auto}
.gis-error{color:#fac57e;font-weight:600}
.gis-legend{display:flex;gap:15px;flex-wrap:wrap;padding:7px 12px;z-index:400;font-size:10px;background:#102136;color:#b5c7d7}
/* Leaflet 自带的控件是浅色的，放在暗色底图上会亮成几个白块 */
.leaflet-canvas :deep(.leaflet-control-zoom a){background:#102136;color:#dbe9f6;border-color:#35506a}
.leaflet-canvas :deep(.leaflet-control-zoom a:hover){background:#1b3a55}
.leaflet-canvas :deep(.leaflet-control-layers){background:rgba(11,23,36,.94);color:#dbe9f6;border:1px solid #35506a;box-shadow:0 6px 18px rgba(3,9,16,.55)}
.leaflet-canvas :deep(.leaflet-control-layers-separator){border-top-color:#2b4257}
.leaflet-canvas :deep(.leaflet-control-attribution){background:rgba(11,23,36,.85);color:#9fb4c8}
.leaflet-canvas :deep(.leaflet-control-attribution a){color:#8fb6d8}
@media(max-width:900px){.gis-status{gap:6px}.gis-legend{gap:8px}}
</style>
