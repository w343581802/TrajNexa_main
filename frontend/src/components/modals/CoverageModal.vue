<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import StatusChip from "../ui/StatusChip.vue";
import RecordingClipNameModal from "./RecordingClipNameModal.vue";
import type { RecordingClip } from "../../services/mainApi";
import { minutesToLabel } from "../../data/mock";
import { actions, recordingClips, recordingSummary, toast, ui, wb } from "../../store/workbench";

type CovStatus = "indexed" | "unknown" | "scanning" | "error";
type CovInterval = { id: string; s: number; e: number; status: CovStatus; files: string[]; note?: string };
type CovCamera = { id: string; name: string; channel: string; analyzing: boolean; notice: boolean; intervals: CovInterval[] };

const query = ref("");
const filter = ref<"all" | "with" | "issues">("all");
/** 空串 = 不按日期过滤（扫描结果里可能跨天） */
const date = ref("");

const windowState = reactive({ start: 0, span: 1440 });
/** 默认自动贴合数据范围；用户选固定区间或平移后改为 custom */
const rangeId = ref<"auto" | "day" | "night" | "evening" | "hour" | "custom">("auto");

const rangeOptions = [
  { id: "auto", label: "自动（贴合录像范围）", start: 0, span: 0 },
  { id: "day", label: "全天 00:00–24:00", start: 0, span: 1440 },
  { id: "night", label: "当夜 18:00–24:00", start: 1080, span: 360 },
  { id: "evening", label: "20:00–22:00", start: 1200, span: 120 },
  { id: "hour", label: "20:00–21:00", start: 1200, span: 60 },
];

function setRange(id: string) {
  if (id === "auto") {
    rangeId.value = "auto";
    return;
  }
  const option = rangeOptions.find((item) => item.id === id);
  if (!option) return;
  windowState.start = option.start;
  windowState.span = option.span;
  rangeId.value = option.id as typeof rangeId.value;
}

function shiftWindow(direction: -1 | 1) {
  const step = viewSpan.value / 2;
  windowState.start = Math.min(Math.max(0, 1440 - viewSpan.value), Math.max(0, viewStart.value + direction * step));
  windowState.span = viewSpan.value;
  rangeId.value = "custom";
}

const viewStart = computed(() => (rangeId.value === "auto" ? autoWindow.value.start : windowState.start));
const viewSpan = computed(() => (rangeId.value === "auto" ? autoWindow.value.span : windowState.span));

const pct = (minutes: number) => ((minutes - viewStart.value) / viewSpan.value) * 100;

/** 自动区间：贴合实际录像的起止，留一点边距，并对齐到易读刻度 */
const autoWindow = computed(() => {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (const row of rows.value) {
    for (const interval of row.intervals) {
      min = Math.min(min, interval.s);
      max = Math.max(max, interval.e);
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) {
    return { start: 0, span: 1440 };
  }
  const pad = Math.max(2, (max - min) * 0.08);
  let start = Math.max(0, min - pad);
  let end = Math.min(1440, max + pad);
  if (end - start < 1) {
    start = Math.max(0, start - 1);
    end = Math.min(1440, end + 1);
  }
  const step = pickStep(end - start);
  start = Math.max(0, Math.floor(start / step) * step);
  end = Math.min(1440, Math.ceil(end / step) * step);
  if (end - start < step) end = Math.min(1440, start + step);
  if (start >= 1440) start = Math.max(0, 1440 - step);
  return { start, span: Math.max(1, end - start) };
});

function pickStep(span: number) {
  for (const step of [1, 2, 5, 10, 15, 30, 60, 120, 180, 240, 360]) {
    if (span / step <= 8) return step;
  }
  return 360;
}

const ticks = computed(() => {
  const span = viewSpan.value;
  const step = span <= 6 ? 1 : span <= 12 ? 2 : span <= 30 ? 5 : span <= 60 ? 10 : span <= 120 ? 20 : span <= 360 ? 30 : 120;
  const start = viewStart.value;
  const end = start + span;
  const list: number[] = [];
  for (let m = start; m <= end; m += step) list.push(m);
  if (list[list.length - 1] < end) list.push(end);
  return list;
});

const tickLabel = (minutes: number) => (minutes >= 1440 ? "24:00" : minutesToLabel(minutes));

const tableWidth = computed(() => {
  if (rangeId.value === "auto") return "100%";
  const factor = windowState.span >= 1440 ? 1 : windowState.span <= 60 ? 6 : windowState.span <= 120 ? 4 : 3;
  return `${factor * 100}%`;
});

/** 当前时间窗对应的预设 id（自定义区间时为 custom） */
const currentRangeId = computed(() => rangeId.value);

/** 从 `start_local` 取当天的分钟数；只读到开始时间，结尾时间不推断 */
function startMinutes(iso: string | null): number | null {
  if (!iso) return null;
  const match = /T(\d{2}):(\d{2}):(\d{2})/.exec(iso);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]) + Number(match[3]) / 60;
}

const hasIndexed = (camera: CovCamera) => camera.intervals.some((i) => i.status === "indexed");

/** 真实录像资源扫描结果：按「匹配到的镜头 / 识别到的名称」归行，不再有演示数据 */
const rows = computed<CovCamera[]>(() => {
  const groups = new Map<string, CovCamera>();
  for (const clip of recordingClips.value) {
    if (date.value && !(clip.start_local ?? "").startsWith(date.value)) continue;
    const start = startMinutes(clip.start_local);
    if (start === null) continue;
    const key = clip.camera_uuid || clip.camera_name || "(名称未识别)";
    let row = groups.get(key);
    if (!row) {
      row = {
        id: key,
        name: clip.camera_name ?? "名称未识别",
        channel: "",
        analyzing: false,
        notice: clip.match_status !== "matched",
        intervals: [],
      };
      groups.set(key, row);
    }
    const duration = Math.max(0.5, (clip.duration_seconds ?? 0) / 60);
    row.intervals.push({
      id: clip.id,
      s: start,
      e: Math.min(1440, start + duration),
      status: clip.match_status === "matched" ? "indexed" : "unknown",
      files: [clip.file_name],
      note: clip.match_status === "matched" ? undefined : "未唯一匹配到台账镜头",
    });
  }
  const list = [...groups.values()];
  for (const row of list) {
    row.intervals.sort((a, b) => a.s - b.s);
    row.channel = `${row.intervals.length} 段录像`;
  }
  return list.sort((a, b) => a.name.localeCompare(b.name, "zh"));
});

const filteredCameras = computed(() => {
  const keyword = query.value.trim().toLowerCase();
  return rows.value.filter((camera) => {
    if (keyword && !`${camera.name} ${camera.id}`.toLowerCase().includes(keyword)) return false;
    if (filter.value === "with") return !camera.notice;
    if (filter.value === "issues") return camera.notice;
    return true;
  });
});

const covSummary = computed(() => {
  const summary = recordingSummary.value;
  const last = summary?.last_scan_at ? summary.last_scan_at.replace("T", " ").slice(5, 16) : "—";
  return {
    cameras: rows.value.length,
    scanned: summary?.file_count ?? recordingClips.value.length,
    timed: summary?.time_count ?? 0,
    matched: summary?.matched_count ?? 0,
    issues: rows.value.filter((row) => row.notice).length,
    unknownFiles: recordingClips.value.filter((clip) => !clip.start_local).length,
    last,
  };
});

const selected = ref<{ cameraId: string; interval: CovInterval } | null>(null);
const reviewClip = ref<RecordingClip | null>(null);

function reviewInterval(interval: CovInterval) {
  const clip = recordingClips.value.find((item) => item.id === interval.id);
  if (clip) reviewClip.value = clip;
}

function selectInterval(camera: CovCamera, interval: CovInterval) {
  selected.value = { cameraId: camera.id, interval };
  wb.selectedCameraId = camera.id;
  wb.selectedIntervalId = interval.id;
  reviewInterval(interval);
}

async function saveReview(name: string, note: string) {
  const clip = reviewClip.value;
  if (!clip) return;
  const saved = await actions.setRecordingClipName(clip.id, name, note || undefined);
  if (saved) reviewClip.value = null;
}

function isSelected(camera: CovCamera, interval: CovInterval) {
  return selected.value?.cameraId === camera.id && selected.value.interval.id === interval.id;
}

function barStyle(interval: CovInterval) {
  const start = Math.max(interval.s, viewStart.value);
  const end = Math.min(interval.e, viewStart.value + viewSpan.value);
  return { left: `${pct(start)}%`, width: `${Math.max(0.6, pct(end) - pct(start))}%` };
}

const intervalStatusText = (status: CovStatus) =>
  status === "indexed" ? "已匹配到镜头，读到开始时间" : status === "unknown" ? "未匹配到镜头 / 时间未知" : status === "scanning" ? "扫描中（未知）" : "读取 / 解析异常";

const selectedCamera = computed(() => rows.value.find((c) => c.id === selected.value?.cameraId) ?? null);

function locateOnMap() {
  if (!selected.value) return;
  wb.selectedCameraId = selected.value.cameraId;
  ui.coverageOpen = false;
  toast("已选中该镜头；Map 上按名称查看它的点位", "ok");
}

function refresh() {
  toast("录像目录扫描请在底部「录像资源」里发起", "info");
}
</script>

<template>
  <BaseModal
    :open="ui.coverageOpen"
    title="摄像头录像覆盖时间轴"
    subtitle="区段来自录像资源扫描结果：只读到开始时间，结尾时间不推断（留给研判读取视频时补充）。"
    width="xl"
    height="88vh"
    @close="ui.coverageOpen = false"
  >
    <div class="cov">
      <div class="cov-summary">
        <div class="cov-metric"><span>已扫描文件</span><b class="num ok">{{ covSummary.scanned }}</b></div>
        <div class="cov-metric"><span>读到开始时间</span><b class="num">{{ covSummary.timed }}</b></div>
        <div class="cov-metric"><span>匹配到镜头</span><b class="num ok">{{ covSummary.matched }}</b></div>
        <div class="cov-metric"><span>未匹配（需人工核查）</span><b class="num warn">{{ covSummary.issues }}</b></div>
        <div class="cov-metric"><span>时间未知文件</span><b class="num warn">{{ covSummary.unknownFiles }}</b></div>
        <div class="cov-metric"><span>上次扫描</span><b class="num">{{ covSummary.last }}</b></div>
        <span class="grow" />
        <StatusChip :label="recordingSummary?.scanning ? '扫描中' : '只读扫描结果'" :tone="recordingSummary?.scanning ? 'warn' : 'info'" dot />
      </div>

      <div class="cov-toolbar">
        <label class="tb-field" title="留空表示全部日期">
          <span>日期</span>
          <input v-model="date" class="input input--date num" type="date" />
        </label>
        <label class="tb-field">
          <span>范围</span>
          <select class="select select--sm" :value="currentRangeId" @change="setRange(($event.target as HTMLSelectElement).value)">
            <option v-for="option in rangeOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
            <option value="custom" disabled>自定义区间</option>
          </select>
        </label>

        <div class="cov-nav">
          <button class="icon-btn" aria-label="向前平移时间窗" title="向前平移" @click="shiftWindow(-1)"><AppIcon name="chevron-left" :size="14" /></button>
          <span class="cov-nav-label num">{{ tickLabel(viewStart) }} – {{ tickLabel(Math.min(1440, viewStart + viewSpan)) }}</span>
          <button class="icon-btn" aria-label="向后平移时间窗" title="向后平移" @click="shiftWindow(1)"><AppIcon name="chevron-right" :size="14" /></button>
        </div>

        <div class="cov-search">
          <AppIcon name="search" :size="13" />
          <input v-model="query" type="search" placeholder="搜索摄像头名称 / 编号" aria-label="搜索摄像头" />
        </div>

        <div class="cov-seg">
          <button v-for="item in ([
            { id: 'all', label: '全部' },
            { id: 'with', label: '已匹配' },
            { id: 'issues', label: '未匹配' }
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
            <button class="cov-cam cov-sticky" :title="camera.name" @click="wb.selectedCameraId = camera.id">
              <i class="cov-dot" :data-state="camera.analyzing ? 'busy' : hasIndexed(camera) ? 'ok' : 'idle'" />
              <span class="cov-cam-text">
                <strong>{{ camera.name }}</strong>
                <span class="mono">{{ camera.channel }}</span>
              </span>
              <span v-if="camera.notice" class="cov-alert" title="未唯一匹配到台账镜头，需人工核查">!</span>
              <span class="cov-cam-count num">{{ camera.intervals.length }}</span>
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

          <div v-if="!filteredCameras.length" class="cov-empty">
            {{ recordingClips.length ? "没有匹配的镜头或筛选条件" : "该案件还没有扫描到录像：先在底部「录像资源」里添加视频目录并扫描" }}
          </div>
        </div>
      </div>

      <div class="cov-detail">
        <template v-if="selected && selectedCamera">
          <div class="detail-head">
            <strong>{{ selectedCamera.name }}</strong>
            <span class="num">{{ minutesToLabel(selected.interval.s) }} – {{ minutesToLabel(selected.interval.e) }}</span>
            <span class="dim">时长 {{ selected.interval.e - selected.interval.s }} 分钟 · {{ selected.interval.files.length }} 个文件 · {{ intervalStatusText(selected.interval.status) }}</span>
          </div>
          <div class="detail-files scroll-x">
            <span v-for="file in selected.interval.files" :key="file" class="detail-file mono">{{ file }}</span>
            <span v-if="!selected.interval.files.length" class="dim">{{ selected.interval.note ?? "该区间尚无已索引文件" }}</span>
          </div>
          <div class="detail-actions">
            <button class="btn btn--sm btn--outline" @click="reviewInterval(selected.interval)">
              <AppIcon name="check" :size="12" />审核 / 命名
            </button>
            <button class="btn btn--sm btn--primary" @click="locateOnMap">
              <AppIcon name="target" :size="12" />定位到地图
            </button>
          </div>
        </template>
        <p v-else class="detail-empty dim">
          点击区段查看文件名、开始时间与匹配状态。扫描只读开始时间；段长按视频技术时长绘制，不代表已确认结束时间。
        </p>
      </div>

      <div class="cov-legend">
        <span><i class="lg lg--ok" />绿色：匹配到台账镜头（已读到开始时间）</span>
        <span><i class="lg lg--unknown" />斜纹：未唯一匹配到镜头，名称待人工核对</span>
        <span><i class="lg lg--idle" />灰：该镜头暂无扫描到的录像</span>
        <span><i class="lg lg--error" />红：文件读取或解析异常</span>
        <span class="grow" />
        <span class="dim">一段视频一行区段；段长按技术时长绘制，不推断结束时间</span>
      </div>
    </div>
  </BaseModal>

  <RecordingClipNameModal
    :open="Boolean(reviewClip)"
    :clip="reviewClip"
    @close="reviewClip = null"
    @save="saveReview"
  />
</template>

<style scoped>
.cov {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  padding: 12px 16px 14px;
  gap: 10px;
}

.cov-summary {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 10px 14px;
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
  font-size: 11px;
  color: var(--text-3);
  white-space: nowrap;
}

.cov-metric b {
  font-size: 17px;
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
  gap: 12px;
  flex: none;
  flex-wrap: wrap;
}

.tb-field {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-3);
}

.input--date {
  width: 145px;
  height: 30px;
  font-size: 12px;
  padding: 0 8px;
}

.select--sm {
  height: 30px;
  font-size: 12px;
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
  width: 24px;
  height: 24px;
}

.cov-nav-label {
  font-size: 11.5px;
  color: var(--text-1);
  padding: 0 6px;
  white-space: nowrap;
}

.cov-search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 9px;
  border-radius: 6px;
  border: 1px solid var(--line-2);
  background: var(--bg-input);
  color: var(--text-3);
  width: 230px;
}

.cov-search input {
  border: 0;
  background: transparent;
  outline: none;
  font-size: 12px;
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
  height: 24px;
  padding: 0 10px;
  border-radius: 4px;
  font-size: 11.5px;
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
  grid-template-columns: 260px minmax(600px, 1fr);
  height: 32px;
  background: #0b121b;
  border-bottom: 1px solid var(--line-2);
}

.cov-head-cell {
  display: flex;
  align-items: center;
  padding-left: 12px;
  font-size: 11px;
  letter-spacing: 0.06em;
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
  font-size: 11px;
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
  grid-template-columns: 260px minmax(600px, 1fr);
  height: 36px;
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
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cov-cam-text span {
  font-size: 9.5px;
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
  font-size: 10.5px;
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.04);
  border-radius: 99px;
  padding: 0 6px;
  line-height: 16px;
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
  top: 9px;
  height: 18px;
  border-radius: 3px;
  border: 1px solid rgba(47, 207, 146, 0.4);
  background: linear-gradient(180deg, rgba(47, 207, 146, 0.55), rgba(47, 207, 146, 0.3));
  color: #d8fff0;
  font-size: 9.5px;
  line-height: 16px;
  padding: 0 4px;
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
  gap: 14px;
  min-height: 48px;
  padding: 8px 12px;
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
  font-size: 12.5px;
  color: var(--text-0);
  font-weight: 600;
}

.detail-head .num {
  font-size: 11.5px;
  color: var(--text-1);
}

.detail-head .dim {
  font-size: 10.5px;
}

.detail-files {
  display: flex;
  gap: 6px;
  min-width: 0;
  flex: 1;
  overflow-x: auto;
}

.detail-file {
  font-size: 10.5px;
  color: var(--text-2);
  padding: 3px 7px;
  border-radius: 4px;
  border: 1px solid var(--line);
  background: var(--bg-input);
  white-space: nowrap;
}

.detail-actions {
  display: flex;
  gap: 6px;
  flex: none;
}

.detail-empty {
  font-size: 11px;
}

.cov-legend {
  display: flex;
  align-items: center;
  gap: 18px;
  font-size: 11px;
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
