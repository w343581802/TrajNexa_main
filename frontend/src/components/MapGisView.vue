<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

type Geo = { type: string; coordinates: unknown };
type Level = { level: string; geometry: Geo; occlusion_applied: boolean };
type Camera = {
  map_camera_id: string; camera_uuid: string | null; display_name: string | null;
  location: { lon: number; lat: number };
  bearing_deg: number | null; tilt_deg: number | null; horizontal_fov_deg: number | null;
  pose_quality: string; map_registry_link: string; fov_levels: Level[];
};
type CameraResult = { status: string; spatial_revision: string; counts: Record<string, number>; cameras: Camera[] };
type Road = { osm_id: string; class: string | null; name: string | null; geometry: Geo };
type Building = { id: string; geometry: Geo };
type Basemap = {
  status: string; basemap_revision: string; counts: Record<string, number>;
  attribution: string; roads: Road[]; buildings: Building[];
};

const host = ref<HTMLDivElement | null>(null);
const cameras = ref<CameraResult | null>(null);
const basemap = ref<Basemap | null>(null);
const cameraError = ref("");
const basemapError = ref("");
const loading = ref(false);
const notice = ref("");
const zoom = ref(16);
const showRoads = ref(true);
const showBuildings = ref(true);
const showCameras = ref(true);
const showFov = ref(true);
const showLabels = ref(false);

let map: L.Map | null = null;
let canvas: L.Canvas | null = null;
let canvasFov: L.Canvas | null = null;
type GeoJsonOpts = L.GeoJSONOptions & { renderer?: L.Renderer | null };
let roadLayer: L.GeoJSON | null = null;
let buildingLayer: L.GeoJSON | null = null;
let cameraLayer: L.LayerGroup | null = null;
let fovLayer: L.LayerGroup | null = null;
let labelLayer: L.LayerGroup | null = null;
let controller: AbortController | null = null;
let timer: number | undefined;
let lastKey = "";

const ROAD_STYLE: Record<string, { color: string; weight: number; dash?: string }> = {
  motorway: { color: "#f0c069", weight: 4 },
  trunk: { color: "#e8b45e", weight: 3.6 },
  primary: { color: "#dfa955", weight: 3.2 },
  secondary: { color: "#c99a5c", weight: 2.7 },
  tertiary: { color: "#a98c64", weight: 2.2 },
  residential: { color: "#7d8794", weight: 1.6 },
  service: { color: "#5d6a77", weight: 1.1 },
  footway: { color: "#4f6472", weight: 0.9, dash: "3 4" },
  cycleway: { color: "#4f6472", weight: 0.9, dash: "5 4" },
  pedestrian: { color: "#5c6d79", weight: 1.0, dash: "3 4" },
};

function roadStyle(road: Road): L.PathOptions {
  const style = (road.class && ROAD_STYLE[road.class]) || { color: "#5b6773", weight: 1 };
  return { color: style.color, weight: style.weight, dashArray: style.dash, opacity: 0.92, interactive: false };
}

function cameraPopup(cam: Camera): string {
  return [
    `<strong>${cam.display_name || "未登记名称"}</strong>`,
    `地图 ID：${cam.map_camera_id}`,
    `稳定 UUID：${cam.camera_uuid || "未关联"}`,
    `朝向 / 俯角（Map 数值）：${cam.bearing_deg ?? "未知"} / ${cam.tilt_deg ?? "未知"}`,
    `台账位姿质量：${cam.pose_quality}`,
    `视域等级：${cam.fov_levels.map((f) => f.level).join(" / ") || "无"}`,
  ].join("<br />");
}

function headingLine(cam: Camera): L.LatLngTuple[] | null {
  if (cam.bearing_deg === null) return null;
  const rad = (cam.bearing_deg * Math.PI) / 180;
  const length = 0.0004;
  return [
    [cam.location.lat, cam.location.lon],
    [cam.location.lat + Math.cos(rad) * length, cam.location.lon + Math.sin(rad) * length],
  ];
}

function drawCameras() {
  if (!map) return;
  cameraLayer?.clearLayers();
  fovLayer?.clearLayers();
  labelLayer?.clearLayers();
  for (const cam of cameras.value?.cameras ?? []) {
    if (showFov.value) {
      for (const level of cam.fov_levels) {
        const options: GeoJsonOpts = {
          renderer: canvasFov,
          pane: "fovPane",
          style: () => ({
            color: level.level === "identification" ? "#ff3131"
              : level.level === "recognition" ? "#ffbf00" : "#00b300",
            weight: 1.2,
            fillOpacity: 0.22,
            dashArray: level.occlusion_applied ? undefined : "4 5",
            interactive: false,
          }),
        };
        L.geoJSON(level.geometry as unknown as GeoJSON.GeoJsonObject, options).addTo(fovLayer!);
      }
    }
    if (!showCameras.value) continue;
    const marker = L.circleMarker([cam.location.lat, cam.location.lon], {
      radius: cam.camera_uuid ? 6 : 5,
      color: "#081323", weight: 2,
      fillColor: cam.camera_uuid ? "#55b7ff" : "#e7ae5e",
      fillOpacity: 1,
    }).addTo(cameraLayer!);
    marker.bindPopup(cameraPopup(cam));
    const line = headingLine(cam);
    if (line) L.polyline(line, { color: "#eef6ff", weight: 1.3, opacity: 0.7, interactive: false }).addTo(cameraLayer!);
    if (showLabels.value) {
      L.marker([cam.location.lat, cam.location.lon], {
        interactive: false,
        icon: L.divIcon({
          className: "gis-label",
          html: `<span>${cam.display_name || "Map #" + cam.map_camera_id}</span>`,
          iconSize: [0, 0],
        }),
      }).addTo(labelLayer!);
    }
  }
}

function drawBasemap(data: Basemap) {
  if (!map) return;
  roadLayer?.remove();
  buildingLayer?.remove();
  const roadFeatures = data.roads.map((r) => ({ type: "Feature", properties: { road: r }, geometry: r.geometry }));
  const buildingFeatures = data.buildings.map((b) => ({ type: "Feature", properties: {}, geometry: b.geometry }));
  const roadOptions: GeoJsonOpts = {
    renderer: canvas,
    style: (feature) => roadStyle((feature?.properties as { road: Road }).road),
    interactive: false,
  };
  const buildingOptions: GeoJsonOpts = {
    renderer: canvas,
    style: () => ({ color: "#31465c", weight: 0.6, fillColor: "#1b2939", fillOpacity: 0.85, interactive: false }),
    interactive: false,
  };
  roadLayer = L.geoJSON(roadFeatures as unknown as GeoJSON.GeoJsonObject, roadOptions);
  buildingLayer = L.geoJSON(buildingFeatures as unknown as GeoJSON.GeoJsonObject, buildingOptions);
  if (showRoads.value) roadLayer.addTo(map);
  if (showBuildings.value && map.getZoom() >= 15) buildingLayer.addTo(map);
}

function applyToggles() {
  if (!map) return;
  if (roadLayer) {
    if (showRoads.value && !map.hasLayer(roadLayer)) roadLayer.addTo(map);
    if (!showRoads.value && map.hasLayer(roadLayer)) roadLayer.remove();
  }
  if (buildingLayer) {
    const want = showBuildings.value && map.getZoom() >= 15;
    if (want && !map.hasLayer(buildingLayer)) buildingLayer.addTo(map);
    if (!want && map.hasLayer(buildingLayer)) buildingLayer.remove();
  }
  drawCameras();
  if (cameraLayer) {
    if (showCameras.value && !map.hasLayer(cameraLayer)) cameraLayer.addTo(map);
    if (!showCameras.value && map.hasLayer(cameraLayer)) cameraLayer.remove();
  }
  if (fovLayer) {
    if (showFov.value && !map.hasLayer(fovLayer)) fovLayer.addTo(map);
    if (!showFov.value && map.hasLayer(fovLayer)) fovLayer.remove();
  }
  if (labelLayer) {
    if (showLabels.value && !map.hasLayer(labelLayer)) labelLayer.addTo(map);
    if (!showLabels.value && map.hasLayer(labelLayer)) labelLayer.remove();
  }
}

function boundsKey(bounds: L.LatLngBounds, level: number): string {
  const round = (v: number) => v.toFixed(4);
  return [round(bounds.getWest()), round(bounds.getSouth()), round(bounds.getEast()), round(bounds.getNorth()), level].join(",");
}

async function loadBasemap() {
  if (!map) return;
  const level = Math.round(map.getZoom());
  zoom.value = level;
  if (level < 13) {
    notice.value = "缩放级别过低，道路与建筑在放大后按范围加载。";
    return;
  }
  const bounds = map.getBounds();
  const key = boundsKey(bounds, level);
  if (key === lastKey && basemap.value) return;
  lastKey = key;
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  basemapError.value = "";
  notice.value = level < 15 ? "当前级别不加载建筑，放大到 15 级后出现。" : "";
  try {
    const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()]
      .map((v) => v.toFixed(6)).join(",");
    const response = await fetch(
      `/api/v1/spatial/basemap?bbox=${bbox}&zoom=${level}`,
      { signal: current.signal, cache: "no-store" },
    );
    const body = await response.json();
    if (!response.ok || body?.status !== "available") throw new Error(body?.error?.code || "map_basemap_unavailable");
    if (!Array.isArray(body.roads) || !Array.isArray(body.buildings)) throw new Error("invalid_basemap_response");
    basemap.value = body as Basemap;
    drawBasemap(body as Basemap);
    lastKey = key;
  } catch (e) {
    if (current.signal.aborted) return;
    basemapError.value = e instanceof Error ? e.message : "request_failed";
  } finally {
    if (controller === current) loading.value = false;
  }
}

async function loadCameras() {
  cameraError.value = "";
  try {
    const response = await fetch("/api/v1/spatial/cameras", { cache: "no-store" });
    const body = await response.json();
    if (!response.ok || body?.status !== "available") throw new Error(body?.error?.code || "map_unavailable");
    cameras.value = body as CameraResult;
    drawCameras();
  } catch (e) {
    cameraError.value = e instanceof Error ? e.message : "request_failed";
  }
}

function scheduleLoad() {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => { void loadBasemap(); }, 250);
}

watch([showRoads, showBuildings, showCameras, showFov, showLabels], applyToggles);

onMounted(() => {
  if (!host.value) return;
  const instance = L.map(host.value, { zoomControl: false, attributionControl: true, preferCanvas: true })
    .setView([25.2512, 110.2084], 16);
  map = instance;
  canvas = L.canvas({ padding: 0.4, tolerance: 6 });
  instance.createPane("fovPane");
  instance.getPane("fovPane")!.style.zIndex = "415";
  canvasFov = L.canvas({ padding: 0.4, tolerance: 6, pane: "fovPane" });
  L.control.scale({ imperial: false, position: "bottomright" }).addTo(instance);
  L.control.zoom({ position: "topleft" }).addTo(instance);
  instance.attributionControl.setPrefix(false);
  instance.attributionControl.addAttribution("道路/建筑 © OpenStreetMap contributors (ODbL) · 台账 TrajNexa_Map（只读）");
  fovLayer = L.layerGroup().addTo(instance);
  cameraLayer = L.layerGroup().addTo(instance);
  labelLayer = L.layerGroup().addTo(instance);
  instance.on("moveend", scheduleLoad);
  instance.on("zoomend", () => { zoom.value = Math.round(instance.getZoom()); applyToggles(); scheduleLoad(); });
  void loadCameras();
  void loadBasemap();
});

onUnmounted(() => {
  controller?.abort();
  window.clearTimeout(timer);
  map?.remove();
  map = null;
});
</script>

<template>
  <section class="gis">
    <header>
      <div class="title">
        <strong>Map 台账 · GIS 地图</strong>
        <small>道路与建筑来自 TrajNexa_Map 只读库 · 缩放 {{ zoom }} 级 · 不使用示意坐标</small>
      </div>
      <div class="status">
        <span v-if="basemap">道路 {{ basemap.counts.roads }} · 建筑 {{ basemap.counts.buildings }}</span>
        <span v-if="loading" class="dim">加载中…</span>
        <span v-if="basemap" class="dim">{{ basemap.basemap_revision }}</span>
        <button type="button" class="ghost" @click="map?.fitBounds([[25.249, 110.204], [25.254, 110.214]])">回到金水路</button>
        <button type="button" class="ghost" @click="loadBasemap">刷新</button>
      </div>
    </header>

    <div class="toolbar">
      <label><input v-model="showRoads" type="checkbox" />道路</label>
      <label><input v-model="showBuildings" type="checkbox" />建筑</label>
      <label><input v-model="showCameras" type="checkbox" />摄像头</label>
      <label><input v-model="showFov" type="checkbox" />FOV</label>
      <label><input v-model="showLabels" type="checkbox" />名称</label>
      <span class="legend"><i class="lv1" />辨识 <i class="lv2" />识别 <i class="lv3" />观察</span>
      <span class="dim">蓝点＝台账已关联 · 橙点＝仅 GIS 点位</span>
    </div>

    <p v-if="cameraError || basemapError" class="banner err">
      数据读取失败：{{ basemapError || cameraError }}（不显示任何替代几何，请检查 Map 只读账号与网络）
    </p>
    <p v-else-if="notice" class="banner warn">{{ notice }}</p>

    <div ref="host" class="canvas" role="application" aria-label="TrajNexa_Map 真实道路、建筑与摄像头图层" />
    <p class="foot">
      数据来源：TrajNexa_Map 台账（只读）· {{ cameras?.counts.displayable_points ?? 0 }} 个镜头 ·
      FOV 为模型计算几何，不构成真实覆盖证明，也不构成身份结论。
    </p>
  </section>
</template>

<style scoped>
.gis {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  min-width: 0;
  background: #0b1522;
  color: #dbe8f6;
  border: 1px solid #26394d;
}
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid #26394d;
}
.title strong { font-size: 12px; }
.title small { display: block; margin-top: 3px; color: #9ab1c7; font-size: 10px; }
.status { display: flex; align-items: center; gap: 10px; font-size: 10px; }
.status .ghost {
  color: #d0e9ff; background: #123047; border: 1px solid #426887;
  border-radius: 5px; padding: 4px 8px; cursor: pointer; font-size: 10px;
}
.toolbar {
  display: flex; flex-wrap: wrap; align-items: center; gap: 12px;
  padding: 6px 12px; border-bottom: 1px solid #1f2f42; font-size: 11px;
}
.toolbar label { display: flex; align-items: center; gap: 4px; cursor: pointer; }
.legend { display: flex; align-items: center; gap: 4px; color: #b9cde0; }
.legend i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; margin-right: 3px; }
.legend i:not(:first-child) { margin-left: 8px; }
.legend .lv1 { background: #ff3131; }
.legend .lv2 { background: #ffbf00; }
.legend .lv3 { background: #00b300; }
.dim { color: #8ea6bd; }
.banner { margin: 0; padding: 6px 12px; font-size: 11px; }
.banner.err { background: #3a1d21; color: #f4b8b8; }
.banner.warn { background: #2c2617; color: #e8c887; }
.canvas { flex: 1; min-height: 0; background: #0d1724; }
.foot {
  margin: 0; padding: 6px 12px; border-top: 1px solid #1f2f42;
  font-size: 10px; color: #8ea6bd;
}
:deep(.leaflet-container) { background: #0d1724; font: inherit; }
:deep(.leaflet-control-attribution) {
  background: rgba(9, 20, 32, 0.82); color: #9ab1c7; font-size: 10px;
}
:deep(.leaflet-control-attribution a) { color: #9ecdf6; }
:deep(.leaflet-popup-content-wrapper) {
  background: #101f2f; color: #dbe8f6; border: 1px solid #35526f; font-size: 11px;
}
:deep(.leaflet-popup-tip) { background: #101f2f; }
:deep(.gis-label span) {
  display: inline-block; transform: translate(6px, -16px);
  padding: 1px 4px; border-radius: 3px;
  background: rgba(9, 20, 32, 0.8); color: #e6f1ff; font-size: 10px; white-space: nowrap;
}
</style>
