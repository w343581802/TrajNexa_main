<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import MockFrame from "./ui/MockFrame.vue";
import {
  buildings,
  cameras,
  coverageDate,
  coverageMinutes,
  hasCoverage,
  mapLabels,
  minutesToLabel,
  roads,
  snapshot,
  timeWindowPresets,
  type Camera,
} from "../data/mock";
import { actions, layers, observationList, timeWindow, visibleElements, wb } from "../store/workbench";

/* ---------------------------- 视图变换 ---------------------------- */

const view = reactive({ k: 1, tx: 0, ty: 0 });
const panning = ref(false);
let dragStart = { x: 0, y: 0, tx: 0, ty: 0 };
let moved = false;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

function onWheel(event: WheelEvent) {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  const dx = event.clientX - rect.left - rect.width / 2;
  const dy = event.clientY - rect.top - rect.height / 2;
  const next = clamp(view.k * Math.exp(-event.deltaY * 0.0015), 0.8, 4);
  const ratio = next / view.k;
  view.tx = view.tx * ratio + dx * (1 - ratio);
  view.ty = view.ty * ratio + dy * (1 - ratio);
  view.k = next;
}

function startPan(event: PointerEvent) {
  if (event.button !== 0) return;
  panning.value = true;
  moved = false;
  dragStart = { x: event.clientX, y: event.clientY, tx: view.tx, ty: view.ty };
  (event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
}

function onPan(event: PointerEvent) {
  if (!panning.value) return;
  const dx = event.clientX - dragStart.x;
  const dy = event.clientY - dragStart.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
  view.tx = clamp(dragStart.tx + dx, -420, 420);
  view.ty = clamp(dragStart.ty + dy, -260, 260);
}

function endPan() {
  panning.value = false;
}

function zoomBy(factor: number) {
  const next = clamp(view.k * factor, 0.8, 4);
  const ratio = next / view.k;
  view.tx *= ratio;
  view.ty *= ratio;
  view.k = next;
}

function resetView() {
  view.k = 1;
  view.tx = 0;
  view.ty = 0;
}

function onBackgroundClick() {
  if (moved) return;
  openPanel.value = null;
  camCardOpen.value = false;
}

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

const fovPath = (camera: Camera) => {
  const radius = 96;
  const start = camera.heading - camera.fov / 2;
  const end = camera.heading + camera.fov / 2;
  const steps = 10;
  const points: string[] = [];
  for (let i = 0; i <= steps; i += 1) {
    const angle = ((start + ((end - start) * i) / steps) * Math.PI) / 180;
    points.push(`${(camera.x + Math.sin(angle) * radius).toFixed(1)},${(camera.y - Math.cos(angle) * radius).toFixed(1)}`);
  }
  return `M${camera.x},${camera.y} L${points.join(" L")} Z`;
};

/* ---------------------------- 选中态 ---------------------------- */

const selectedCamera = computed(() => cameras.find((c) => c.id === wb.selectedCameraId) ?? null);
const camCardOpen = ref(false);
const anchorCardOpen = ref(false);

function pickCamera(camera: Camera) {
  actions.selectCamera(camera.id);
  camCardOpen.value = true;
  anchorCardOpen.value = false;
}

function pickAnchor(elementId: string) {
  actions.selectAnchor(elementId);
  anchorCardOpen.value = true;
  camCardOpen.value = false;
}

watch(
  () => wb.selectedObservationId,
  (id) => {
    const obs = observationList.value.find((o) => o.id === id);
    if (obs?.kind === "anchor" && obs.elementId) {
      anchorCardOpen.value = true;
      camCardOpen.value = false;
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

function focusAnchor() {
  resetView();
  if (anchorChips.value) {
    actions.selectAnchor(anchorChips.value.id);
    anchorCardOpen.value = true;
  }
}
</script>

<template>
  <section class="map-region">
    <div
      class="map-canvas"
      :class="{ 'is-panning': panning }"
      @pointerdown="startPan"
      @pointermove="onPan"
      @pointerup="endPan"
      @pointercancel="endPan"
      @wheel.prevent="onWheel"
      @dblclick="resetView"
      @click="onBackgroundClick"
    >
      <svg
        class="map-svg"
        :style="{ transform: `translate3d(${view.tx}px, ${view.ty}px, 0) scale(${view.k})` }"
        viewBox="0 0 1400 430"
        preserveAspectRatio="xMidYMid slice"
        role="img"
        aria-label="业务地图：Map 发布的摄像头点位、视域与业务叠加层（示意数据）"
      >
        <defs>
          <pattern id="map-grid" width="36" height="36" patternUnits="userSpaceOnUse">
            <path d="M36 0H0V36" fill="none" stroke="#0d1622" stroke-width="1" />
          </pattern>
          <radialGradient id="map-glow" cx="0.5" cy="0.45" r="0.62">
            <stop offset="0" stop-color="#0d1723" />
            <stop offset="1" stop-color="#070c12" />
          </radialGradient>
          <filter id="map-glow-blur" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="6" />
          </filter>
        </defs>

        <rect width="1400" height="430" fill="url(#map-glow)" />
        <rect width="1400" height="430" fill="url(#map-grid)" opacity="0.55" />

        <!-- 街区与建筑体块（示意，非真实测绘数据） -->
        <g>
          <rect
            v-for="(b, index) in buildings"
            :key="`b-${index}`"
            :x="b.x"
            :y="b.y"
            :width="b.w"
            :height="b.h"
            rx="1.5"
            :fill="b.tone === 0 ? '#121c28' : b.tone === 1 ? '#16212e' : '#192535'"
            stroke="#0b131c"
            stroke-width="1"
          />
        </g>

        <!-- 道路 -->
        <g fill="none" stroke-linecap="round">
          <polyline
            v-for="road in roads"
            :key="road.id"
            :points="road.points.map((p) => `${p[0]},${p[1]}`).join(' ')"
            :stroke-width="road.width"
            stroke="#111a25"
          />
        </g>
        <g fill="none" stroke-linecap="round">
          <polyline
            v-for="road in roads"
            :key="`line-${road.id}`"
            :points="road.points.map((p) => `${p[0]},${p[1]}`).join(' ')"
            stroke-width="1.2"
            stroke="#263545"
            stroke-dasharray="14 16"
            opacity="0.8"
          />
        </g>

        <!-- 地铁口 / 广场标记 -->
        <g>
          <rect x="1086" y="126" width="276" height="74" rx="6" fill="#0f1822" stroke="#1b2937" stroke-dasharray="4 5" />
          <circle cx="900" cy="206" r="9" fill="#16212e" stroke="#31445c" />
          <text x="900" y="210" text-anchor="middle" font-size="9" fill="#8fb6ff">M</text>
        </g>

        <!-- 地名与路名 -->
        <g v-if="layers.labels">
          <text
            v-for="label in mapLabels"
            :key="label.text"
            :x="label.x"
            :y="label.y"
            :font-size="label.size"
            :fill="label.kind === 'road' ? '#3d5065' : '#54687f'"
            :letter-spacing="label.kind === 'road' ? 2 : 3"
            text-anchor="middle"
            style="paint-order: stroke; stroke: #070c12; stroke-width: 3px"
          >
            {{ label.text }}
          </text>
        </g>

        <!-- 摄像头视域（FOV） -->
        <g v-if="layers.fov">
          <path
            v-for="camera in cameras"
            :key="`fov-${camera.id}`"
            :d="fovPath(camera)"
            :fill="isActive(camera) ? 'rgba(47,207,146,0.10)' : 'rgba(130,148,170,0.055)'"
            :stroke="isActive(camera) ? 'rgba(47,207,146,0.34)' : 'rgba(130,148,170,0.2)'"
            stroke-width="1"
            :stroke-dasharray="camera.fovType === '理论' ? '4 4' : undefined"
          />
        </g>

        <!-- 轨迹与盲区 -->
        <g v-if="layers.tracks" fill="none" stroke-linecap="round">
          <polyline
            v-for="track in tracks"
            :key="track.id"
            :points="track.points"
            :stroke="
              track.tone === 'verified'
                ? 'rgba(47,207,146,0.92)'
                : track.tone === 'candidate'
                  ? 'rgba(240,180,90,0.85)'
                  : track.tone === 'branch'
                    ? 'rgba(240,180,90,0.45)'
                    : 'rgba(140,158,180,0.45)'
            "
            :stroke-width="track.tone === 'verified' ? 2.6 : 1.8"
            :stroke-dasharray="track.dash || undefined"
          />
          <g font-size="8.5" text-anchor="middle" stroke="none">
            <text
              v-for="track in tracks"
              :key="`tl-${track.id}`"
              :x="track.labelAt[0]"
              :y="track.labelAt[1]"
              :fill="track.tone === 'verified' ? '#6fe4b6' : track.tone === 'gap' ? '#7d8ca1' : '#f0c07c'"
              style="paint-order: stroke; stroke: #070c12; stroke-width: 3px"
            >
              {{ track.label }}
            </text>
          </g>
        </g>

        <!-- 起始锚点 -->
        <g v-if="layers.anchors">
          <g v-for="element in regionAnchors" :key="`region-${element.id}`" @pointerdown.stop @click.stop="pickAnchor(element.id)">
            <circle
              :cx="regionAnchor.x"
              :cy="regionAnchor.y"
              :r="regionAnchor.r"
              fill="rgba(169,139,255,0.07)"
              stroke="rgba(169,139,255,0.55)"
              stroke-dasharray="5 5"
              stroke-width="1.2"
            />
            <circle :cx="regionAnchor.x" :cy="regionAnchor.y" r="3" fill="#a98bff" />
            <text
              :x="regionAnchor.x"
              :y="regionAnchor.y - regionAnchor.r - 8"
              text-anchor="middle"
              font-size="9.5"
              fill="#c3aeff"
              style="paint-order: stroke; stroke: #070c12; stroke-width: 3px"
            >
              {{ element.id }} · 区域锚点（待定位）
            </text>
          </g>
          <g
            v-for="item in cameraAnchors"
            :key="`a-${item.element.id}`"
            class="anchor-pin"
            :class="{ 'is-selected': wb.selectedAnchorId === item.element.id }"
            :transform="`translate(${item.camera.x},${item.camera.y - 8})`"
            @pointerdown.stop
            @click.stop="pickAnchor(item.element.id)"
          >
            <circle v-if="wb.selectedAnchorId === item.element.id" r="20" fill="rgba(169,139,255,0.14)" />
            <path
              d="M0 0 C-3.6 -6 -12 -11 -12 -19.5 A12 12 0 1 1 12 -19.5 C12 -11 3.6 -6 0 0 Z"
              fill="#7c5cf0"
              stroke="#c3aeff"
              stroke-width="1.2"
            />
            <circle cy="-19.5" r="4.2" fill="#f0ecff" />
            <g transform="translate(0,-44)">
              <rect x="-16" y="-10" width="32" height="15" rx="4" fill="#1a1430" stroke="rgba(169,139,255,0.6)" stroke-width="1" />
              <text y="1.6" text-anchor="middle" font-size="9" fill="#d8ccff">{{ item.element.id }}</text>
            </g>
          </g>
        </g>

        <!-- 摄像头 -->
        <g v-if="layers.cameras">
          <g
            v-for="camera in cameras"
            :key="camera.id"
            class="camera-marker"
            :class="{ 'is-selected': wb.selectedCameraId === camera.id, 'is-linked': linkedCameraId === camera.id }"
            :transform="`translate(${camera.x},${camera.y})`"
            @pointerdown.stop
            @click.stop="pickCamera(camera)"
          >
            <circle v-if="camera.analyzing" class="pulse-ring" r="9" fill="none" stroke="#4d8dff" stroke-width="1.6" />
            <circle v-if="camera.analyzing" r="14" fill="none" stroke="rgba(77,141,255,0.45)" stroke-width="1" stroke-dasharray="3 4" />
            <circle v-if="wb.selectedCameraId === camera.id" r="16" fill="none" stroke="#6fa5ff" stroke-width="1.2" stroke-dasharray="3 3" />
            <circle v-if="linkedCameraId === camera.id" r="12" fill="none" stroke="rgba(169,139,255,0.75)" stroke-width="1.4" />
            <circle
              r="6.4"
              :fill="isActive(camera) ? '#2fcf92' : '#4a5a70'"
              stroke="#060a0f"
              stroke-width="1.8"
            />
            <circle r="2" :fill="isActive(camera) ? '#052018' : '#0b1119'" opacity="0.7" />
            <g v-if="camera.notice" :transform="'translate(9,-9)'">
              <circle r="4.6" fill="#f0b45a" stroke="#060a0f" stroke-width="1" />
              <text y="2.2" text-anchor="middle" font-size="6.5" font-weight="700" fill="#1a1205">!</text>
            </g>
            <text
              v-if="layers.labels"
              x="10"
              y="3.4"
              font-size="9.2"
              :fill="isActive(camera) ? '#a8c5bb' : '#6f8095'"
              style="paint-order: stroke; stroke: #070c12; stroke-width: 3.4px"
            >
              {{ camera.name }}
            </text>
            <title>{{ camera.name }} · {{ isActive(camera) ? "当前窗内有已索引录像" : "当前窗内无已索引录像" }}</title>
          </g>
        </g>
      </svg>

      <div class="map-compass" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="26" height="26">
          <circle cx="12" cy="12" r="10.5" fill="rgba(8,13,19,0.7)" stroke="#25384c" />
          <path d="M12 5.5 14.2 12 12 10.6 9.8 12Z" fill="#8fb6ff" />
          <path d="M12 18.5 9.8 12 12 13.4l2.2-1.4Z" fill="#33465c" />
        </svg>
      </div>
    </div>

    <!-- 顶部工具条 -->
    <div class="map-toolbar popover" @click.stop>
      <div class="tb-group">
        <button class="tb-btn" :class="{ 'is-open': openPanel === 'layers' }" @click="togglePanel('layers')">
          <AppIcon name="layers" :size="14" />图层
        </button>
        <button class="tb-btn" @click="actions.openCoverage()">
          <AppIcon name="video" :size="14" />录像覆盖
        </button>
        <button class="tb-btn" @click="focusAnchor">
          <AppIcon name="target" :size="14" />定位
        </button>
        <button class="tb-btn" :class="{ 'is-open': openPanel === 'legend' }" @click="togglePanel('legend')">
          <AppIcon name="list" :size="14" />图例
        </button>
      </div>
      <span class="tb-sep" />
      <button class="tb-time" :class="{ 'is-open': openPanel === 'time' }" @click="togglePanel('time')">
        <AppIcon name="clock" :size="14" />
        <span class="num">{{ windowLabel }}</span>
        <AppIcon name="chevron-down" :size="12" />
      </button>
    </div>

    <div v-if="openPanel === 'layers'" class="map-panel map-panel--layers popover" @click.stop>
      <header class="panel-head">
        <span>图层</span>
        <span class="dim">来自 Map 快照</span>
      </header>
      <label class="layer-row"><input v-model="layers.cameras" type="checkbox" /><span>摄像头点位</span><em>{{ cameras.length }}</em></label>
      <label class="layer-row"><input v-model="layers.fov" type="checkbox" /><span>视域 FOV</span><em>{{ cameras.length }}</em></label>
      <label class="layer-row"><input v-model="layers.anchors" type="checkbox" /><span>目标起始锚点</span><em>{{ visibleElements.length }}</em></label>
      <label class="layer-row"><input v-model="layers.tracks" type="checkbox" /><span>轨迹与盲区</span><em>{{ tracks.length }}</em></label>
      <label class="layer-row"><input v-model="layers.labels" type="checkbox" /><span>地名与路名</span><em>—</em></label>
      <p class="panel-note">可用性颜色来自主系统录像索引，不写回 Map GIS。</p>
    </div>

    <div v-if="openPanel === 'legend'" class="map-panel map-panel--legend popover" @click.stop>
      <header class="panel-head"><span>图例</span></header>
      <p class="legend-title">摄像头录像可用性</p>
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
      <span class="zoom-level num">{{ Math.round(view.k * 100) }}%</span>
    </div>

    <!-- 锚点操作卡 -->
    <Transition name="slide">
      <aside v-if="anchorCardOpen && anchorChips && !wb.emptyMode" class="anchor-card popover">
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
      <aside v-if="camCardOpen && selectedCamera" class="cam-card popover">
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

    <footer class="map-status">
      <span class="mono">{{ snapshot.revision }}</span>
      <span class="sep">·</span>
      <span>{{ cameras.length }} 台摄像头</span>
      <span class="sep">·</span>
      <span class="ok">{{ activeCount }} 台当前窗内有已索引录像</span>
      <span class="grow" />
      <span class="dim">查询窗 {{ coverageDate }} {{ windowLabel }}</span>
      <span class="sep">·</span>
      <span class="dim">拖动平移 · 滚轮缩放 · 双击复位</span>
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
  cursor: grab;
  touch-action: none;
  overflow: hidden;
}

.map-canvas.is-panning {
  cursor: grabbing;
}

.map-svg {
  width: 100%;
  height: 100%;
  transition: transform 0.18s var(--ease);
  will-change: transform;
}

.map-svg text {
  font-family: var(--font);
  user-select: none;
}

.map-canvas.is-panning .map-svg {
  transition: none;
}

.camera-marker {
  cursor: pointer;
}

.camera-marker:hover text {
  fill: #e6eefb;
}

.anchor-pin {
  cursor: pointer;
}

.pulse-ring {
  transform-box: fill-box;
  transform-origin: center;
  animation: pulse-ring 2.2s var(--ease) infinite;
}

/* 工具条 */
.map-toolbar {
  position: absolute;
  top: 10px;
  left: 10px;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px;
  border-radius: 9px;
  background: rgba(10, 16, 24, 0.9);
  backdrop-filter: blur(10px);
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
  top: 48px;
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
</style>
