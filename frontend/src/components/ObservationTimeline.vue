<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { cameras, type Observation } from "../data/mock";
import {
  actions,
  filteredObservations,
  timelineCounts,
  wb,
  type TimelineFilter,
} from "../store/workbench";

const filters: { id: TimelineFilter; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "anchor", label: "起始锚点" },
  { id: "review", label: "待复核" },
  { id: "approved", label: "已审核" },
  { id: "excluded", label: "已排除" },
];

const reliabilityMap = {
  detected: { label: "检测候选", tone: "muted" },
  review: { label: "观察待核验", tone: "warn" },
  verified: { label: "观察已核验", tone: "ok" },
  invalid: { label: "观察无效", tone: "danger" },
} as const;

const relationMap = {
  unknown: { label: "关联未判断", tone: "muted" },
  hypothesis: { label: "工作假设", tone: "info" },
  pending: { label: "关联待复核", tone: "warn" },
  confirmed: { label: "人工确认关联", tone: "ok" },
  excluded: { label: "关联已排除", tone: "danger" },
  indeterminate: { label: "无法判断", tone: "muted" },
} as const;

const chips = (obs: Observation) => {
  const list: { label: string; tone: string }[] = [];
  if (obs.reliability) list.push(reliabilityMap[obs.reliability]);
  if (obs.relation) list.push(relationMap[obs.relation]);
  if (obs.kind === "gap") list.push({ label: "无已索引录像", tone: "muted" });
  return list;
};

const kindLabel = (kind: Observation["kind"]) =>
  kind === "anchor" ? "起始锚点" : kind === "gap" ? "盲区" : "候选观测";

const total = computed(() => timelineCounts.value);

function select(obs: Observation) {
  actions.selectObservation(obs.id);
}

function focusNew() {
  wb.timelineFilter = "review";
  actions.dismissNewCandidates();
}
</script>

<template>
  <aside class="rail" aria-label="统一观测时间线">
    <header class="rail-head">
      <div class="rail-title">
        <span class="card-title">统一观测时间线</span>
        <span class="rail-count num">{{ total.all }}</span>
      </div>
      <div class="rail-tools">
        <button class="icon-btn" aria-label="刷新时间线" title="刷新时间线">
          <AppIcon name="refresh" :size="14" />
        </button>
        <button class="icon-btn" aria-label="时间线筛选设置" title="筛选设置">
          <AppIcon name="filter" :size="14" />
        </button>
      </div>
    </header>

    <div class="rail-filters scroll-x">
      <button
        v-for="filter in filters"
        :key="filter.id"
        class="fchip"
        :class="{ 'is-on': wb.timelineFilter === filter.id }"
        @click="wb.timelineFilter = filter.id"
      >
        {{ filter.label }}
        <span class="num">{{ total[filter.id] }}</span>
      </button>
    </div>

    <div class="rail-selects">
      <select v-model="wb.timelineRound" class="select select--sm" aria-label="按研判轮次筛选">
        <option value="all">全部轮次</option>
        <option value="R1">R1 · 首轮</option>
        <option value="R2">R2 · 当前轮</option>
      </select>
      <select v-model="wb.timelineCamera" class="select select--sm" aria-label="按摄像头筛选">
        <option value="all">全部来源</option>
        <option v-for="camera in cameras" :key="camera.id" :value="camera.id">{{ camera.id }} {{ camera.name }}</option>
      </select>
    </div>

    <div v-if="wb.newCandidates > 0" class="rail-notice">
      <AppIcon name="info" :size="13" />
      <span>发现 <b class="num">{{ wb.newCandidates }}</b> 条新候选</span>
      <button class="rail-notice-link" @click="focusNew">查看</button>
      <button class="rail-notice-x" aria-label="忽略本次提醒" @click="actions.dismissNewCandidates()">忽略</button>
    </div>

    <div class="rail-list scroll-y">
      <div v-if="!filteredObservations.length" class="rail-empty">
        <span class="rail-empty-icon"><AppIcon name="clock" :size="16" /></span>
        <p class="rail-empty-title">{{ wb.emptyMode ? "时间线为空" : "当前筛选下没有记录" }}</p>
        <p class="rail-empty-text">
          {{ wb.emptyMode ? "添加目标要素后，起始锚点与候选观测将按真实观测时间排序显示。" : "切换筛选条件，或等待新一轮研判产生观测。" }}
        </p>
        <button v-if="wb.emptyMode" class="btn btn--sm btn--outline" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="12" />添加目标要素
        </button>
      </div>

      <button
        v-for="obs in filteredObservations"
        :key="obs.id"
        class="rec"
        :class="[`rec--${obs.kind}`, { 'is-selected': wb.selectedObservationId === obs.id }]"
        :title="obs.note ?? obs.sub"
        @click="select(obs)"
      >
        <span class="rec-rail">
          <i class="rec-dot" />
        </span>
        <span class="rec-body">
          <span class="rec-top">
            <span class="rec-time num">{{ obs.timeLabel }}</span>
            <span class="rec-kind">{{ kindLabel(obs.kind) }}</span>
            <span v-if="obs.round" class="rec-round num">{{ obs.round }}</span>
          </span>
          <span class="rec-title">{{ obs.title }}</span>
          <span class="rec-sub">{{ obs.sub }}</span>
          <span v-if="chips(obs).length" class="rec-chips">
            <span v-for="chip in chips(obs)" :key="chip.label" class="chip" :data-tone="chip.tone">{{ chip.label }}</span>
          </span>
        </span>
      </button>
    </div>

    <footer class="rail-foot">
      <span class="dim">按实际观测时间升序</span>
      <span class="dim">共 {{ filteredObservations.length }} 条</span>
    </footer>
  </aside>
</template>

<style scoped>
.rail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg-1);
  border-left: 1px solid var(--line);
}

.rail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 36px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.rail-title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.rail-count {
  font-size: 10px;
  color: var(--text-3);
  background: var(--bg-3);
  border-radius: 99px;
  padding: 0 6px;
  line-height: 16px;
}

.rail-tools {
  display: flex;
  gap: 2px;
  flex: none;
}

.rail-tools .icon-btn {
  width: 22px;
  height: 22px;
}

.rail-filters {
  display: flex;
  gap: 3px;
  padding: 7px 8px 6px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.fchip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 7px;
  border-radius: 5px;
  border: 1px solid transparent;
  color: var(--text-2);
  font-size: 10.5px;
  white-space: nowrap;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.fchip:hover {
  background: var(--bg-3);
  color: var(--text-1);
}

.fchip.is-on {
  background: var(--accent-soft);
  border-color: var(--accent-line);
  color: var(--accent-hi);
}

.fchip span {
  font-size: 9px;
  color: var(--text-3);
}

.fchip.is-on span {
  color: #8fb6ff;
}

.rail-selects {
  display: flex;
  gap: 5px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.select--sm {
  height: 24px;
  font-size: 10px;
  padding-left: 6px;
}

.rail-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 7px 8px 0;
  padding: 5px 8px;
  border-radius: 6px;
  border: 1px solid var(--accent-line);
  background: var(--accent-soft);
  color: #b6cffd;
  font-size: 10px;
  flex: none;
}

.rail-notice b {
  color: #dbe8ff;
}

.rail-notice-link {
  margin-left: auto;
  color: #cfe0ff;
  font-size: 10px;
  text-decoration: underline;
}

.rail-notice-x {
  color: var(--text-3);
  font-size: 10px;
}

.rail-notice-x:hover {
  color: var(--text-1);
}

.rail-list {
  flex: 1;
  min-height: 0;
  padding: 6px 6px 10px 8px;
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
  font-size: 10px;
  line-height: 1.55;
  color: var(--text-3);
}

.rec {
  position: relative;
  display: flex;
  gap: 8px;
  width: 100%;
  padding: 7px 8px 7px 4px;
  border-radius: 7px;
  border: 1px solid transparent;
  text-align: left;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.rec:hover {
  background: rgba(255, 255, 255, 0.03);
}

.rec.is-selected {
  background: linear-gradient(90deg, rgba(77, 141, 255, 0.12), rgba(77, 141, 255, 0.02));
  border-color: rgba(77, 141, 255, 0.28);
}

.rec-rail {
  position: relative;
  width: 10px;
  flex: none;
  display: flex;
  justify-content: center;
}

.rec-rail::before {
  content: "";
  position: absolute;
  top: 12px;
  bottom: -12px;
  width: 1px;
  background: var(--line-2);
}

.rec:last-child .rec-rail::before {
  display: none;
}

.rec-dot {
  position: relative;
  z-index: 1;
  width: 7px;
  height: 7px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--bg-1);
  border: 1.6px solid var(--text-3);
}

.rec--anchor .rec-dot {
  border-color: var(--violet);
  background: var(--violet);
  border-radius: 50% 50% 50% 0;
  transform: rotate(-45deg);
}

.rec--gap .rec-dot {
  border-color: #55657a;
  border-style: dashed;
  background: transparent;
}

.rec-body {
  display: grid;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.rec-top {
  display: flex;
  align-items: center;
  gap: 6px;
}

.rec-time {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-1);
  letter-spacing: 0.01em;
}

.rec-kind {
  font-size: 9px;
  color: var(--text-3);
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.04);
  white-space: nowrap;
}

.rec--anchor .rec-kind {
  color: #c3aeff;
  background: var(--violet-soft);
}

.rec-round {
  margin-left: auto;
  font-size: 8.5px;
  color: var(--text-4);
}

.rec-title {
  font-size: 11.5px;
  color: var(--text-1);
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rec--anchor .rec-title {
  color: #d5c9ff;
}

.rec-sub {
  font-size: 9.5px;
  color: var(--text-3);
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.rec-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-top: 2px;
}

.rec-chips .chip {
  height: 16px;
  font-size: 8.5px;
  padding: 0 5px;
}

.rail-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 26px;
  padding: 0 10px;
  border-top: 1px solid var(--line);
  font-size: 9.5px;
  flex: none;
}
</style>
