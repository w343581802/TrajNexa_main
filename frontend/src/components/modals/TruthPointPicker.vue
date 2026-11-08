<script setup lang="ts">
/**
 * 真值点拾取器：在卫星影像上点地物，得到独立于投影链路的地面真值。
 *
 * 为什么必须「点影像」而不是用摄像头坐标：
 * 要检验的是「像素 → 地面」这一步，而摄像头坐标/姿态正是这一步的**输入**。拿它当真值等于
 * 用 A 验证 A —— 本项目真实反例是 `direction=168` 与 `bearing_deg=355` 差 187°，用摄像头
 * 坐标做真值完全发现不了（射线永远从原点出发）。所以这里只允许卫星影像控制点
 * （`map_control_point`）/人工控制点（`manual_control_point`）/现场测量（`surveyed`、
 * `gnss_walk`），算法输出直接拒绝。
 *
 * 时刻不靠猜：录像起点由 OCR（文件名 / 画面字幕）给出，目标压在地物上的**片段内秒数**由人输入，
 * `at = 起点 + 秒数`。两个值分开存，以后换更准的时钟基准也不用重做点位。
 *
 * 产出：`POST /api/v1/spatial/camera-truth/datasets`，写进 `data/truth/`，
 * `scripts/b8_projection_metrics.py --truth` 直接消费同一份文件。
 */
import { computed, onBeforeUnmount, ref, watch, nextTick } from "vue";
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import { basemapTiles } from "../../services/basemapTiles";
import { cameraCode, loadMapSnapshot, type SnapshotCamera } from "../../services/mapSnapshot";
import { mainApi, type TruthDatasetRow } from "../../services/mainApi";
import { recordingClips, toast, ui } from "../../store/workbench";

type Picked = {
  /** `T1` / `T2` …，只在本数据集内唯一 */
  id: string;
  lon: number;
  lat: number;
  /** 目标压在该地物上的片段内秒数；`null` 表示还没填 */
  seconds: number | null;
  source: string;
  evidence: string;
};

const SOURCE_LABEL: Record<string, string> = {
  map_control_point: "卫星影像控制点",
  manual_control_point: "人工控制点",
  surveyed: "现场测量",
  gnss_walk: "GNSS 走测",
};

const cameras = ref<SnapshotCamera[]>([]);
/** Map 侧空间数据版本；改完镜头台账后靠它确认弹窗用的是不是新数据 */
const revision = ref("");
const cameraUuid = ref("");
const baseTime = ref("");
const points = ref<Picked[]>([]);
const notes = ref("");
const saving = ref(false);
const loading = ref(false);
const datasets = ref<TruthDatasetRow[]>([]);
const datasetRoot = ref("");
/** 保存成功后的路径，直接告诉用户指标脚本该传什么 */
const savedPath = ref("");

const host = ref<HTMLElement | null>(null);
let map: L.Map | null = null;
let fovLayer: L.LayerGroup | null = null;
let cameraLayer: L.LayerGroup | null = null;
let pickLayer: L.LayerGroup | null = null;
let tiles: L.TileLayer | null = null;

const camera = computed(() => cameras.value.find((row) => row.camera_uuid === cameraUuid.value) ?? null);
const withUuid = computed(() => cameras.value.filter((row) => row.camera_uuid));

/** 打开时的默认基准时间：优先用最近一次录像扫描读到的起点（OCR/文件名），不虚构 */
function defaultBaseTime(): string {
  const rows = [...recordingClips.value]
    .filter((clip) => clip.start_local)
    .sort((a, b) => String(b.start_local).localeCompare(String(a.start_local)));
  const value = rows[0]?.start_local ?? "";
  return value.slice(0, 19);
}

function distanceM(lon: number, lat: number): number {
  const target = camera.value;
  if (!target) return Number.NaN;
  const radius = 6371008.8;
  const phi1 = (target.lat * Math.PI) / 180;
  const phi2 = (lat * Math.PI) / 180;
  const dPhi = phi2 - phi1;
  const dLambda = ((lon - target.lon) * Math.PI) / 180;
  const h = Math.sin(dPhi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dLambda / 2) ** 2;
  return 2 * radius * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** 近 0–15 / 中 15–40 / 远 40+，与后端 `RANGE_BUCKETS` 同一口径 */
function rangeClass(metres: number): string {
  if (!Number.isFinite(metres)) return "unknown";
  if (metres < 15) return "near";
  if (metres < 40) return "mid";
  return "far";
}

const RANGE_TONE: Record<string, string> = { near: "近距", mid: "中距", far: "远距", unknown: "距离未知" };

function rangeLabel(metres: number): string {
  if (!Number.isFinite(metres)) return "距离未知";
  return `${metres.toFixed(1)} m · ${RANGE_TONE[rangeClass(metres)] ?? ""}`;
}

/** `2026-10-23T15:00:00` + 12.5 秒 → `2026-10-23T15:00:12`；解析不了就空串 */
function atOf(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || !baseTime.value) return "";
  const start = new Date(`${baseTime.value}Z`);
  if (Number.isNaN(start.getTime())) return "";
  const moment = new Date(start.getTime() + seconds * 1000);
  return moment.toISOString().slice(0, 19);
}

/** Leaflet 对象不进响应式（Vue 代理会破坏 Leaflet 内部状态），按点 id 另存 */
const markers = new Map<string, L.Marker>();

function drawPoint(row: Picked): void {
  if (!map || !pickLayer) return;
  const label = row.evidence.trim() || row.id;
  const marker = L.marker([row.lat, row.lon], { draggable: true, autoPan: true });
  marker.bindTooltip(`${row.id} · ${label}`, { permanent: false, direction: "top" });
  marker.on("dragend", () => {
    const position = marker.getLatLng();
    row.lat = Number(position.lat.toFixed(7));
    row.lon = Number(position.lng.toFixed(7));
  });
  marker.on("contextmenu", () => removePoint(row.id));
  marker.addTo(pickLayer);
  markers.set(row.id, marker);
}

function removePoint(id: string): void {
  const index = points.value.findIndex((row) => row.id === id);
  if (index < 0) return;
  markers.get(id)?.remove();
  markers.delete(id);
  points.value.splice(index, 1);
}

async function clearPoints(): Promise<void> {
  for (const marker of markers.values()) marker.remove();
  markers.clear();
  points.value = [];
  savedPath.value = "";
  await nextTick();
}

function onMapClick(event: L.LeafletMouseEvent): void {
  const row: Picked = {
    id: `T${points.value.length + 1}`,
    lon: Number(event.latlng.lng.toFixed(7)),
    lat: Number(event.latlng.lat.toFixed(7)),
    seconds: null,
    source: "map_control_point",
    evidence: "",
  };
  points.value.push(row);
  drawPoint(row);
}

function drawCamera(focus = true): void {
  if (!map || !cameraLayer || !fovLayer) return;
  cameraLayer.clearLayers();
  fovLayer.clearLayers();
  const target = camera.value;
  if (!target) return;
  const tone = { identification: "#529de3", recognition: "#25b99a", observation: "#dfb65b" };
  for (const level of target.fov_levels ?? []) {
    for (const ring of level.rings) {
      const latlngs = ring.map(([lon, lat]) => [lat, lon] as [number, number]);
      if (latlngs.length < 3) continue;
      L.polygon(latlngs, {
        color: tone[level.level] ?? "#25b99a",
        weight: 1.2,
        fillOpacity: 0.1,
        // Map 的视域是模型几何，不是实测覆盖：虚线提示「仅供参考，不当范围真值」
        dashArray: "5 5",
      }).addTo(fovLayer);
    }
  }
  L.circleMarker([target.lat, target.lon], {
    radius: 6, weight: 2, color: "#07111b", fillColor: "#5caaff", fillOpacity: 0.95,
  })
    .bindTooltip(cameraCode(target), { direction: "top" })
    .addTo(cameraLayer);
  // 刷新台账时不要把用户正在点选的视野拽走，只有换镜头才重新定位
  if (focus) map.setView([target.lat, target.lon], 18);
}

/** 卫星影像是人工判读依据，不做暗色调色 */
function setBase(kind: "satellite" | "map"): void {
  if (!map) return;
  if (tiles) map.removeLayer(tiles);
  tiles = basemapTiles(kind);
  tiles.addTo(map);
  tiles.bringToBack();
}

/**
 * 读 Map 镜头。
 *
 * `mapSnapshot.loadMapSnapshot()` 带模块级缓存，主地图与拾取器共用同一份；
 * Map 侧改完镜头台账后主地图会换 `spatial_revision` 重拉，拾取器若直接复用缓存就会一直显示旧坐标。
 * 所以打开弹窗、点「刷新」都强制走一次网络（force=true），并把版本号显示出来供核对。
 */
async function loadCameras(force = false, focus = false): Promise<void> {
  loading.value = true;
  try {
    const snapshot = await loadMapSnapshot(force);
    cameras.value = snapshot.cameras;
    revision.value = snapshot.spatial_revision;
    const first = withUuid.value[0];
    const stillThere = withUuid.value.some((row) => row.camera_uuid === cameraUuid.value);
    if (!stillThere && first?.camera_uuid) cameraUuid.value = first.camera_uuid;
    drawCamera(focus);
  } catch (error) {
    toast(error instanceof Error ? error.message : "Map 镜头读取失败", "warn");
  } finally {
    loading.value = false;
  }
}

async function loadDatasets(): Promise<void> {
  try {
    const payload = await mainApi.truthDatasets();
    datasets.value = payload.datasets.filter((row) => !row.template);
    datasetRoot.value = payload.root;
  } catch {
    /* 列表只是导航，取不到不影响拾取与保存 */
  }
}

/** 校验：缺秒数/缺说明/时刻算不出来都不许提交，避免写出跑不了的真值 */
function buildPoints(): { id: string; lon: number; lat: number; at: string; frame_seconds: number; source: string; range_class: string; evidence: string }[] {
  const rows = [];
  for (const row of points.value) {
    const metres = distanceM(row.lon, row.lat);
    const at = atOf(row.seconds);
    rows.push({
      id: row.id,
      lon: row.lon,
      lat: row.lat,
      at,
      frame_seconds: row.seconds as number,
      source: row.source,
      range_class: rangeClass(metres),
      evidence: row.evidence.trim() || `影像拾取 ${row.id}`,
    });
  }
  return rows;
}

function validate(): string | null {
  if (!cameraUuid.value) return "请先选择镜头";
  if (!baseTime.value) return "请先填录像起点时间（OCR 读到的开始时间）";
  if (!points.value.length) return "在影像上至少点一个地物";
  if (points.value.some((row) => row.seconds === null || !Number.isFinite(row.seconds))) {
    return "每个点都要填「目标压在该地物上的秒数」";
  }
  if (points.value.some((row) => !atOf(row.seconds))) return "录像起点时间格式不对，无法换算时刻";
  return null;
}

function payload() {
  const target = camera.value;
  return {
    camera_uuid: cameraUuid.value,
    camera_label: target ? cameraCode(target) : null,
    time_basis: "recording_start_ocr_plus_frame_seconds",
    base_time: baseTime.value,
    notes: notes.value.trim(),
    collected_via: "gis_picker",
    points: buildPoints().map((row) => ({
      ...row,
      // 后端只收 `YYYY-MM-DDTHH:MM:SS`；空串表示时刻缺失，会返回 warning 而不是静默通过
      at: row.at,
    })),
  };
}

async function save(): Promise<void> {
  const problem = validate();
  if (problem) {
    toast(problem, "warn");
    return;
  }
  if (saving.value) return;
  saving.value = true;
  try {
    const result = await mainApi.saveTruthDataset(payload());
    savedPath.value = result.path;
    const extra = result.warnings.length ? `（提示：${result.warnings.join("、")}）` : "";
    toast(`已保存真值集 ${result.dataset_id}：${result.points} 个点${extra}`, "ok");
    await loadDatasets();
  } catch (error) {
    toast(error instanceof Error ? error.message : "真值集保存失败", "warn");
  } finally {
    saving.value = false;
  }
}

/** 导出同一份 JSON：没有后端也能带走文件 */
function exportJson(): void {
  const problem = validate();
  if (problem) {
    toast(problem, "warn");
    return;
  }
  const body = {
    dataset_id: `camera-truth-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "")}`,
    ...payload(),
    collected_at: new Date().toISOString().slice(0, 19),
  };
  const blob = new Blob([JSON.stringify(body, null, 2) + "\n"], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${body.dataset_id}.json`;
  link.click();
  URL.revokeObjectURL(url);
  toast("已导出真值集 JSON，可直接传给 --truth", "ok");
}

function close(): void {
  ui.truthPickerOpen = false;
}

watch(cameraUuid, () => {
  drawCamera();
});

/** `BaseModal` 用 `v-if` 控制内容，宿主元素只在弹窗打开时存在，因此地图必须在打开后初始化。 */
function destroyMap(): void {
  map?.remove();
  map = null;
  fovLayer = null;
  cameraLayer = null;
  pickLayer = null;
  tiles = null;
  markers.clear();
}

function initMap(): void {
  if (map || !host.value) return;
  map = L.map(host.value, { zoomControl: true, minZoom: 12, maxZoom: 19 }).setView([25.2562, 110.2118], 16);
  map.attributionControl.setPrefix(false);
  setBase("satellite");
  fovLayer = L.layerGroup().addTo(map);
  cameraLayer = L.layerGroup().addTo(map);
  pickLayer = L.layerGroup().addTo(map);
  map.on("click", onMapClick);
  for (const row of points.value) drawPoint(row);
  drawCamera();
}

watch(
  () => ui.truthPickerOpen,
  async (open) => {
    if (!open) {
      destroyMap();
      return;
    }
    if (!baseTime.value) baseTime.value = defaultBaseTime();
    if (!datasetRoot.value) void loadDatasets();
    // 每次打开都强制重拉 Map 镜头：台账刚改过时缓存里的坐标是旧的；打开时按新坐标重新定位
    void loadCameras(true, true);
    await nextTick();
    initMap();
  },
);

onBeforeUnmount(destroyMap);
</script>

<template>
  <BaseModal
    :open="ui.truthPickerOpen"
    title="真值点拾取"
    subtitle="在卫星影像上点地物取坐标，填目标压在该地物上的秒数。坐标来自影像、时刻来自录像起点；投影结果不能当真值。"
    width="xl"
    @close="close"
  >
    <div class="tp">
      <div class="tp-bar">
        <label class="tp-field">
          <span>镜头</span>
          <select v-model="cameraUuid" class="input" :disabled="loading || !withUuid.length">
            <option v-for="row in withUuid" :key="row.camera_uuid ?? row.camera_id" :value="row.camera_uuid">
              {{ cameraCode(row) }}
            </option>
            <option v-if="!withUuid.length" value="">Map 没有带台账 UUID 的镜头</option>
          </select>
        </label>
        <label class="tp-field">
          <span>录像起点</span>
          <input v-model="baseTime" class="input" placeholder="2026-10-23T15:00:00" />
        </label>
        <div class="tp-field">
          <span>Map 数据</span>
          <div class="tp-rev">
            <code :title="revision">{{ revision.replace("mapdb-spatial:", "") || "—" }}</code>
            <button class="btn btn--ghost btn--sm" :disabled="loading" @click="loadCameras(true)">
              <AppIcon name="refresh" :size="12" />{{ loading ? "读取中…" : "刷新" }}
            </button>
          </div>
        </div>
        <span class="grow" />
        <label class="tp-field tp-field--inline">
          <span>底图</span>
          <button class="btn btn--ghost btn--sm" @click="setBase('satellite')">卫星</button>
          <button class="btn btn--ghost btn--sm" @click="setBase('map')">地图</button>
        </label>
      </div>

      <div class="tp-body">
        <div class="tp-map-wrap">
          <div ref="host" class="tp-map" />
          <p class="tp-hint">
            点一下落一个点（拖动可微调、右键删除）。视域虚线是 Map 模型几何，只作参照，不能当测距真值。
          </p>
        </div>

        <div class="tp-list">
          <div v-if="!points.length" class="tp-empty">
            还没有点。在左侧影像上点第一处地物（停止线、井盖、路灯基座等）。
          </div>
          <div v-for="row in points" :key="row.id" class="tp-point">
            <div class="tp-point-head">
              <b>{{ row.id }}</b>
              <em>{{ rangeLabel(distanceM(row.lon, row.lat)) }}</em>
              <span class="grow" />
              <button class="btn btn--ghost btn--sm" @click="removePoint(row.id)">
                <AppIcon name="trash" :size="12" />删除
              </button>
            </div>
            <div class="tp-point-grid">
              <label class="tp-field">
                <span>秒数</span>
                <input
                  v-model.number="row.seconds"
                  class="input"
                  type="number"
                  min="0"
                  step="0.5"
                  placeholder="目标此刻的片段秒数"
                />
              </label>
              <label class="tp-field">
                <span>时刻</span>
                <input class="input" :value="atOf(row.seconds)" readonly placeholder="起点 + 秒数" />
              </label>
            </div>
            <div class="tp-point-grid">
              <label class="tp-field">
                <span>来源</span>
                <select v-model="row.source" class="input">
                  <option v-for="(label, value) in SOURCE_LABEL" :key="value" :value="value">{{ label }}</option>
                </select>
              </label>
              <label class="tp-field">
                <span>地物 / 依据</span>
                <input v-model="row.evidence" class="input" placeholder="如：路口南侧停止线端点" />
              </label>
            </div>
            <p class="tp-coord">
              {{ row.lat.toFixed(7) }}, {{ row.lon.toFixed(7) }} · WGS84
            </p>
          </div>

          <label class="tp-field">
            <span>数据集备注</span>
            <textarea v-model="notes" class="input" rows="2" placeholder="谁取的、依据哪帧影像、有没有存疑" />
          </label>

          <div class="tp-actions">
            <button class="btn btn--primary btn--sm" :disabled="saving" @click="save">
              <AppIcon name="download" :size="12" />{{ saving ? "保存中…" : "保存到真值目录" }}
            </button>
            <button class="btn btn--ghost btn--sm" @click="exportJson">导出 JSON</button>
            <button class="btn btn--ghost btn--sm" :disabled="!points.length" @click="clearPoints">清空点</button>
          </div>
          <p v-if="savedPath" class="tp-saved">
            已写入 <code>{{ savedPath }}</code>；指标脚本用
            <code>python scripts/b8_projection_metrics.py --truth {{ savedPath }}</code>
          </p>
          <p v-if="datasets.length" class="tp-history">
            已有 {{ datasets.length }} 份真值集：
            <span v-for="row in datasets.slice(0, 4)" :key="row.dataset_id" class="tp-chip">
              {{ row.dataset_id }} · {{ row.points }} 点
            </span>
          </p>
        </div>
      </div>
    </div>
  </BaseModal>
</template>

<style scoped>
.tp { display: flex; flex-direction: column; gap: 10px; }
.tp-bar { display: flex; align-items: end; gap: 10px; flex-wrap: wrap; }
.tp-field { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-3); }
.tp-field > span { letter-spacing: .04em; }
.tp-field--inline { flex-direction: row; align-items: center; gap: 6px; }
.tp-rev { display: flex; align-items: center; gap: 6px; }
.tp-rev code { font-family: var(--mono); font-size: 10.5px; color: var(--text-3); }
.grow { flex: 1; }
.tp-body { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(300px, 1fr); gap: 12px; }
.tp-map-wrap { display: flex; flex-direction: column; gap: 6px; }
.tp-map { height: 460px; border-radius: 8px; overflow: hidden; border: 1px solid var(--line); }
.tp-hint { margin: 0; font-size: 11px; color: var(--text-3); line-height: 1.5; }
.tp-list { display: flex; flex-direction: column; gap: 10px; max-height: 520px; overflow: auto; padding-right: 4px; }
.tp-empty { font-size: 12px; color: var(--text-3); line-height: 1.6; padding: 10px; border: 1px dashed var(--line); border-radius: 8px; }
.tp-point { display: flex; flex-direction: column; gap: 6px; padding: 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg-2); }
.tp-point-head { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.tp-point-head b { color: var(--text-0); }
.tp-point-head em { font-style: normal; color: var(--text-3); }
.tp-point-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tp-coord { margin: 0; font-family: var(--mono); font-size: 10.5px; color: var(--text-3); }
.tp-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.tp-saved { margin: 0; font-size: 11px; color: var(--text-3); line-height: 1.6; }
.tp-saved code { font-family: var(--mono); font-size: 10.5px; color: var(--accent); }
.tp-history { margin: 0; font-size: 11px; color: var(--text-3); display: flex; gap: 6px; flex-wrap: wrap; }
.tp-chip { padding: 1px 6px; border: 1px solid var(--line); border-radius: 999px; font-family: var(--mono); font-size: 10.5px; }
@media (max-width: 1080px) { .tp-body { grid-template-columns: 1fr; } .tp-map { height: 340px; } }
</style>