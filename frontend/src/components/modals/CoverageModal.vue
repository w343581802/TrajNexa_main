<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import StatusChip from "../ui/StatusChip.vue";
import {
  cameras,
  coverageDate,
  coverageSummary,
  minutesToLabel,
  type Camera,
  type CoverageInterval,
} from "../../data/mock";
import { toast, ui, wb } from "../../store/workbench";

const query = ref("");
const filter = ref<"all" | "with" | "issues">("all");
const date = ref(coverageDate);

const windowState = reactive({ start: 0, span: 1440, label: "全天" });

const rangeOptions = [
  { id: "day", label: "全天 00:00–24:00", start: 0, span: 1440 },
  { id: "night", label: "当夜 18:00–24:00", start: 1080, span: 360 },
  { id: "evening", label: "20:00–22:00", start: 1200, span: 120 },
  { id: "hour", label: "20:00–21:00", start: 1200, span: 60 },
];

function setRange(id: string) {
  const option = rangeOptions.find((item) => item.id === id);
  if (!option) return;
  windowState.start = option.start;
  windowState.span = option.span;
  windowState.label = option.label;
}

function shiftWindow(direction: -1 | 1) {
  const step = windowState.span / 2;
  windowState.start = Math.min(1440 - windowState.span, Math.max(0, windowState.start + direction * step));
  windowState.label = "自定义区间";
}

const pct = (minutes: number) => ((minutes - windowState.start) / windowState.span) * 100;

const ticks = computed(() => {
  const step = windowState.span <= 60 ? 10 : windowState.span <= 120 ? 20 : windowState.span <= 360 ? 30 : 120;
  const list: number[] = [];
  for (let m = windowState.start; m <= windowState.start + windowState.span; m += step) list.push(m);
  return list;
});

const tickLabel = (minutes: number) => (minutes >= 1440 ? "24:00" : minutesToLabel(minutes));

const tableWidth = computed(() => {
  const factor = windowState.span >= 1440 ? 1 : windowState.span <= 60 ? 6 : windowState.span <= 120 ? 4 : 3;
  return `${factor * 100}%`;
});

const hasIndexed = (camera: Camera) => camera.intervals.some((i) => i.status === "indexed");

const filteredCameras = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  return cameras.filter((camera) => {
    if (keyword && !`${camera.name} ${camera.id} ${camera.site} ${camera.uuid}`.toLowerCase().includes(keyword)) return false;
    if (filter.value === "with") return hasIndexed(camera);
    if (filter.value === "issues") return camera.intervals.some((i) => i.status === "error" || i.status === "unknown");
    return true;
  });
});

const selected = ref<{ cameraId: string; interval: CoverageInterval } | null>(null);

function selectInterval(camera: Camera, interval: CoverageInterval) {
  selected.value = { cameraId: camera.id, interval };
  wb.selectedCameraId = camera.id;
  wb.selectedIntervalId = interval.id;
}

function isSelected(camera: Camera, interval: CoverageInterval) {
  return selected.value?.cameraId === camera.id && selected.value.interval.id === interval.id;
}

function barStyle(interval: CoverageInterval) {
  const start = Math.max(interval.s, windowState.start);
  const end = Math.min(interval.e, windowState.start + windowState.span);
  return { left: `${pct(start)}%`, width: `${Math.max(0.4, pct(end) - pct(start))}%` };
}

const intervalStatusText = (status: CoverageInterval["status"]) =>
  status === "indexed" ? "已索引录像" : status === "scanning" ? "扫描中（未知）" : status === "unknown" ? "时间未知" : "读取 / 解析异常";

const selectedCamera = computed(() => cameras.find((c) => c.id === selected.value?.cameraId) ?? null);

function locateOnMap() {
  if (!selected.value) return;
  wb.selectedCameraId = selected.value.cameraId;
  ui.coverageOpen = false;
  toast("已在地图定位该摄像头", "ok");
}

function refresh() {
  toast("已触发增量索引：只重算受影响摄像头与时间段（演示）", "ok");
}
</script>

<template>
  <BaseModal
    :open="ui.coverageOpen"
    title="摄像头录像覆盖时间轴"
    subtitle="绿色区段严格按录像起止绘制；灰色只表示当前目录、已完成索引范围内未发现录像，不代表摄像头离线。"
    width="xl"
    height="88vh"
    @close="ui.coverageOpen = false"
  >
    <div class="cov">
      <div class="cov-summary">
        <div class="cov-metric"><span>摄像头</span><b class="num">{{ coverageSummary.cameras }}</b></div>
        <div class="cov-metric"><span>当前窗内有录像</span><b class="num ok">{{ coverageSummary.withRecording }}</b></div>
        <div class="cov-metric"><span>未发现录像</span><b class="num">{{ coverageSummary.noRecording }}</b></div>
        <div class="cov-metric"><span>映射异常</span><b class="num warn">{{ coverageSummary.mappingIssues }}</b></div>
        <div class="cov-metric"><span>时间未知文件</span><b class="num warn">{{ coverageSummary.unknownFiles }}</b></div>
        <div class="cov-metric"><span>上次索引</span><b class="num">{{ coverageSummary.lastIndex }}</b></div>
        <span class="grow" />
        <StatusChip label="增量刷新中 · 部分可用" tone="warn" dot />
      </div>

      <div class="cov-toolbar">
        <label class="tb-field">
          <span>日期</span>
          <input v-model="date" class="input input--date num" type="date" />
        </label>
        <label class="tb-field">
          <span>范围</span>
          <select class="select select--sm" :value="rangeOptions.find((r) => r.span === windowState.span && r.start === windowState.start)?.id ?? 'custom'" @change="setRange(($event.target as HTMLSelectElement).value)">
            <option v-for="option in rangeOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
            <option value="custom" disabled>自定义区间</option>
          </select>
        </label>

        <div class="cov-nav">
          <button class="icon-btn" aria-label="向前平移时间窗" title="向前平移" @click="shiftWindow(-1)"><AppIcon name="chevron-left" :size="14" /></button>
          <span class="cov-nav-label num">{{ tickLabel(windowState.start) }} – {{ tickLabel(Math.min(1440, windowState.start + windowState.span)) }}</span>
          <button class="icon-btn" aria-label="向后平移时间窗" title="向后平移" @click="shiftWindow(1)"><AppIcon name="chevron-right" :size="14" /></button>
        </div>

        <div class="cov-search">
          <AppIcon name="search" :size="13" />
          <input v-model="query" type="search" placeholder="搜索摄像头名称 / 编号" aria-label="搜索摄像头" />
        </div>

        <div class="cov-seg">
          <button v-for="item in ([
            { id: 'all', label: '全部' },
            { id: 'with', label: '有录像' },
            { id: 'issues', label: '异常' }
          ] as const)" :key="item.id" :class="{ 'is-on': filter === item.id }" @click="filter = item.id">
            {{ item.label }}
          </button>
        </div>

        <button class="btn btn--sm btn--ghost cov-refresh" @click="refresh">
          <AppIcon name="refresh" :size="12" />增量刷新
        </button>
      </div>

      <div class="cov-table-wrap">
        <div class="cov-table" :style="{ width: tableWidth }">
          <div class="cov-head-row">
            <div class="cov-head-cell cov-sticky">摄像头</div>
            <div class="cov-axis">
              <span
                v-for="(tick, index) in ticks"
                :key="`t-${tick}`"
                class="cov-tick"
                :class="{ 'is-end': index === ticks.length - 1 }"
                :style="{ left: `${pct(tick)}%` }"
              >{{ tickLabel(tick) }}</span>
            </div>
          </div>

          <div
            v-for="camera in filteredCameras"
            :key="camera.id"
            class="cov-row"
            :class="{ 'is-selected': wb.selectedCameraId === camera.id }"
          >
            <button class="cov-cam cov-sticky" @click="wb.selectedCameraId = camera.id">
              <i class="cov-dot" :data-state="camera.analyzing ? 'busy' : hasIndexed(camera) ? 'ok' : 'idle'" />
              <span class="cov-cam-text">
                <strong>{{ camera.name }}</strong>
                <span class="mono">{{ camera.id }} · {{ camera.channel }}</span>
              </span>
              <span v-if="camera.notice" class="cov-alert" title="文件映射 / 时间解析异常，需人工核查">!</span>
              <span class="cov-cam-count num">{{ camera.intervals.filter((i) => i.status === "indexed").length }}</span>
            </button>

            <div class="cov-track">
              <span v-for="tick in ticks" :key="`g-${camera.id}-${tick}`" class="cov-grid-line" :style="{ left: `${pct(tick)}%` }" />
              <button
                v-for="interval in camera.intervals"
                :key="interval.id"
                class="cov-bar"
                :class="[`bar--${interval.status}`, { 'is-selected': isSelected(camera, interval) }]"
                :style="barStyle(interval)"
                :title="`${intervalStatusText(interval.status)} · ${minutesToLabel(interval.s)}–${minutesToLabel(interval.e)}`"
                @click.stop="selectInterval(camera, interval)"
              >
                <span v-if="pct(interval.e) - pct(interval.s) > 6">{{ interval.files.length }} 文件</span>
              </button>
            </div>
          </div>

          <div v-if="!filteredCameras.length" class="cov-empty">没有匹配的摄像头或筛选条件</div>
        </div>
      </div>

      <div class="cov-detail">
        <template v-if="selected && selectedCamera">
          <div class="detail-head">
            <span class="mono chip" data-tone="info">{{ selected.cameraId }}</span>
            <strong>{{ selectedCamera.name }}</strong>
            <span class="num">{{ minutesToLabel(selected.interval.s) }} – {{ minutesToLabel(selected.interval.e) }}</span>
            <span class="dim">时长 {{ selected.interval.e - selected.interval.s }} 分钟 · {{ selected.interval.files.length }} 个文件 · {{ intervalStatusText(selected.interval.status) }}</span>
          </div>
          <div class="detail-files scroll-x">
            <span v-for="file in selected.interval.files" :key="file" class="detail-file mono">{{ file }}</span>
            <span v-if="!selected.interval.files.length" class="dim">{{ selected.interval.note ?? "该区间尚无已索引文件" }}</span>
          </div>
          <div class="detail-actions">
            <button class="btn btn--sm btn--outline" @click="toast('按需预览需要后端授权片段接口（演示）', 'info')">
              <AppIcon name="play" :size="12" />打开按需预览
            </button>
            <button class="btn btn--sm btn--primary" @click="locateOnMap">
              <AppIcon name="target" :size="12" />定位到地图
            </button>
          </div>
        </template>
        <p v-else class="detail-empty dim">
          点击绿色区段查看文件、实际起止时间与读取状态。灰色区段不能凭空播放；未知 / 扫描中区间使用斜纹标记，不假装“确定无录像”。
        </p>
      </div>

      <div class="cov-legend">
        <span><i class="lg lg--ok" />绿色：已索引录像（可并集叠加多个文件）</span>
        <span><i class="lg lg--idle" />灰色：已完成索引范围内未发现录像</span>
        <span><i class="lg lg--unknown" />斜纹：时间未知 / 扫描中</span>
        <span><i class="lg lg--error" />红：文件读取或解析异常</span>
        <span class="grow" />
        <span class="dim">单行与地图摄像头一一对应，行高严格对齐</span>
      </div>
    </div>
  </BaseModal>
</template>

<style scoped>
.cov {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  padding: 10px 14px 12px;
  gap: 8px;
}

.cov-summary {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 8px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background: var(--bg-2);
  flex: none;
}

.cov-metric {
  display: grid;
  gap: 1px;
}

.cov-metric span {
  font-size: 9px;
  color: var(--text-3);
  white-space: nowrap;
}

.cov-metric b {
  font-size: 15px;
  font-weight: 650;
  color: var(--text-0);
  line-height: 1.1;
}

.cov-metric b.ok {
  color: var(--ok);
}

.cov-metric b.warn {
  color: var(--warn);
}

.grow {
  flex: 1;
}

.cov-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
  flex-wrap: wrap;
}

.tb-field {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10px;
  color: var(--text-3);
}

.input--date {
  width: 130px;
  height: 26px;
  font-size: 11px;
  padding: 0 7px;
}

.select--sm {
  height: 26px;
  font-size: 10.5px;
  width: auto;
}

.cov-nav {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg-2);
}

.cov-nav .icon-btn {
  width: 20px;
  height: 20px;
}

.cov-nav-label {
  font-size: 10px;
  color: var(--text-1);
  padding: 0 4px;
  white-space: nowrap;
}

.cov-search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid var(--line-2);
  background: var(--bg-input);
  color: var(--text-3);
  width: 210px;
}

.cov-search input {
  border: 0;
  background: transparent;
  outline: none;
  font-size: 10.5px;
  color: var(--text-1);
  width: 100%;
}

.cov-search input::placeholder {
  color: var(--text-4);
}

.cov-seg {
  display: flex;
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--bg-2);
}

.cov-seg button {
  height: 20px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 10px;
  color: var(--text-2);
}

.cov-seg button.is-on {
  background: var(--accent-soft);
  color: var(--accent-hi);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.cov-refresh {
  margin-left: auto;
}

.cov-table-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg-0);
  scrollbar-width: thin;
  scrollbar-color: var(--line-3) transparent;
}

.cov-table {
  position: relative;
  min-width: 100%;
}

.cov-head-row {
  position: sticky;
  top: 0;
  z-index: 5;
  display: grid;
  grid-template-columns: 236px minmax(600px, 1fr);
  height: 28px;
  background: #0b121b;
  border-bottom: 1px solid var(--line-2);
}

.cov-head-cell {
  display: flex;
  align-items: center;
  padding-left: 12px;
  font-size: 9.5px;
  letter-spacing: 0.08em;
  color: var(--text-3);
}

.cov-sticky {
  position: sticky;
  left: 0;
  z-index: 4;
  background: #0b121b;
  border-right: 1px solid var(--line-2);
}

.cov-axis {
  position: relative;
}

.cov-tick {
  position: absolute;
  top: 50%;
  transform: translate(-50%, -50%);
  font-size: 9px;
  color: var(--text-4);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.cov-tick:first-child {
  transform: translate(0, -50%);
}

.cov-tick.is-end {
  transform: translate(-100%, -50%);
}

.cov-row {
  display: grid;
  grid-template-columns: 236px minmax(600px, 1fr);
  height: 30px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.035);
}

.cov-row.is-selected {
  background: rgba(77, 141, 255, 0.05);
}

.cov-cam {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 8px 0 12px;
  text-align: left;
  min-width: 0;
}

.cov-cam:hover {
  background: rgba(255, 255, 255, 0.03);
}

.cov-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #4a5a70;
  flex: none;
}

.cov-dot[data-state="ok"] {
  background: var(--ok);
  box-shadow: 0 0 6px rgba(47, 207, 146, 0.55);
}

.cov-dot[data-state="busy"] {
  background: var(--accent);
  border: 1.5px solid var(--accent-hi);
  animation: blink 1.8s infinite;
}

.cov-cam-text {
  display: grid;
  line-height: 1.2;
  min-width: 0;
}

.cov-cam-text strong {
  font-size: 10.5px;
  font-weight: 500;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cov-cam-text span {
  font-size: 8.5px;
  color: var(--text-4);
}

.cov-alert {
  display: grid;
  place-items: center;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--warn);
  color: #1a1205;
  font-size: 8px;
  font-weight: 700;
  flex: none;
}

.cov-cam-count {
  margin-left: auto;
  font-size: 9px;
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.04);
  border-radius: 99px;
  padding: 0 5px;
  line-height: 14px;
}

.cov-track {
  position: relative;
  background: #080d13;
}

.cov-row:nth-child(even) .cov-track {
  background: #090f16;
}

.cov-grid-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: rgba(255, 255, 255, 0.045);
}

.cov-bar {
  position: absolute;
  top: 8px;
  height: 14px;
  border-radius: 3px;
  border: 1px solid rgba(47, 207, 146, 0.4);
  background: linear-gradient(180deg, rgba(47, 207, 146, 0.55), rgba(47, 207, 146, 0.3));
  color: #d8fff0;
  font-size: 8px;
  line-height: 12px;
  padding: 0 3px;
  overflow: hidden;
  white-space: nowrap;
  cursor: pointer;
  transition: filter var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}

.cov-bar:hover {
  filter: brightness(1.25);
}

.cov-bar.is-selected {
  border-color: #8fffd2;
  box-shadow: 0 0 0 2px rgba(47, 207, 146, 0.28), 0 0 10px rgba(47, 207, 146, 0.35);
}

.cov-bar.bar--unknown {
  border-color: rgba(240, 180, 90, 0.45);
  background: repeating-linear-gradient(135deg, rgba(240, 180, 90, 0.32) 0 4px, rgba(240, 180, 90, 0.1) 4px 8px);
  color: #f6d9a8;
}

.cov-bar.bar--scanning {
  border-color: rgba(130, 148, 170, 0.4);
  background: repeating-linear-gradient(135deg, rgba(130, 148, 170, 0.28) 0 4px, rgba(130, 148, 170, 0.08) 4px 8px);
  color: var(--text-2);
}

.cov-bar.bar--error {
  border-color: rgba(242, 112, 106, 0.55);
  background: linear-gradient(180deg, rgba(242, 112, 106, 0.55), rgba(242, 112, 106, 0.28));
  color: #ffd5d2;
}

.cov-empty {
  padding: 30px;
  text-align: center;
  font-size: 11px;
  color: var(--text-3);
}

.cov-detail {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 42px;
  padding: 6px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background: var(--bg-2);
  flex: none;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: none;
}

.detail-head strong {
  font-size: 11px;
  color: var(--text-0);
  font-weight: 600;
}

.detail-head .num {
  font-size: 10.5px;
  color: var(--text-1);
}

.detail-head .dim {
  font-size: 9.5px;
}

.detail-files {
  display: flex;
  gap: 6px;
  min-width: 0;
  flex: 1;
  overflow-x: auto;
}

.detail-file {
  font-size: 9px;
  color: var(--text-2);
  padding: 2px 6px;
  border-radius: 4px;
  border: 1px solid var(--line);
  background: var(--bg-input);
  white-space: nowrap;
}

.detail-actions {
  display: flex;
  gap: 5px;
  flex: none;
}

.detail-empty {
  font-size: 10px;
}

.cov-legend {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 9.5px;
  color: var(--text-3);
  flex: none;
  padding: 0 2px;
  flex-wrap: wrap;
}

.cov-legend span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.lg {
  width: 14px;
  height: 8px;
  border-radius: 2px;
  flex: none;
}

.lg--ok {
  background: rgba(47, 207, 146, 0.45);
  border: 1px solid rgba(47, 207, 146, 0.6);
}

.lg--idle {
  background: #141d28;
  border: 1px solid var(--line-2);
}

.lg--unknown {
  background: repeating-linear-gradient(135deg, rgba(240, 180, 90, 0.4) 0 3px, rgba(240, 180, 90, 0.1) 3px 6px);
  border: 1px solid rgba(240, 180, 90, 0.35);
}

.lg--error {
  background: rgba(242, 112, 106, 0.45);
  border: 1px solid rgba(242, 112, 106, 0.6);
}

@media (max-width: 1200px) {
  .cov-summary {
    gap: 12px;
  }

  .cov-search {
    width: 150px;
  }
}
</style>
