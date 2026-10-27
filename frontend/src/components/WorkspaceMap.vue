<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import AppIcon from "./ui/AppIcon.vue";
import MockFrame from "./ui/MockFrame.vue";
import {
  cameras,
  coverageDate,
  coverageMinutes,
  hasCoverage,
  minutesToLabel,
  timeWindowPresets,
  type Camera,
} from "../data/mock";
import { actions, layers, observationList, timeWindow, visibleElements, wb } from "../store/workbench";
import { cameraCode, cameraLabel, loadMapSnapshot, type MapSnapshot, type SnapshotCamera } from "../services/mapSnapshot";

/* ---------------------------- 地理投影 ----------------------------
 * 底图为真实 OSM 瓦片（WGS84）。默认视野对准桂林临桂（Map 库中两台已标定
 * 镜头的所在片区）。演示业务层沿用 1400×430 的世界坐标，以固定仿射关系
 * 投影到该片区，仅用于界面演示，不代表真实轨迹。
 * ------------------------------------------------------------------ */

const WORLD = { w: 1400, h: 430 };
const GEO = { west: 110.20302, south: 25.24968, east: 110.21394, north: 25.25272 };
const GEO_BOUNDS = L.latLngBounds([GEO.south, GEO.west], [GEO.north, GEO.east]);
const METERS_PER_DEG_LAT = 111320;

const geoHeightMeters = (GEO.north - GEO.south) * METERS_PER_DEG_LAT;

const toLatLng = (x: number, y: number): L.LatLng =>
  L.latLng(
    GEO.north - (y / WORLD.h) * (GEO.north - GEO.south),
    GEO.west + (x / WORLD.w) * (GEO.east - GEO.west),
  );

const worldRadiusMeters = (radius: number) => (radius / WORLD.h) * geoHeightMeters;

/* ---------------------------- 地图实例 ---------------------------- */

const mapEl = ref<HTMLElement | null>(null);
const mapSize = ref({ w: 0, h: 0 });
const zoom = ref(16);
const cursor = ref<{ lat: number; lng: number } | null>(null);
let map: L.Map | null = null;

const lMock = L.layerGroup();
const lReal = L.layerGroup();
const lFocus = L.layerGroup();
const lTracks = L.layerGroup();
const lAnchors = L.layerGroup();
const lLabels = L.layerGroup();

/** 与 TrajNexa_Map 同一套视域等级配色（Map 侧 FOV_STYLES：辨识 / 识别 / 观察）。 */
const FOV_COLORS: Record<string, string> = {
  identification: "#ff3131",
  recognition: "#ffbf00",
  observation: "#00b300",
};

function baseTiles(): L.TileLayer {
  return L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "© OpenStreetMap contributors · 镜头数据 TrajNexa_Map",
  });
}

function satelliteTiles(): L.TileLayer {
  return L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19,
    attribution: "影像 © Esri · Maxar · Earthstar Geographics",
  });
}

type Basemap = "map" | "satellite";
const basemap = ref<Basemap>("map");
let tileLayer: L.TileLayer | null = null;

function setBasemap(kind: Basemap) {
  if (!map || basemap.value === kind) return;
  basemap.value = kind;
  if (tileLayer) map.removeLayer(tileLayer);
  tileLayer = (kind === "satellite" ? satelliteTiles() : baseTiles()).addTo(map);
}

const query = ref("");
const hits = ref<{ id: number; label: string; lat: number; lon: number }[]>([]);

function runSearch() {
  const text = query.value.trim().toLowerCase();
  if (!text) {
    hits.value = [];
    return;
  }
  hits.value = (snapshot.value?.cameras ?? [])
    .filter((camera) =>
      `${camera.camera_id} ${camera.index_code} ${camera.external_index_code} ${camera.zone} ${camera.display_name} ${cameraCode(camera)}`.toLowerCase().includes(text),
    )
    .slice(0, 6)
    .map((camera) => ({ id: camera.camera_id, label: cameraLabel(camera), lat: camera.lat, lon: camera.lon }));
  if (hits.value.length === 1) focusHit(hits.value[0]);
}

function focusHit(hit: { lat: number; lon: number; id: number; label: string }) {
  hits.value = [];
  query.value = hit.label;
  map?.setView([hit.lat, hit.lon], Math.max(18, map?.getZoom() ?? 18));
  selectedRealId.value = hit.id;
}

function initMap() {
  if (!mapEl.value) return;
  map = L.map(mapEl.value, {
    center: GEO_BOUNDS.getCenter(),
    zoom: 16,
    zoomControl: false,
    attributionControl: false,
    preferCanvas: true,
  });
  tileLayer = baseTiles().addTo(map);
  [lFocus, lReal, lMock, lTracks, lAnchors, lLabels].forEach((group) => group.addTo(map!));
  map.fitBounds(GEO_BOUNDS, { padding: [24, 24] });
  zoom.value = map.getZoom();

  map.on("zoomend", () => {
    zoom.value = map!.getZoom();
  });
  map.on("move zoom resize", () => {
    placeCard();
  });
  map.on("move zoom resize", () => {
    placeCard();
    syncSize();
  });
  map.on("zoomend", () => {
    syncSize();
  });
  map.on("mousemove", (event: L.LeafletMouseEvent) => {
    cursor.value = { lat: event.latlng.lat, lng: event.latlng.lng };
  });
  map.on("mouseout", () => {
    cursor.value = null;
  });
  syncSize();
}

function syncSize() {
  if (!map) return;
  const size = map.getSize();
  mapSize.value = { w: size.x, h: size.y };
}

function zoomBy(factor: number) {
  if (!map) return;
  if (factor > 1) map.zoomIn();
  else map.zoomOut();
}

function resetView() {
  map?.fitBounds(GEO_BOUNDS, { padding: [24, 24] });
}

/* ---------------------------- Map 快照 ---------------------------- */

const snapshotState = ref<"loading" | "ready" | "error">("loading");
const snapshot = ref<MapSnapshot | null>(null);
const realVisible = ref(true);
const selectedRealId = ref<number | null>(null);

async function reloadSnapshot() {
  snapshotState.value = "loading";
  try {
    snapshot.value = await loadMapSnapshot(true);
    snapshotState.value = "ready";
  } catch {
    snapshotState.value = "error";
  }
  render();
}

const selectedReal = computed(
  () => snapshot.value?.cameras.find((item) => item.camera_id === selectedRealId.value) ?? null,
);


/* ---------------------------- 弹层 ---------------------------- */

const openPanel = ref<null | "layers" | "legend" | "time">(null);
const togglePanel = (panel: "layers" | "legend" | "time") => {
  openPanel.value = openPanel.value === panel ? null : panel;
};

const customStart = ref("18:00");
const customEnd = ref("22:00");

function applyCustomWindow() {
  const parse = (value: string) => {
    const [h, m] = value.split(":").map(Number);
    return (h || 0) * 60 + (m || 0);
  };
  const start = parse(customStart.value);
  const end = parse(customEnd.value) || 1440;
  if (start >= end) {
    actions.setWindow(1080, 1440);
    return;
  }
  actions.setWindow(start, end);
}

/* ---------------------------- 摄像头状态 ---------------------------- */

const isActive = (camera: Camera) => hasCoverage(camera, timeWindow);
const activeCount = computed(() => cameras.filter((c) => isActive(c)).length);
const windowLabel = computed(
  () => `${minutesToLabel(timeWindow.start)}–${timeWindow.end >= 1440 ? "24:00" : minutesToLabel(timeWindow.end)}`,
);

/* ---------------------------- 选中态 ---------------------------- */

const selectedCamera = computed(() => cameras.find((c) => c.id === wb.selectedCameraId) ?? null);
const camCardOpen = ref(false);
const anchorCardOpen = ref(false);
const realCardOpen = ref(false);

const cardLatLng = ref<L.LatLng | null>(null);
const cardPoint = ref<{ x: number; y: number } | null>(null);

function placeCard() {
  if (!map || !cardLatLng.value) {
    cardPoint.value = null;
    return;
  }
  const point = map.latLngToContainerPoint(cardLatLng.value);
  cardPoint.value = { x: point.x, y: point.y };
}

const CARD = { w: 272, h: 214 };

const cardStyle = computed(() => {
  const point = cardPoint.value;
  if (!point) return undefined;
  const box = mapSize.value;
  const left = Math.min(Math.max(point.x + 16, 8), Math.max(box.w - CARD.w - 8, 8));
  const top = Math.min(Math.max(point.y - 92, 8), Math.max(box.h - CARD.h, 8));
  return { left: `${left}px`, top: `${top}px`, bottom: "auto" };
});

function pickCamera(camera: Camera) {
  actions.selectCamera(camera.id);
  camCardOpen.value = true;
  anchorCardOpen.value = false;
  realCardOpen.value = false;
  cardLatLng.value = toLatLng(camera.x, camera.y);
  placeCard();
}

function pickReal(camera: SnapshotCamera) {
  selectedRealId.value = camera.camera_id;
  realCardOpen.value = true;
  camCardOpen.value = false;
  anchorCardOpen.value = false;
  cardLatLng.value = L.latLng(camera.lat, camera.lon);
  placeCard();
}

function pickAnchor(elementId: string) {
  actions.selectAnchor(elementId);
  anchorCardOpen.value = true;
  camCardOpen.value = false;
  realCardOpen.value = false;
  const element = visibleElements.value.find((item) => item.id === elementId);
  const camera = cameras.find((item) => item.id === element?.cameraId);
  cardLatLng.value = camera ? toLatLng(camera.x, camera.y) : toLatLng(800, 330);
  placeCard();
}

watch(
  () => wb.selectedObservationId,
  (id) => {
    const obs = observationList.value.find((o) => o.id === id);
    if (obs?.kind === "anchor" && obs.elementId) {
      anchorCardOpen.value = true;
      camCardOpen.value = false;
      realCardOpen.value = false;
      const element = visibleElements.value.find((item) => item.id === obs.elementId);
      const camera = cameras.find((item) => item.id === element?.cameraId);
      cardLatLng.value = camera ? toLatLng(camera.x, camera.y) : toLatLng(800, 330);
      placeCard();
    } else if (obs && obs.kind !== "gap" && obs.cameraId) {
      const camera = cameras.find((item) => item.id === obs.cameraId);
      if (camera) {
        camCardOpen.value = true;
        anchorCardOpen.value = false;
        realCardOpen.value = false;
        cardLatLng.value = toLatLng(camera.x, camera.y);
        placeCard();
      }
    }
  },
);

const coverageWindowMinutes = computed(() => (selectedCamera.value ? coverageMinutes(selectedCamera.value, timeWindow) : 0));

const linkedCameraId = computed(() => {
  const obs = observationList.value.find((o) => o.id === wb.selectedObservationId);
  if (!obs || obs.kind === "anchor" || obs.kind === "gap") return null;
  return obs.cameraId;
});

const anchorChips = computed(() => visibleElements.value.find((e) => e.id === wb.selectedAnchorId) ?? null);

/* 锚点坐标：有摄像头则贴镜头，仅有区域则画区域圈 */
const regionAnchor = { x: 800, y: 330, r: 62 };
const cameraAnchors = computed(() =>
  visibleElements.value
    .map((element) => ({ element, camera: cameras.find((c) => c.id === element.cameraId) ?? null }))
    .filter((item): item is { element: (typeof visibleElements.value)[number]; camera: Camera } => item.camera !== null),
);
const regionAnchors = computed(() => visibleElements.value.filter((element) => !element.cameraId));

const tracks = [
  { id: "verified", tone: "verified", dash: "", label: "已核验片段", labelAt: [470, 206], points: "302,198 400,216 500,228 620,236" },
  { id: "gap", tone: "gap", dash: "5 7", label: "未知盲区", labelAt: [676, 286], points: "620,236 684,268 736,300 790,318" },
  { id: "candidate", tone: "candidate", dash: "8 7", label: "待验证连接", labelAt: [884, 372], points: "790,318 856,344 936,372 1010,392" },
  { id: "branch", tone: "branch", dash: "2 8", label: "多分支", labelAt: [948, 250], points: "856,344 902,314 962,282 1032,246" },
];

/* ---------------------------- 图层渲染 ---------------------------- */

const TRACK_COLOR: Record<string, string> = {
  verified: "rgba(47,207,146,0.92)",
  candidate: "rgba(240,180,90,0.85)",
  branch: "rgba(240,180,90,0.45)",
  gap: "rgba(140,158,180,0.45)",
};

const parseWorldPoints = (raw: string): L.LatLng[] =>
  raw
    .split(" ")
    .map((pair) => pair.split(",").map(Number))
    .filter((pair) => pair.length === 2 && Number.isFinite(pair[0]) && Number.isFinite(pair[1]))
    .map(([x, y]) => toLatLng(x, y));

function mockCameraIcon(camera: Camera): L.DivIcon {
  const classes = [
    "mk",
    wb.selectedCameraId === camera.id ? "is-selected" : "",
    linkedCameraId.value === camera.id ? "is-linked" : "",
    camera.analyzing ? "is-analyzing" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return L.divIcon({
    className: "mk-wrap",
    html: `<span class="${classes}" data-tone="${isActive(camera) ? "ok" : "idle"}"><i></i>${
      camera.notice ? '<b class="mk-note">!</b>' : ""
    }</span>`,
    iconSize: [0, 0],
    iconAnchor: [0, 0],
  });
}

function textIcon(text: string, className: string, offset: [number, number] = [0, 0]): L.DivIcon {
  return L.divIcon({
    className: "mk-wrap",
    html: `<span class="${className}">${text}</span>`,
    iconSize: [0, 0],
    iconAnchor: [offset[0], offset[1]],
  });
}

function anchorIcon(elementId: string, selected: boolean): L.DivIcon {
  return L.divIcon({
    className: "mk-wrap",
    html: `<span class="anchor-pin${selected ? " is-selected" : ""}"><svg viewBox="0 0 24 34" width="22" height="30" aria-hidden="true"><path d="M12 33C12 33 22 21.5 22 13A10 10 0 1 0 2 13C2 21.5 12 33 12 33Z" fill="#7c5cf0" stroke="#c3aeff" stroke-width="1.4"/><circle cx="12" cy="12.6" r="4" fill="#f0ecff"/></svg><em>${elementId}</em></span>`,
    iconSize: [22, 30],
    iconAnchor: [11, 30],
  });
}

function render() {
  if (!map) return;
  [lMock, lReal, lFocus, lTracks, lAnchors, lLabels].forEach((group) => group.clearLayers());

  /* 真实镜头：TrajNexa_Map 只读快照 */
  if (realVisible.value) {
    for (const camera of snapshot.value?.cameras ?? []) {
      const calibrated = camera.pose_quality === "calibrated";
      if (layers.fov) {
        if (camera.fov_levels?.length) {
          // 与 TrajNexa_Map 同一套等级配色：辨识 #ff3131 / 识别 #ffbf00 / 观察 #00b300
          for (const group of camera.fov_levels) {
            const color = FOV_COLORS[group.level] ?? FOV_COLORS.observation;
            for (const ring of group.rings) {
              L.polygon(
                ring.map(([lng, lat]) => [lat, lng] as [number, number]),
                { stroke: false, fillColor: color, fillOpacity: 0.4, interactive: false },
              ).addTo(lFocus);
            }
          }
        } else if (camera.fov_rings) {
          for (const ring of camera.fov_rings) {
            L.polygon(
              ring.map(([lng, lat]) => [lat, lng] as [number, number]),
              { stroke: false, fillColor: FOV_COLORS.observation, fillOpacity: 0.4, interactive: false },
            ).addTo(lFocus);
          }
        }
      }
      const selected = selectedRealId.value === camera.camera_id;
      const marker = L.circleMarker([camera.lat, camera.lon], {
        radius: selected ? 7.5 : calibrated ? 5.2 : 4.6,
        color: selected ? "#d8f6ff" : "#060a0f",
        weight: selected ? 2 : 1.4,
        fillColor: calibrated ? "#4fd1d9" : "#8b9bb0",
        fillOpacity: 0.92,
      }).addTo(lReal);
      marker.bindTooltip(
        `${cameraLabel(camera)} · ${calibrated ? "已标定姿态" : "姿态未知（不画扇形）"}`,
        { direction: "top", offset: [0, -6], className: "map-tip" },
      );
      marker.on("click", () => pickReal(camera));
    }
  }

  /* 演示业务层：摄像头 */
  if (layers.cameras) {
    for (const camera of cameras) {
      L.marker(toLatLng(camera.x, camera.y), { icon: mockCameraIcon(camera), keyboard: false })
        .addTo(lMock)
        .on("click", () => pickCamera(camera));
      if (layers.labels) {
        L.marker(toLatLng(camera.x, camera.y), {
          icon: textIcon(camera.name, `mk-label${isActive(camera) ? " is-on" : ""}`),
          interactive: false,
          keyboard: false,
        }).addTo(lLabels);
      }
    }
  }

  /* 演示业务层：轨迹 */
  if (layers.tracks) {
    for (const track of tracks) {
      L.polyline(parseWorldPoints(track.points), {
        color: TRACK_COLOR[track.tone] ?? TRACK_COLOR.gap,
        weight: track.tone === "verified" ? 2.6 : 1.8,
        dashArray: track.dash || undefined,
        lineCap: "round",
        interactive: false,
      }).addTo(lTracks);
      if (layers.labels) {
        L.marker(toLatLng(track.labelAt[0], track.labelAt[1]), {
          icon: textIcon(track.label, `track-label is-${track.tone}`),
          interactive: false,
          keyboard: false,
        }).addTo(lTracks);
      }
    }
  }

  /* 演示业务层：锚点 */
  if (layers.anchors) {
    for (const item of cameraAnchors.value) {
      L.marker(toLatLng(item.camera.x, item.camera.y - 8), {
        icon: anchorIcon(item.element.id, wb.selectedAnchorId === item.element.id),
        keyboard: false,
      })
        .addTo(lAnchors)
        .on("click", () => pickAnchor(item.element.id));
    }
    for (const element of regionAnchors.value) {
      const center = toLatLng(regionAnchor.x, regionAnchor.y);
      L.circle(center, {
        radius: worldRadiusMeters(regionAnchor.r),
        color: "rgba(169,139,255,0.55)",
        weight: 1.2,
        dashArray: "5 5",
        fillColor: "#a98bff",
        fillOpacity: 0.07,
      })
        .addTo(lAnchors)
        .on("click", () => pickAnchor(element.id));
      L.marker(center, {
        icon: textIcon(`${element.id} · 区域锚点（待定位）`, "region-label"),
        interactive: false,
        keyboard: false,
      }).addTo(lAnchors);
    }
  }
}

watch(
  [layers, wb.selectedCameraId, wb.selectedObservationId, timeWindow, realVisible, selectedRealId, snapshot],
  () => render(),
  { deep: true },
);

onMounted(() => {
  initMap();
  render();
  void reloadSnapshot();
});

onBeforeUnmount(() => {
  map?.remove();
  map = null;
});

function focusAnchor() {
  resetView();
  if (anchorChips.value) {
    pickAnchor(anchorChips.value.id);
  }
}
</script>

<template>
  <section class="map-region">
    <div class="map-canvas">
      <div
        ref="mapEl"
        class="map-leaflet"
        role="img"
        aria-label="业务地图：TrajNexa_Map 发布的摄像头点位与标定视域，叠加演示业务层"
      />

      <div class="map-compass" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="26" height="26">
          <circle cx="12" cy="12" r="10.5" fill="rgba(8,13,19,0.7)" stroke="#25384c" />
          <path d="M12 5.5 14.2 12 12 10.6 9.8 12Z" fill="#8fb6ff" />
          <path d="M12 18.5 9.8 12 12 13.4l2.2-1.4Z" fill="#33465c" />
        </svg>
      </div>

      <div v-if="snapshotState !== 'ready'" class="map-snapshot-hint" :data-tone="snapshotState">
        <AppIcon :name="snapshotState === 'error' ? 'warning' : 'refresh'" :size="13" />
        <span>{{ snapshotState === "error" ? "Map 空间数据不可用：仅显示演示业务层" : "正在加载 Map 空间数据…" }}</span>
        <button v-if="snapshotState === 'error'" class="btn btn--sm btn--outline" @click="reloadSnapshot">重试</button>
      </div>
    </div>

    <!-- 顶部工具条 -->
    <div class="map-toolbar popover" @click.stop>
      <div class="tb-seg">
        <button class="tb-seg-btn" :class="{ 'is-on': basemap === 'map' }" :aria-pressed="basemap === 'map'" @click="setBasemap('map')">地图</button>
        <button class="tb-seg-btn" :class="{ 'is-on': basemap === 'satellite' }" :aria-pressed="basemap === 'satellite'" @click="setBasemap('satellite')">卫星</button>
        <button class="tb-seg-btn" disabled title="三维地形需要 WebGL 服务，本版本未接入">三维</button>
      </div>
      <label class="tb-search" :class="{ 'is-open': hits.length > 0 }">
        <AppIcon name="search" :size="13" />
        <input
          v-model="query"
          type="search"
          placeholder="搜索摄像头 / 编号"
          @input="query && runSearch()"
          @keydown.enter.prevent="runSearch"
        />
        <ul v-if="hits.length" class="tb-hits">
          <li v-for="hit in hits" :key="hit.id">
            <button @click="focusHit(hit)">{{ hit.label }}</button>
          </li>
        </ul>
      </label>
      <span class="tb-sep" />
      <button class="tb-btn tb-btn--icon" :class="{ 'is-open': openPanel === 'layers' }" title="图层" aria-label="图层" @click="togglePanel('layers')">
        <AppIcon name="layers" :size="14" />
      </button>
      <button class="tb-btn tb-btn--icon" :class="{ 'is-open': openPanel === 'legend' }" title="图例" aria-label="图例" @click="togglePanel('legend')">
        <AppIcon name="list" :size="14" />
      </button>
      <button class="tb-btn tb-btn--icon" title="定位到起始锚点" aria-label="定位到起始锚点" @click="focusAnchor">
        <AppIcon name="target" :size="14" />
      </button>
      <button class="tb-btn tb-btn--icon" title="录像覆盖" aria-label="录像覆盖" @click="actions.openCoverage()">
        <AppIcon name="video" :size="14" />
      </button>
      <button class="tb-time" :class="{ 'is-open': openPanel === 'time' }" @click="togglePanel('time')">
        <AppIcon name="clock" :size="13" />
        <span class="num">{{ windowLabel }}</span>
        <AppIcon name="chevron-down" :size="11" />
      </button>
    </div>

    <!-- 图层开关 -->
    <div class="map-chips popover" @click.stop>
      <label class="map-chip"><input v-model="layers.fov" type="checkbox" /><span>摄像头视域</span></label>
      <label class="map-chip"><input v-model="layers.tracks" type="checkbox" /><span>轨迹图层</span></label>
      <label class="map-chip"><input v-model="layers.labels" type="checkbox" /><span>名称标注</span></label>
      <label class="map-chip"><input v-model="layers.anchors" type="checkbox" /><span>起始锚点</span></label>
      <button class="map-chip map-chip--more" :class="{ 'is-open': openPanel === 'layers' }" @click="togglePanel('layers')">
        更多图层
        <AppIcon name="chevron-down" :size="11" />
      </button>
    </div>

    <div v-if="openPanel === 'layers'" class="map-panel map-panel--layers popover" @click.stop>
      <header class="panel-head">
        <span>图层</span>
        <span class="dim">{{ snapshot ? snapshot.spatial_revision : "空间数据未就绪" }}</span>
      </header>
      <label class="layer-row">
        <input v-model="realVisible" type="checkbox" /><span>Map 镜头（TrajNexa_Map）</span><em>{{ snapshot?.cameras.length ?? 0 }}</em>
      </label>
      <label class="layer-row">
        <input v-model="layers.fov" type="checkbox" /><span>标定视域 FOV</span><em>{{ snapshot?.counts.with_focus ?? 0 }}</em>
      </label>
      <label class="layer-row"><input v-model="layers.cameras" type="checkbox" /><span>任务镜头（演示）</span><em>{{ cameras.length }}</em></label>
      <label class="layer-row"><input v-model="layers.anchors" type="checkbox" /><span>目标起始锚点</span><em>{{ visibleElements.length }}</em></label>
      <label class="layer-row"><input v-model="layers.tracks" type="checkbox" /><span>轨迹与盲区</span><em>{{ tracks.length }}</em></label>
      <label class="layer-row"><input v-model="layers.labels" type="checkbox" /><span>名称标注</span><em>—</em></label>
      <p class="panel-note">Map 镜头与视域来自 TrajNexa_Map 只读空间数据（主系统直连 Map 只读库）；任务镜头与轨迹为演示数据，按示意投影落图。</p>
    </div>

    <div v-if="openPanel === 'legend'" class="map-panel map-panel--legend popover" @click.stop>
      <header class="panel-head"><span>图例</span></header>
      <p class="legend-title">Map 镜头（TrajNexa_Map）</p>
      <ul class="legend">
        <li><i class="sw sw--real" />已标定姿态</li>
        <li><i class="sw sw--real-unknown" />姿态未知，仅点位（不虚构扇形）</li>
        <li><i class="sw sw--fov-ident" />视域 辨识（identification）</li>
        <li><i class="sw sw--fov-recog" />视域 识别（recognition）</li>
        <li><i class="sw sw--fov-obs" />视域 观察（observation）</li>
      </ul>
      <p class="legend-title">任务层录像可用性（演示）</p>
      <ul class="legend">
        <li><i class="sw sw--ok" />当前查询窗内有已索引录像</li>
        <li><i class="sw sw--idle" />当前窗内无已索引录像（<b>非离线</b>）</li>
        <li><i class="sw sw--warn">!</i>解析 / 映射异常，需人工核查</li>
        <li><i class="sw sw--pulse" />后台正在处理该摄像头</li>
      </ul>
      <p class="legend-title">轨迹可信度</p>
      <ul class="legend">
        <li><i class="ln ln--ok" />已核验且有直接录像支持</li>
        <li><i class="ln ln--cand" />待验证连接（虚线）</li>
        <li><i class="ln ln--gap" />未知盲区，不默认连通</li>
        <li><i class="ln ln--anchor" />起始锚点（独立编号）</li>
      </ul>
    </div>

    <div v-if="openPanel === 'time'" class="map-panel map-panel--time popover" @click.stop>
      <header class="panel-head">
        <span>地图查询时间窗</span>
        <span class="dim">{{ coverageDate }}</span>
      </header>
      <label v-for="preset in timeWindowPresets" :key="preset.id" class="time-row" :class="{ 'is-on': timeWindow.preset === preset.id }">
        <input type="radio" name="tw" :checked="timeWindow.preset === preset.id" @change="actions.applyPreset(preset.id)" />
        <span>{{ preset.label }}</span>
      </label>
      <div class="time-custom">
        <span class="dim">自定义</span>
        <input v-model="customStart" class="input input--time num" type="time" step="60" />
        <span class="dim">→</span>
        <input v-model="customEnd" class="input input--time num" type="time" step="60" />
        <button class="btn btn--sm btn--outline" @click="applyCustomWindow">应用</button>
      </div>
      <p class="panel-note">仅影响地图可用性与覆盖时间轴的查询范围，不改变观测事实。</p>
    </div>

    <div v-if="wb.emptyMode" class="map-empty-hint">
      <AppIcon name="info" :size="14" />
      <span>未选择录像目录：摄像头录像可用性为灰色（不代表离线）</span>
      <button class="btn btn--sm btn--outline" @click="actions.openCoverage()">查看覆盖说明</button>
    </div>

    <!-- 右侧缩放 -->
    <div class="map-zoom popover" @click.stop>
      <button class="icon-btn" aria-label="放大" title="放大" @click="zoomBy(1.25)"><AppIcon name="plus" :size="14" /></button>
      <button class="icon-btn" aria-label="缩小" title="缩小" @click="zoomBy(0.8)"><AppIcon name="minus" :size="14" /></button>
      <span class="zoom-sep" />
      <button class="icon-btn" aria-label="复位视图" title="复位视图" @click="resetView"><AppIcon name="scan" :size="14" /></button>
      <span class="zoom-level num">z{{ zoom }}</span>
    </div>

    <!-- 锚点操作卡 -->
    <Transition name="slide">
      <aside v-if="anchorCardOpen && anchorChips && !wb.emptyMode" class="anchor-card popover" :style="cardStyle">
        <div class="anchor-top">
          <div class="anchor-thumb">
            <MockFrame :tone="anchorChips.tone" :quality="anchorChips.quality" :time="anchorChips.time" :label="anchorChips.cameraLabel" :shape="'portrait'" />
          </div>
          <div class="anchor-info">
            <div class="anchor-line">
              <span class="anchor-id">{{ anchorChips.id }}</span>
              <span class="num anchor-time">{{ anchorChips.timeLabel }}</span>
              <button class="icon-btn anchor-close" aria-label="收起锚点操作卡" @click="anchorCardOpen = false">
                <AppIcon name="close" :size="13" />
              </button>
            </div>
            <p class="anchor-cam truncate">{{ anchorChips.cameraLabel }}</p>
            <div class="anchor-chips">
              <span class="chip" :data-tone="anchorChips.status === 'confirmed' ? 'ok' : anchorChips.status === 'candidate' ? 'warn' : 'muted'">
                {{ anchorChips.statusLabel }}
              </span>
              <span class="chip" data-tone="violet">起始锚点</span>
            </div>
          </div>
        </div>

        <div class="anchor-actions">
          <span class="dim">选择追查方向</span>
          <div class="dir-toggle">
            <button :class="{ 'is-on': wb.direction === 'backward' }" @click="actions.setDirection('backward')">
              <AppIcon name="arrow-left" :size="13" />向前倒查
            </button>
            <button :class="{ 'is-on': wb.direction === 'forward' }" @click="actions.setDirection('forward')">
              向后追查<AppIcon name="arrow-right" :size="13" />
            </button>
          </div>
        </div>

        <p class="anchor-range">
          已选 {{ wb.direction === "backward" ? "向前倒查" : "向后追查" }} · 范围 <span class="num">{{ wb.from }}–{{ wb.to }}</span> · 授权范围校验通过
        </p>

        <div class="anchor-foot">
          <button class="btn btn--primary btn--sm" @click="actions.startRound()">
            <AppIcon name="play" :size="12" />开始此轮研判
          </button>
          <button class="btn btn--ghost btn--sm" @click="actions.openElements()">
            <AppIcon name="folder" :size="12" />查看原件
          </button>
          <button class="btn btn--ghost btn--sm" @click="actions.openAddElement(anchorChips.id)">
            <AppIcon name="edit" :size="12" />编辑要素
          </button>
        </div>
        <p class="anchor-note">锚点只代表操作起点；不代表已确认跨镜头身份。</p>
      </aside>
    </Transition>

    <!-- 摄像头概览卡 -->
    <Transition name="slide">
      <aside v-if="camCardOpen && selectedCamera" class="cam-card popover" :style="cardStyle">
        <header class="cam-head">
          <span class="mono cam-id">{{ selectedCamera.id }}</span>
          <span class="chip" :data-tone="isActive(selectedCamera) ? 'ok' : 'muted'">
            <i class="dot" />{{ isActive(selectedCamera) ? "有已索引录像" : "无已索引录像" }}
          </span>
          <button class="icon-btn" aria-label="关闭摄像头卡片" @click="camCardOpen = false"><AppIcon name="close" :size="13" /></button>
        </header>
        <h3 class="cam-name">{{ selectedCamera.name }}</h3>
        <p class="cam-sub">
          {{ selectedCamera.site }} · {{ selectedCamera.channel }}
          <span class="dim mono">{{ selectedCamera.uuid }}</span>
        </p>
        <dl class="cam-kv">
          <div><dt>朝向</dt><dd class="num">{{ selectedCamera.heading }}°</dd></div>
          <div><dt>视域</dt><dd class="num">{{ selectedCamera.fov }}° · {{ selectedCamera.fovType }}</dd></div>
          <div><dt>标定</dt><dd>{{ selectedCamera.quality }}</dd></div>
          <div><dt>快照</dt><dd class="mono">{{ selectedCamera.source }}</dd></div>
        </dl>
        <div class="cam-cov">
          <span>当前窗内已索引 <b class="num">{{ coverageWindowMinutes }}</b> 分钟</span>
          <span v-if="selectedCamera.notice === 'mapping'" class="chip" data-tone="warn">映射歧义</span>
          <span v-if="selectedCamera.analyzing" class="chip" data-tone="info"><i class="dot" />处理中</span>
        </div>
        <div class="cam-actions">
          <button class="btn btn--sm btn--primary" @click="actions.openCoverage(selectedCamera.id)">
            <AppIcon name="video" :size="12" />查看录像时间
          </button>
          <button class="btn btn--sm btn--outline" @click="actions.openCorrection()">
            <AppIcon name="edit" :size="12" />修正工单
          </button>
        </div>
        <p class="cam-note">点位与 FOV 为 Map 发布数据；主系统只读，不回写 GIS。</p>
      </aside>
    </Transition>

    <!-- TrajNexa_Map 镜头卡 -->
    <Transition name="slide">
      <aside v-if="realCardOpen && selectedReal" class="cam-card cam-card--real popover" :style="cardStyle">
        <header class="cam-head">
          <span class="mono cam-id">{{ cameraLabel(selectedReal) }}</span>
          <span class="chip" :data-tone="selectedReal.pose_quality === 'calibrated' ? 'info' : 'muted'">
            <i class="dot" />{{ selectedReal.pose_quality === "calibrated" ? "已标定姿态" : "姿态未知" }}
          </span>
          <button class="icon-btn" aria-label="关闭镜头卡片" @click="realCardOpen = false"><AppIcon name="close" :size="13" /></button>
        </header>
        <p class="cam-sub">
          {{ selectedReal.camera_type || "类型未登记" }} · {{ selectedReal.zone || "区域未登记" }}
          <span class="dim mono">{{ cameraCode(selectedReal) }}</span>
        </p>
        <dl class="cam-kv">
          <div><dt>经纬度</dt><dd class="num">{{ selectedReal.lon.toFixed(5) }}, {{ selectedReal.lat.toFixed(5) }}</dd></div>
          <div><dt>朝向</dt><dd class="num">{{ selectedReal.bearing_deg ?? "—" }}°</dd></div>
          <div><dt>视域</dt><dd class="num">{{ selectedReal.fov_deg ?? "—" }}°</dd></div>
          <div><dt>量程</dt><dd class="num">{{ selectedReal.range_m ?? "—" }} m</dd></div>
        </dl>
        <div class="cam-actions">
          <button class="btn btn--sm btn--outline" @click="actions.openCorrection()">
            <AppIcon name="edit" :size="12" />上报修正工单
          </button>
        </div>
        <p class="cam-note">来自 TrajNexa_Map 只读空间数据；主系统不回写 GIS，不构成身份结论。</p>
      </aside>
    </Transition>

    <footer class="map-status">
      <span class="mono">{{ snapshot ? snapshot.spatial_revision : "空间数据未连接" }}</span>
      <span class="sep">·</span>
      <span>{{ snapshot?.cameras.length ?? 0 }} 台 Map 镜头</span>
      <span class="sep">·</span>
      <span class="ok">{{ activeCount }}/{{ cameras.length }} 台任务镜头窗内有索引</span>
      <span class="grow" />
      <span class="dim">查询窗 {{ coverageDate }} {{ windowLabel }}</span>
      <span class="sep">·</span>
      <span v-if="cursor" class="dim num">坐标 {{ cursor.lat.toFixed(5) }}, {{ cursor.lng.toFixed(5) }}</span>
      <span class="sep">·</span>
      <span class="dim">底图 © OpenStreetMap</span>
    </footer>
  </section>
</template>

<style scoped>
.map-region {
  position: relative;
  min-height: 0;
  overflow: hidden;
  background: var(--map-bg);
  border-bottom: 1px solid var(--line);
}

.map-canvas {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

/* 工具条 */
.map-toolbar {
  position: absolute;
  top: 10px;
  left: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 38px;
  padding: 3px 4px;
  border-radius: 9px;
  background: rgba(8, 12, 18, 0.92);
  backdrop-filter: blur(10px);
  z-index: 6;
}

.tb-seg {
  display: flex;
  gap: 2px;
  padding: 2px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
}

.tb-seg-btn {
  height: 26px;
  padding: 0 12px;
  border-radius: 5px;
  color: var(--text-2);
  font-size: 12px;
}

.tb-seg-btn:hover:not(:disabled) {
  color: var(--text-0);
}

.tb-seg-btn.is-on {
  background: #e8eef7;
  color: #10161f;
  font-weight: 600;
}

.tb-seg-btn:disabled {
  color: var(--text-4);
  cursor: not-allowed;
}

.tb-search {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  width: 214px;
  height: 28px;
  padding: 0 9px;
  border-radius: 7px;
  border: 1px solid var(--line-2);
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-3);
}

.tb-search.is-open {
  border-color: var(--accent-line);
}

.tb-search input {
  width: 100%;
  border: 0;
  background: none;
  color: var(--text-1);
  font-size: 11.5px;
  outline: none;
}

.tb-search input::placeholder {
  color: var(--text-4);
}

.tb-hits {
  position: absolute;
  top: 34px;
  left: 0;
  width: 100%;
  padding: 4px;
  border-radius: 8px;
  border: 1px solid var(--line-2);
  background: #0b1119;
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.5);
  z-index: 9;
}

.tb-hits button {
  display: block;
  width: 100%;
  padding: 5px 7px;
  border-radius: 5px;
  text-align: left;
  font-size: 11.5px;
  color: var(--text-1);
}

.tb-hits button:hover {
  background: var(--accent-soft);
  color: var(--accent-hi);
}

.tb-btn--icon {
  width: 28px;
  padding: 0;
  justify-content: center;
}

.map-chips {
  position: absolute;
  top: 10px;
  right: 10px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border-radius: 9px;
  background: rgba(8, 12, 18, 0.92);
  backdrop-filter: blur(10px);
  z-index: 5;
}

.map-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 9px;
  border-radius: 6px;
  color: var(--text-2);
  font-size: 11.5px;
  white-space: nowrap;
  cursor: pointer;
}

.map-chip:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--text-1);
}

.map-chip input {
  width: 12px;
  height: 12px;
  accent-color: var(--accent);
}

.map-chip--more {
  gap: 4px;
  border-left: 1px solid var(--line-2);
  border-radius: 0 6px 6px 0;
  padding-left: 10px;
}

.map-chip--more.is-open {
  color: var(--accent-hi);
}

.tb-group {
  display: flex;
  gap: 2px;
}

.tb-btn,
.tb-time {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 8px;
  border-radius: 6px;
  color: var(--text-2);
  font-size: var(--fs-note);
  white-space: nowrap;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}

.tb-btn:hover,
.tb-time:hover {
  background: var(--bg-hover);
  color: var(--text-0);
}

.tb-btn.is-open,
.tb-time.is-open {
  background: var(--accent-soft);
  color: var(--accent-hi);
}

.tb-sep {
  width: 1px;
  height: 16px;
  background: var(--line-2);
  margin: 0 2px;
}

.tb-time {
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}

.tb-time span {
  font-size: var(--fs-note);
}

.map-panel {
  position: absolute;
  top: 58px;
  left: 10px;
  width: 248px;
  padding: 10px;
  z-index: 6;
  animation: rise-in 0.14s var(--ease);
}

.map-panel--time {
  left: auto;
  right: 10px;
  width: 268px;
}

.map-panel--legend {
  width: 262px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 7px;
  margin-bottom: 6px;
  border-bottom: 1px solid var(--line);
  font-size: var(--fs-label);
  font-weight: 600;
  color: var(--text-1);
}

.panel-head .dim {
  font-weight: 400;
  font-size: 10px;
}

.layer-row,
.time-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 5px 4px;
  border-radius: 5px;
  font-size: var(--fs-label);
  color: var(--text-1);
  cursor: pointer;
}

.layer-row:hover,
.time-row:hover {
  background: rgba(255, 255, 255, 0.04);
}

.layer-row input,
.time-row input {
  accent-color: var(--accent);
  width: 12px;
  height: 12px;
  margin: 0;
}

.layer-row em {
  margin-left: auto;
  font-style: normal;
  font-size: 10px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.time-row.is-on {
  color: var(--accent-hi);
  background: var(--accent-soft);
}

.time-custom {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 7px 4px 3px;
  border-top: 1px solid var(--line);
  margin-top: 5px;
}

.input--time {
  height: 24px;
  padding: 0 5px;
  font-size: 10.5px;
  width: 78px;
}

.panel-note {
  margin-top: 7px;
  font-size: 10px;
  line-height: 1.5;
  color: var(--text-3);
  border-top: 1px dashed var(--line);
  padding-top: 7px;
}

.legend-title {
  font-size: 9.5px;
  letter-spacing: 0.08em;
  color: var(--text-3);
  margin: 7px 2px 4px;
}

.legend {
  display: grid;
  gap: 4px;
}

.legend li {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 10.5px;
  color: var(--text-2);
}

.legend b {
  color: var(--text-1);
  font-weight: 600;
}

.sw {
  display: inline-grid;
  place-items: center;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex: none;
}

.sw--ok {
  background: var(--ok);
  box-shadow: 0 0 6px rgba(47, 207, 146, 0.6);
}

.sw--idle {
  background: #4a5a70;
}

.sw--warn {
  background: var(--warn);
  color: #1a1205;
  font-size: 7px;
  font-style: normal;
  font-weight: 700;
}

.sw--pulse {
  border: 1.5px solid var(--accent);
  background: transparent;
}

.ln {
  width: 18px;
  height: 0;
  border-top-width: 2px;
  border-top-style: solid;
  flex: none;
}

.ln--ok {
  border-color: var(--ok);
}

.ln--cand {
  border-color: var(--warn);
  border-top-style: dashed;
}

.ln--gap {
  border-color: #55657a;
  border-top-style: dotted;
}

.ln--anchor {
  width: 8px;
  height: 8px;
  border: 0;
  border-radius: 50% 50% 50% 0;
  background: var(--violet);
  transform: rotate(-45deg);
}

.map-compass {
  position: absolute;
  top: 10px;
  right: 54px;
  opacity: 0.75;
  pointer-events: none;
}

.map-empty-hint {
  position: absolute;
  top: 52px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 6px 5px 12px;
  border-radius: 999px;
  border: 1px solid rgba(240, 180, 90, 0.3);
  background: rgba(30, 24, 12, 0.86);
  color: #f0d3a2;
  font-size: var(--fs-note);
  backdrop-filter: blur(8px);
  z-index: 5;
}

.map-zoom {
  position: absolute;
  top: 50%;
  right: 10px;
  transform: translateY(-50%);
  display: grid;
  justify-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 9px;
  background: rgba(10, 16, 24, 0.9);
  backdrop-filter: blur(10px);
}

.zoom-sep {
  width: 16px;
  height: 1px;
  background: var(--line-2);
  margin: 2px 0;
}

.zoom-level {
  font-size: 8.5px;
  color: var(--text-3);
  padding: 1px 0 2px;
}

/* 锚点操作卡 */
.anchor-card {
  position: absolute;
  left: 10px;
  bottom: 34px;
  width: 320px;
  padding: 10px;
  z-index: 5;
  display: grid;
  gap: 8px;
  background: rgba(11, 17, 25, 0.96);
}

.anchor-top {
  display: flex;
  gap: 9px;
}

.anchor-thumb {
  width: 58px;
  height: 68px;
  flex: none;
  border-radius: 7px;
  overflow: hidden;
  border: 1px solid var(--line-2);
}

.anchor-info {
  min-width: 0;
  flex: 1;
}

.anchor-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.anchor-id {
  font-size: var(--fs-strong);
  font-weight: 700;
  color: #d8ccff;
  letter-spacing: 0.02em;
}

.anchor-time {
  font-size: var(--fs-label);
  color: var(--text-1);
}

.anchor-close {
  margin-left: auto;
  width: 20px;
  height: 20px;
}

.anchor-cam {
  font-size: var(--fs-note);
  color: var(--text-2);
  margin-top: 2px;
}

.anchor-chips {
  display: flex;
  gap: 4px;
  margin-top: 5px;
}

.anchor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.anchor-actions > .dim {
  font-size: 10px;
}

.dir-toggle {
  display: flex;
  padding: 2px;
  border-radius: 6px;
  background: var(--bg-2);
  border: 1px solid var(--line);
}

.dir-toggle button {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 22px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 10px;
  color: var(--text-2);
}

.dir-toggle button.is-on {
  background: var(--accent-soft);
  color: var(--accent-hi);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.anchor-range {
  font-size: 10.5px;
  color: var(--text-2);
}

.anchor-foot {
  display: flex;
  align-items: center;
  gap: 4px;
}

.anchor-note {
  font-size: 9.5px;
  color: var(--text-3);
  border-top: 1px dashed var(--line);
  padding-top: 6px;
}

/* 摄像头卡 */
.cam-card {
  position: absolute;
  right: 10px;
  bottom: 34px;
  width: 276px;
  padding: 10px;
  z-index: 5;
  display: grid;
  gap: 6px;
  background: rgba(11, 17, 25, 0.96);
}

.cam-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.cam-id {
  font-size: 10px;
  color: var(--accent-hi);
  background: var(--accent-soft);
  border: 1px solid var(--accent-line);
  border-radius: 4px;
  padding: 0 4px;
}

.cam-head .icon-btn {
  margin-left: auto;
  width: 20px;
  height: 20px;
}

.cam-name {
  font-size: var(--fs-title);
  font-weight: 600;
  color: var(--text-0);
}

.cam-sub {
  display: flex;
  gap: 6px;
  font-size: 10px;
  color: var(--text-3);
}

.cam-kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 3px 10px;
  padding: 6px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.cam-kv > div {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-size: 10px;
}

.cam-kv dt {
  color: var(--text-3);
}

.cam-kv dd {
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cam-cov {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 10.5px;
  color: var(--text-2);
}

.cam-cov b {
  color: var(--text-0);
}

.cam-actions {
  display: flex;
  gap: 6px;
}

.cam-note {
  font-size: 9.5px;
  color: var(--text-3);
}

/* 底部状态条 */
.map-status {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 12px;
  font-size: 10px;
  color: var(--text-3);
  background: linear-gradient(180deg, rgba(6, 9, 14, 0), rgba(6, 9, 14, 0.9) 40%);
  pointer-events: none;
  z-index: 4;
}

.map-status .ok {
  color: #4fbf95;
}

.map-status .sep {
  color: var(--text-4);
}

.map-status .grow {
  flex: 1;
}

.slide-enter-active,
.slide-leave-active {
  transition: opacity 0.16s var(--ease), transform 0.18s var(--ease);
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

/* ---------------------------- Leaflet 底图 ---------------------------- */

.map-leaflet {
  position: absolute;
  inset: 0;
  z-index: 0;
  background: #eef1f4;
}

.map-leaflet :deep(.leaflet-container) {
  background: #eef1f4;
  font-family: var(--font);
  outline: none;
}

/* 底图与 TrajNexa_Map 完全一致：OSM 原色，不加反相/滤色，避免语义色被翻掉 */
.map-leaflet :deep(.leaflet-tile-pane) {
  filter: none;
}

.map-leaflet :deep(.leaflet-control-container) {
  display: none;
}

.map-snapshot-hint {
  position: absolute;
  top: 10px;
  left: 50%;
  z-index: 6;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border-radius: 6px;
  border: 1px solid var(--line-2);
  background: rgba(11, 17, 25, 0.95);
  color: var(--text-2);
  font-size: 10px;
  transform: translateX(-50%);
}

.map-snapshot-hint[data-tone="error"] {
  border-color: rgba(240, 180, 90, 0.55);
  color: #f0c07c;
}

/* ---------------------------- 地图标记 ---------------------------- */

.map-canvas :deep(.mk-wrap) {
  background: none;
  border: 0;
}

.map-canvas :deep(.mk) {
  position: absolute;
  left: 0;
  top: 0;
  display: block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #060a0f;
  background: #4a5a70;
  transform: translate(-50%, -50%);
  cursor: pointer;
  transition: box-shadow 0.16s var(--ease), width 0.16s var(--ease), height 0.16s var(--ease);
}

.map-canvas :deep(.mk i) {
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: rgba(6, 10, 15, 0.55);
}

.map-canvas :deep(.mk[data-tone="ok"]) {
  background: #2fcf92;
  box-shadow: 0 0 10px rgba(47, 207, 146, 0.45);
}

.map-canvas :deep(.mk.is-selected) {
  width: 17px;
  height: 17px;
  border-color: #d8f6ff;
  box-shadow: 0 0 0 3px rgba(111, 165, 255, 0.26);
}

.map-canvas :deep(.mk.is-linked) {
  box-shadow: 0 0 0 4px rgba(169, 139, 255, 0.3);
}

.map-canvas :deep(.mk.is-analyzing)::before {
  content: "";
  position: absolute;
  inset: -6px;
  border-radius: 50%;
  border: 1.4px solid rgba(77, 141, 255, 0.75);
  animation: mk-pulse 1.6s ease-out infinite;
}

@keyframes mk-pulse {
  0% {
    transform: scale(0.7);
    opacity: 0.9;
  }
  100% {
    transform: scale(1.5);
    opacity: 0;
  }
}

.map-canvas :deep(.mk-note) {
  position: absolute;
  top: -7px;
  right: -6px;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  background: #f0b45a;
  color: #1a1205;
  font-size: 8px;
  font-weight: 700;
  line-height: 11px;
  text-align: center;
}

.map-canvas :deep(.mk-label) {
  position: absolute;
  left: 9px;
  top: -5px;
  color: #3f4c59;
  font-size: 9px;
  font-weight: 500;
  white-space: nowrap;
  text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff;
}

.map-canvas :deep(.mk-label.is-on) {
  color: #0d7357;
}

.map-canvas :deep(.track-label) {
  position: absolute;
  color: #54606d;
  transform: translate(-50%, -50%);
  font-size: 8.5px;
  font-weight: 500;
  white-space: nowrap;
  text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff;
}

.map-canvas :deep(.track-label.is-verified) {
  color: #0a7a4a;
}

.map-canvas :deep(.track-label.is-gap) {
  color: #4e5a67;
}

.map-canvas :deep(.track-label.is-candidate),
.map-canvas :deep(.track-label.is-branch) {
  color: #8a5c00;
}

.map-canvas :deep(.region-label) {
  position: absolute;
  color: #5b3fb0;
  font-size: 9.5px;
  font-weight: 500;
  white-space: nowrap;
  text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff;
  transform: translate(-50%, -100%);
}

.map-canvas :deep(.anchor-pin) {
  position: absolute;
  display: grid;
  justify-items: center;
  transform: translate(-50%, -100%);
  cursor: pointer;
}

.map-canvas :deep(.anchor-pin em) {
  margin-top: -3px;
  padding: 0 4px;
  border-radius: 4px;
  border: 1px solid rgba(169, 139, 255, 0.6);
  background: #1a1430;
  color: #d8ccff;
  font-size: 9px;
  font-style: normal;
  line-height: 13px;
}

.map-canvas :deep(.anchor-pin.is-selected)::before {
  content: "";
  position: absolute;
  inset: -9px;
  border-radius: 50%;
  background: rgba(169, 139, 255, 0.14);
}

.map-leaflet :deep(.map-tip) {
  padding: 3px 6px;
  border: 1px solid #d5dde6;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.97);
  color: #1b2733;
  font-size: 10px;
  box-shadow: 0 2px 8px rgba(15, 26, 38, 0.16);
}

.map-leaflet :deep(.map-tip::before) {
  display: none;
}

/* 图例补色：视域等级与 TrajNexa_Map 的 FOV_STYLES 一致 */
.sw--real {
  background: #4fd1d9;
}

.sw--real-unknown {
  background: #8b9bb0;
}

.sw--fov-ident,
.sw--fov-recog,
.sw--fov-obs {
  border-radius: 2px;
  opacity: 0.85;
}

.sw--fov-ident {
  background: #ff3131;
}

.sw--fov-recog {
  background: #ffbf00;
}

.sw--fov-obs {
  background: #00b300;
}

.cam-card,
.anchor-card {
  max-height: calc(100% - 16px);
  overflow: auto;
}

</style>
