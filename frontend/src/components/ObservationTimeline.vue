<script setup lang="ts">
import { computed, ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import MockFrame from "./ui/MockFrame.vue";
import { type Observation } from "../data/mock";
import { actions, elementList, filteredObservations, timelineCounts, wb, type TimelineFilter } from "../store/workbench";

const statusOptions: { id: TimelineFilter; label: string }[] = [
  { id: "all", label: "全部状态" },
  { id: "anchor", label: "起始锚点" },
  { id: "review", label: "待核查" },
  { id: "approved", label: "已检验" },
  { id: "excluded", label: "已排除" },
];

const order = ref<"asc" | "desc">("asc");

/** 时间轴主标签：优先要素里记录的摄像头名称，其次镜头 id；盲区不编造名称 */
function camLabel(obs: Observation) {
  const element = obs.elementId ? elementList.value.find((item) => item.id === obs.elementId) : undefined;
  if (element?.cameraLabel) return element.cameraLabel;
  if (obs.cameraId) return obs.cameraId;
  return `未知盲区 ${obs.title.replace(/^未知盲区\s*·\s*/, "")}`;
}

const list = computed(() => {
  const rows = [...filteredObservations.value];
  return order.value === "asc" ? rows : rows.reverse();
});

const total = computed(() => timelineCounts.value);

/** 日分组标签取要素里记录的实际日期，没有就显示「本日」 */
const dayLabel = computed(() => {
  const element = elementList.value.find((item) => /^\d{4}-\d{2}-\d{2}/.test(item.time));
  return element ? element.time.slice(0, 10) : "本日";
});

function statusOf(obs: Observation) {
  if (obs.kind === "anchor") return { label: "起始锚点", tone: "anchor" };
  if (obs.kind === "gap") return { label: "盲区", tone: "muted" };
  switch (obs.reliability) {
    case "verified":
      return { label: "已检验", tone: "ok" };
    case "review":
      return { label: "待核查", tone: "warn" };
    case "invalid":
      return { label: "已排除", tone: "info" };
    default:
      return { label: "检测候选", tone: "muted" };
  }
}

/** 追查方向：锚点是倒查起点，其余按观测先后给出一句可执行动作 */
function actionOf(obs: Observation) {
  if (obs.kind === "gap") return null;
  if (obs.kind === "anchor") return "向前倒查";
  return obs.relation === "pending" || obs.relation === "hypothesis" ? "向后追查" : "向后追查";
}

function caption(obs: Observation) {
  if (obs.kind === "anchor") return "";
  return obs.sub || obs.note || "";
}

const dotTone = (obs: Observation) => statusOf(obs).tone;
</script>

<template>
  <aside class="rail" aria-label="观测时间线">
    <header class="rail-head">
      <span class="rail-mark"><AppIcon name="target" :size="13" /></span>
      <span class="rail-title">观测时间线</span>
      <span class="rail-count num">({{ total.all }})</span>
      <button class="icon-btn rail-refresh" aria-label="刷新时间线" title="刷新时间线">
        <AppIcon name="refresh" :size="14" />
      </button>
    </header>

    <div class="rail-filters">
      <select v-model="wb.timelineRound" class="select select--sm" aria-label="按研判轮次筛选">
        <option value="all">全部轮次</option>
        <option value="R1">R1 · 首轮</option>
        <option value="R2">R2 · 当前轮</option>
      </select>
      <select v-model="wb.timelineFilter" class="select select--sm" aria-label="按观测状态筛选">
        <option v-for="option in statusOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
      </select>
      <select v-model="order" class="select select--sm" aria-label="排序">
        <option value="asc">时间正序</option>
        <option value="desc">时间倒序</option>
      </select>
    </div>

    <div class="rail-list scroll-y">
      <div v-if="!list.length" class="rail-empty">
        <span class="rail-empty-icon"><AppIcon name="clock" :size="16" /></span>
        <p class="rail-empty-title">{{ wb.emptyMode ? "时间线为空" : "当前筛选下没有记录" }}</p>
        <p class="rail-empty-text">
          {{ wb.emptyMode ? "添加目标要素后，起始锚点与候选观测将按真实观测时间排序显示。" : "切换筛选条件，或等待新一轮研判产生观测。" }}
        </p>
        <button v-if="wb.emptyMode" class="btn btn--sm btn--outline" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="12" />添加目标要素
        </button>
      </div>

      <template v-else>
        <div class="day-head">
          <button class="day-toggle" title="本日分组">
            <AppIcon name="chevron-down" :size="12" />
          </button>
          <span class="day-label num">{{ dayLabel }}</span>
        </div>

        <div class="tl">
          <article
            v-for="obs in list"
            :key="obs.id"
            class="tl-row"
            :class="{ 'is-selected': wb.selectedObservationId === obs.id }"
          >
            <span class="tl-dot" :data-tone="dotTone(obs)" />
            <button class="tl-card" @click="actions.selectObservation(obs.id)">
              <div class="tl-top">
                <span class="tl-time num">{{ obs.timeLabel }}</span>
                <span class="tl-cam">{{ camLabel(obs) }}</span>
                <span v-if="obs.round" class="tl-round num">{{ obs.round }}</span>
              </div>
              <div class="tl-mid">
                <span class="tl-thumb">
                  <MockFrame
                    :chrome="false"
                    :label="camLabel(obs)"
                    :time="obs.timeLabel"
                    :tone="obs.kind === 'anchor' ? 'gray' : 'color'"
                    :quality="obs.reliability === 'invalid' ? 'low' : 'good'"
                  />
                </span>
                <span class="tl-status" :data-tone="statusOf(obs).tone">{{ statusOf(obs).label }}</span>
                <span v-if="actionOf(obs)" class="tl-act">{{ actionOf(obs) }}</span>
                <span v-else class="tl-note">{{ obs.title.replace(/^未知盲区\s*·\s*/, "") }}</span>
              </div>
              <div v-if="caption(obs)" class="tl-sub">
                <span class="tl-sub-label">特征：</span>{{ caption(obs) }}
              </div>
            </button>
          </article>
        </div>
      </template>
    </div>

    <footer class="rail-foot">
      <span class="dim">按真实观测时间排序 · 不构成身份结论</span>
      <span class="dim num">{{ list.length }} 条</span>
    </footer>
  </aside>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #080c12;
  border-left: 1px solid var(--line);
}

.rail-head {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 42px;
  padding: 0 10px 0 12px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.rail-mark {
  display: grid;
  place-items: center;
  color: var(--accent-hi);
}

.rail-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-0);
}

.rail-count {
  font-size: 12px;
  color: var(--text-3);
}

.rail-refresh {
  margin-left: auto;
  width: 24px;
  height: 24px;
}

.rail-filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.select--sm {
  height: 26px;
  font-size: 11px;
  padding: 0 6px;
  border-radius: 6px;
}

.rail-list {
  flex: 1;
  min-height: 0;
  padding: 6px 10px 12px;
}

.day-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0 8px;
}

.day-toggle {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1px solid var(--line-3);
  color: var(--text-3);
}

.day-label {
  font-size: 12px;
  color: var(--text-2);
}

.tl {
  position: relative;
  display: grid;
  gap: 6px;
}

.tl::before {
  content: "";
  position: absolute;
  left: 7px;
  top: 8px;
  bottom: 8px;
  width: 1px;
  background: var(--line-2);
}

.tl-row {
  position: relative;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
}

.tl-dot {
  position: relative;
  z-index: 1;
  width: 9px;
  height: 9px;
  margin: 9px 0 0 3px;
  border-radius: 50%;
  background: var(--text-3);
  box-shadow: 0 0 0 3px #080c12;
}

.tl-dot[data-tone="ok"] {
  background: var(--ok);
}

.tl-dot[data-tone="warn"] {
  background: var(--warn);
}

.tl-dot[data-tone="info"] {
  background: var(--accent);
}

.tl-dot[data-tone="anchor"] {
  background: var(--violet);
}

.tl-card {
  display: grid;
  gap: 6px;
  width: 100%;
  padding: 8px 9px 9px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: #0c121a;
  text-align: left;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.tl-card:hover {
  border-color: var(--line-3);
}

.tl-row.is-selected .tl-card {
  border-color: var(--accent-line);
  background: #0d1622;
}

.tl-top {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.tl-time {
  font-size: 13px;
  font-weight: 650;
  color: var(--text-0);
  letter-spacing: 0.01em;
}

.tl-cam {
  font-size: 11.5px;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tl-el {
  flex: none;
  padding: 1px 6px;
  border-radius: 4px;
  background: #5b3fd0;
  color: #efe9ff;
  font-size: 11px;
  font-weight: 600;
}

.tl-round {
  margin-left: auto;
  flex: none;
  font-size: 10.5px;
  color: var(--text-4);
}

.tl-mid {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tl-thumb {
  flex: none;
  width: 62px;
  height: 42px;
  border-radius: 5px;
  overflow: hidden;
  border: 1px solid var(--line-2);
  background: #0a0f15;
}

.tl-thumb :deep(.mock-frame) {
  width: 100%;
  height: 100%;
  border-radius: 0;
}

.tl-status {
  flex: none;
  padding: 3px 9px;
  border-radius: 5px;
  font-size: 11.5px;
  font-weight: 600;
  color: #06110c;
  background: var(--text-3);
}

.tl-status[data-tone="ok"],
.tl-status[data-tone="anchor"] {
  background: #24c48b;
  color: #04150e;
}

.tl-status[data-tone="warn"] {
  background: #f0a63c;
  color: #1a1003;
}

.tl-status[data-tone="info"] {
  background: #3b82f6;
  color: #f2f7ff;
}

.tl-status[data-tone="muted"] {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-1);
}

.tl-act {
  flex: none;
  padding: 3px 9px;
  border-radius: 5px;
  border: 1px solid var(--line-3);
  background: rgba(77, 141, 255, 0.08);
  color: #8fb6ff;
  font-size: 11.5px;
}

.tl-note {
  font-size: 11px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tl-sub {
  font-size: 11px;
  line-height: 1.4;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tl-sub-label {
  color: var(--text-4);
}

.rail-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 28px;
  padding: 0 10px;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  flex: none;
}

.rail-empty {
  display: grid;
  justify-items: center;
  gap: 6px;
  padding: 26px 16px;
  text-align: center;
}

.rail-empty-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px dashed var(--line-3);
  color: var(--text-3);
}

.rail-empty-title {
  font-size: var(--fs-label);
  color: var(--text-1);
  font-weight: 600;
}

.rail-empty-text {
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-3);
}
</style>