<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

type Geo = { type: "Polygon" | "MultiPolygon"; coordinates: number[][][] | number[][][][] };
type Level = { level: string; geometry: Geo; occlusion_applied: boolean };
type Camera = {
  map_camera_id: string; camera_uuid: string | null; display_name: string | null;
  location: { lon: number; lat: number };
  bearing_deg: number | null; tilt_deg: number | null; horizontal_fov_deg: number | null;
  pose_quality: string; map_registry_link: string; fov_levels: Level[];
};
type Result = { status: string; spatial_revision: string; counts: Record<string, number>; cameras: Camera[] };

const data = ref<Result | null>(null);
const error = ref("");
const loading = ref(false);
const selectedId = ref<string | null>(null);
const showFov = ref(true);
const showNames = ref(false);
let controller: AbortController | null = null;
async function reload() {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  error.value = ""; loading.value = true;
  try {
    const response = await fetch("/api/v1/spatial/cameras", { signal: current.signal, cache: "no-store" });
    const body = await response.json();
    if (!response.ok || body?.status !== "available") throw new Error(body?.error?.code || "map_unavailable");
    if (!Array.isArray(body.cameras)) throw new Error("invalid_map_response");
    data.value = body as Result;
    selectedId.value = null;
  } catch (e) {
    if (current.signal.aborted) return;
    data.value = null; error.value = e instanceof Error ? e.message : "request_failed";
  } finally { if (controller === current) loading.value = false; }
}
onMounted(reload);
onUnmounted(() => controller?.abort());
const selected = computed(() => data.value?.cameras.find((c) => c.map_camera_id === selectedId.value) ?? null);
const WIDTH = 1300, HEIGHT = 570;
const project = computed(() => {
  const cams = data.value?.cameras ?? [];
  if (!cams.length) return (_lon: number, _lat: number): [number, number] => [WIDTH/2,HEIGHT/2];
  const minLon = Math.min(...cams.map((c) => c.location.lon));
  const maxLon = Math.max(...cams.map((c) => c.location.lon));
  const minLat = Math.min(...cams.map((c) => c.location.lat));
  const maxLat = Math.max(...cams.map((c) => c.location.lat));
  const baseLon = (minLon + maxLon)/2, baseLat = (minLat + maxLat)/2;
  const cosLat = Math.max(0.1, Math.cos(baseLat*Math.PI/180));
  const scale = Math.min((WIDTH-100)/(Math.max(0.002,(maxLon-minLon)*cosLat)),
                         (HEIGHT-100)/(Math.max(0.002,maxLat-minLat)));
  return (lon: number, lat: number): [number, number] =>
    [WIDTH/2+(lon-baseLon)*cosLat*scale, HEIGHT/2-(lat-baseLat)*scale];
});
function point(c: Camera) { return project.value(c.location.lon,c.location.lat); }
function path(geometry: Geo): string {
  const polys = geometry.type === "Polygon" ? [geometry.coordinates as number[][][]] :
    geometry.coordinates as number[][][][];
  return polys.map((poly) => poly.map((ring) =>
    ring.map((xy,i) => {
      const p = project.value(xy[0],xy[1]);
      return (i ? "L" : "M")+p[0].toFixed(1)+" "+p[1].toFixed(1);
    }).join(" ")+" Z").join(" ")).join(" ");
}
const fovLayers = computed(() => (data.value?.cameras ?? []).flatMap((c) =>
  c.fov_levels.map((f) => ({
    key: String(c.map_camera_id)+"-"+f.level,
    level: f.level,
    occlusion: f.occlusion_applied,
    svgPath: path(f.geometry),
  }))
));
function angle(v: number | null) { return v === null ? "未知" : String(v)+"°"; }
</script>

<template>
  <section class="m2b">
    <header>
      <div><strong>Map 实时空间数据 · 只读预览</strong>
        <small>WGS84 等比例投影 · 暂无街道/建筑底图 · 不使用示意镜头</small></div>
      <div class="controls">
        <label><input v-model="showFov" type="checkbox" />FOV</label>
        <label><input v-model="showNames" type="checkbox" />名称</label>
        <button :disabled="loading" type="button" @click="reload">{{ loading ? "读取中…" : "刷新" }}</button>
      </div>
    </header>
    <p v-if="error" class="message">地图数据库不可用：{{ error }}。请检查主系统数据库只读账号权限。</p>
    <p v-else-if="!data" class="message">正在读取真实空间数据……</p>
    <p v-else-if="!data.cameras.length" class="message">数据库暂时没有可显示的镜头点位。</p>
    <div v-else class="main">
      <svg :viewBox="'0 0 '+WIDTH+' '+HEIGHT" class="plot" role="img"
        aria-label="按地理坐标绘制的摄像头点位和 Map 计算视域，非测绘底图">
        <defs>
          <pattern id="map-grid-m2b" width="38" height="38" patternUnits="userSpaceOnUse">
            <path d="M38 0H0V38" fill="none" stroke="#263b50" stroke-width=".7" />
          </pattern>
        </defs>
        <rect :width="WIDTH" :height="HEIGHT" fill="#0b1522" />
        <rect :width="WIDTH" :height="HEIGHT" fill="url(#map-grid-m2b)" />
        <g v-if="showFov">
          <path v-for="item in fovLayers" :key="item.key" :d="item.svgPath"
            fill-rule="evenodd"
            :fill="item.level === 'identification' ? '#4484ce30' : item.level === 'recognition' ? '#32bfa825' : '#e6b45020'"
            :stroke="item.level === 'identification' ? '#4484ce' : item.level === 'recognition' ? '#32bfa8' : '#e6b450'"
            :stroke-dasharray="item.occlusion ? undefined : '4 5'" stroke-width="1.2" />
        </g>
        <g v-for="cam in data.cameras" :key="cam.map_camera_id"
          :transform="'translate('+point(cam)[0]+','+point(cam)[1]+')'"
          class="camera" @click="selectedId = cam.map_camera_id">
          <circle :r="selectedId === cam.map_camera_id ? 9 : 6"
            :fill="cam.camera_uuid ? '#55b7ff' : '#e7ae5e'" stroke="#081323" stroke-width="2" />
          <path v-if="cam.bearing_deg !== null" d="M0 -12L-4 -6L4 -6Z" fill="#f3f9ff"
            :transform="'rotate('+cam.bearing_deg+')'" />
          <title>{{ cam.display_name || '未登记名称' }} · Map #{{ cam.map_camera_id }}</title>
          <text v-if="showNames" x="10" y="3" font-size="10" fill="#e4f1ff" stroke="#081323"
            stroke-width="2" paint-order="stroke">{{ cam.display_name || '地图设备 '+cam.map_camera_id }}</text>
        </g>
      </svg>
      <aside>
        <p><b>{{ data.counts.displayable_points }}</b> 个点位 · <b>{{ data.counts.with_fov }}</b> 个有 FOV</p>
        <p>台账关联：{{ data.counts.registry_linked }} · 未关联：{{ data.counts.unlinked_map_cameras }}</p>
        <p class="dim">版本：{{ data.spatial_revision }}</p>
        <div v-if="selected" class="selection">
          <strong>{{ selected.display_name || "未登记名称" }}</strong>
          <p>地图 ID：{{ selected.map_camera_id }}</p>
          <p>稳定 UUID：{{ selected.camera_uuid || "未关联" }}</p>
          <p>经纬度：{{ selected.location.lon }}，{{ selected.location.lat }}</p>
          <p>朝向：{{ angle(selected.bearing_deg) }} / 俯角：{{ angle(selected.tilt_deg) }}</p>
          <p>水平 FOV 参数：{{ angle(selected.horizontal_fov_deg) }}</p>
          <p>台账位姿质量：{{ selected.pose_quality }}</p>
          <p>视域等级：{{ selected.fov_levels.map((f) => f.level).join(' / ') || '无' }}</p>
        </div>
        <p v-else class="dim">点击摄像头点位查看元数据。蓝色代表有关联 UUID，橙色代表仅存在 GIS 点位。</p>
        <p class="warn">注意：FOV 是 Map 的模型计算几何，不能证明真实画面覆盖、录像可用性或对人员的辨识能力。</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.m2b{display:flex;flex-direction:column;min-height:0;min-width:0;background:#0b1522;color:#dbe8f6;border:1px solid #26394d}
header{min-height:53px;display:flex;align-items:center;justify-content:space-between;gap:14px;padding:10px 14px;border-bottom:1px solid #26394d}
strong{font-size:12px}small{display:block;color:#9ab1c7;font-size:10px;margin-top:4px}
.controls{display:flex;align-items:center;gap:12px;font-size:11px}
.controls label{display:flex;align-items:center;gap:5px}
.controls button{color:#d0e9ff;background:#123047;border:1px solid #426887;border-radius:5px;padding:6px}
.controls button:disabled{opacity:.5}
.message{padding:28px;color:#efc17b;font-size:12px}
.main{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 260px}
.plot{width:100%;height:100%;min-height:0}
.camera{cursor:pointer}
aside{padding:13px;border-left:1px solid #293e54;font-size:11px;line-height:1.55;overflow:auto;overflow-wrap:anywhere}
aside p{margin:5px 0 10px}.dim{color:#9ab1c7}.warn{border-top:1px solid #35495e;padding-top:11px;color:#f0c179}
.selection{padding:10px;border:1px solid #3c607c;border-radius:7px;margin:12px 0}
@media(max-width:850px){.main{grid-template-columns:1fr}aside{display:none}}
</style>
