<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { cameras, indexInfo, roundInfo } from "../data/mock";
import {
  actions,
  indexView,
  observationList,
  roundView,
  ui,
  visibleElements,
  visibleJobs,
  wb,
} from "../store/workbench";

type DockTab = "control" | "resources" | "analyzing" | "candidates" | "history";

const tabs: { id: DockTab; label: string; icon: "list" | "folder" | "camera" | "target" | "clock" }[] = [
  { id: "control", label: "任务控制", icon: "list" },
  { id: "resources", label: "录像资源", icon: "folder" },
  { id: "analyzing", label: "正在分析", icon: "camera" },
  { id: "candidates", label: "候选结果", icon: "target" },
  { id: "history", label: "历史记录", icon: "clock" },
];

const tab = ref<DockTab>("control");
const openRow = ref<"index" | "jobs" | "candidates" | null>(null);

const collapsedSummary = computed(() => {
  if (wb.emptyMode) return "未选择录像目录 · 无目标要素 · 未启动研判";
  return `索引 ${indexView.value.progress}% · 要素 ${visibleElements.value.length} · 轮次 ${roundView.value.id} 分析中 ${roundView.value.progress}% · 作业 ${visibleJobs.value.length}`;
});

/** 参考设计的 5 段进度：准备 → 索引 → 排队 → 分析 → 整理 */
const stages = computed(() => [
  { key: "prepare", label: "准备中", value: 100 },
  { key: "index", label: "目标索引", value: indexView.value.progress },
  { key: "queue", label: "视频排队", value: 65 },
  { key: "model", label: "模型分析", value: roundView.value.progress },
  { key: "assemble", label: "结果整理", value: 12 },
]);
const activeStage = 2;

const running = computed(() => visibleJobs.value.filter((job) => job.status !== "queued"));
const currentJob = computed(() => running.value[0] ?? visibleJobs.value[0] ?? null);
const currentCameraName = computed(() => {
  const id = currentJob.value?.cameraId;
  return id ? (cameras.find((camera) => camera.id === id)?.name ?? "") : "";
});

const candidateRows = computed(() => observationList.value.filter((item) => item.kind !== "gap"));
const reviewCount = computed(() => candidateRows.value.filter((item) => item.reliability === "review").length);
const excludedCount = computed(() => candidateRows.value.filter((item) => item.reliability === "invalid").length);

const toggleRow = (key: "index" | "jobs" | "candidates") => {
  openRow.value = openRow.value === key ? null : key;
};

let cleanup: (() => void) | null = null;

function startResize(event: PointerEvent) {
  if (event.button !== 0) return;
  event.preventDefault();
  const startY = event.clientY;
  const startHeight = ui.dockHeight;
  document.body.style.cursor = "ns-resize";
  document.body.style.userSelect = "none";

  const onMove = (moveEvent: PointerEvent) => {
    const next = startHeight - (moveEvent.clientY - startY);
    const max = Math.round(window.innerHeight * 0.58);
    ui.dockHeight = Math.round(Math.min(Math.max(next, 196), max));
  };
  const onUp = () => {
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    cleanup = null;
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  cleanup = onUp;
}

onBeforeUnmount(() => cleanup?.());
</script>

<template>
  <section class="dock" :class="{ 'is-collapsed': ui.dockCollapsed }" :style="{ height: ui.dockCollapsed ? '32px' : `${ui.dockHeight}px` }">
    <div
      class="dock-grip"
      role="separator"
      aria-orientation="horizontal"
      :aria-label="`任务操作台高度 ${ui.dockHeight} 像素，可拖动调整`"
      @pointerdown="startResize"
    >
      <span class="grip-bar" />
      <span v-if="ui.dockCollapsed" class="grip-label">任务控制</span>
      <span v-if="ui.dockCollapsed" class="grip-summary">{{ collapsedSummary }}</span>
      <button
        class="icon-btn grip-toggle"
        :aria-label="ui.dockCollapsed ? '展开任务操作台' : '收起任务操作台'"
        :title="ui.dockCollapsed ? '展开' : '收起'"
        @pointerdown.stop
        @click="ui.dockCollapsed = !ui.dockCollapsed"
      >
        <AppIcon :name="ui.dockCollapsed ? 'chevron-up' : 'chevron-down'" :size="14" />
      </button>
    </div>

    <div v-show="!ui.dockCollapsed" class="dock-body">
      <nav class="dock-tabs" aria-label="任务控制分区">
        <button
          v-for="item in tabs"
          :key="item.id"
          class="dock-tab"
          :class="{ 'is-on': tab === item.id }"
          :aria-pressed="tab === item.id"
          @click="tab = item.id"
        >
          <AppIcon :name="item.icon" :size="15" />
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="dock-panel">
        <!-- 任务控制 -->
        <template v-if="tab === 'control'">
          <div class="round-line">
            <span class="round-label">当前研判轮次</span>
            <span class="round-chip num">{{ roundInfo.id }}</span>
            <span class="round-desc">（基于 {{ roundInfo.anchor }} · {{ roundInfo.directionLabel }}）</span>
            <span class="round-window">时间范围：2026-10-25 18:00–20:12</span>
          </div>

          <div class="stepper">
            <div
              v-for="(stage, index) in stages"
              :key="stage.key"
              class="stage"
              :class="{ 'is-active': index === activeStage }"
            >
              <span class="stage-no num">{{ [1, 2, 3, 3, 4][index] }}</span>
              <span class="stage-label">{{ stage.label }}</span>
              <span class="stage-pct num">{{ stage.value }}%</span>
            </div>

            <div class="stage-actions">
              <button class="btn btn--primary" :disabled="wb.emptyMode" @click="actions.resumeRound()">
                <AppIcon name="play" :size="13" />继续分析
              </button>
              <button class="btn btn--outline" :disabled="wb.emptyMode" @click="actions.pauseRound()">
                <AppIcon name="pause" :size="13" />暂停
              </button>
              <button class="btn btn--danger" :disabled="wb.emptyMode" @click="actions.stopRound()">
                <AppIcon name="stop" :size="13" />终止
              </button>
            </div>
          </div>

          <div class="rows">
            <div class="row" :class="{ 'is-open': openRow === 'index' }">
              <button class="row-head" @click="toggleRow('index')">
                <AppIcon name="chevron-right" :size="13" class="row-chev" />
                <span class="row-title">录像资源索引</span>
                <span class="row-meta">
                  已识别 <b class="num">{{ indexInfo.files.toLocaleString() }}</b> 个视频文件
                  <i>|</i> 已解析 <b class="num">{{ indexInfo.parsed.toLocaleString() }}</b>
                  <i>|</i> 未映射 <b class="num">{{ indexInfo.unmapped }}</b>
                  <i>|</i> 异常 <b class="num danger">{{ indexInfo.errors }}</b>
                </span>
                <span class="row-side">
                  上次扫描 <b class="num">{{ indexInfo.lastRun }}</b>
                  <span class="btn btn--sm btn--outline" @click.stop><AppIcon name="refresh" :size="12" />重新扫描</span>
                </span>
              </button>
              <div v-if="openRow === 'index'" class="row-body">
                <span class="row-dir mono">{{ indexInfo.dir }}</span>
                <span class="stage-list">
                  <span v-for="(stage, i) in indexInfo.stages" :key="stage" class="stage-item">
                    <i class="num">{{ i + 1 }}</i>{{ stage }}
                  </span>
                </span>
                <span class="row-hint">索引 ≠ 目标检测；未映射文件不参与检索。</span>
              </div>
            </div>

            <div class="row" :class="{ 'is-open': openRow === 'jobs' }">
              <button class="row-head" @click="toggleRow('jobs')">
                <AppIcon name="chevron-right" :size="13" class="row-chev" />
                <span class="row-title">正在分析的视频 <span class="num row-badge">({{ running.length }})</span></span>
                <span class="row-meta">
                  <template v-if="currentJob">
                    当前：<b>{{ currentJob.cameraId }}</b> {{ currentCameraName }}
                    <i>|</i> 2026-10-25 {{ currentJob.from }}–{{ currentJob.to }}
                    <i>|</i> 进度 <b class="num">{{ currentJob.progress }}%</b>
                  </template>
                  <template v-else>当前没有正在分析的视频</template>
                </span>
                <span class="row-side"><span class="btn btn--sm btn--outline"><AppIcon name="scan" :size="12" />展开</span></span>
              </button>
              <div v-if="openRow === 'jobs'" class="row-body">
                <div v-for="job in visibleJobs" :key="job.id" class="job-row">
                  <span class="mono job-id">{{ job.id }}</span>
                  <span class="job-cam">{{ job.cameraId }}</span>
                  <span class="job-file truncate">{{ job.file }}</span>
                  <span class="job-status" :data-status="job.status">{{ job.statusLabel }}</span>
                  <span class="job-prog"><i :style="{ width: `${job.progress}%` }" /></span>
                  <span class="num job-pct">{{ job.progress }}%</span>
                </div>
              </div>
            </div>

            <div class="row" :class="{ 'is-open': openRow === 'candidates' }">
              <button class="row-head" @click="toggleRow('candidates')">
                <AppIcon name="chevron-right" :size="13" class="row-chev" />
                <span class="row-title">候选结果 <span class="num row-badge">({{ candidateRows.length }})</span></span>
                <span class="row-meta">
                  待核查 <b class="num warn">{{ reviewCount }}</b>
                  <i>|</i> 已排除 <b class="num">{{ excludedCount }}</b>
                  <i>|</i> 已解排 <b class="num">1</b>
                </span>
                <span class="row-side"><span class="btn btn--sm btn--outline"><AppIcon name="scan" :size="12" />展开</span></span>
              </button>
              <div v-if="openRow === 'candidates'" class="row-body row-body--col">
                <button
                  v-for="item in candidateRows.slice(0, 4)"
                  :key="item.id"
                  class="cand-row"
                  @click="actions.selectObservation(item.id)"
                >
                  <span class="num cand-time">{{ item.timeLabel }}</span>
                  <span class="cand-cam">{{ item.cameraId }} {{ item.title }}</span>
                  <span class="cand-status" :data-tone="item.reliability">
                    {{ item.reliability === "verified" ? "已检验" : item.reliability === "invalid" ? "已排除" : "待核查" }}
                  </span>
                </button>
              </div>
            </div>
          </div>
        </template>

        <!-- 录像资源 -->
        <template v-else-if="tab === 'resources'">
          <div class="panel-head">
            <span class="panel-title">录像资源</span>
            <span class="panel-sub mono">{{ indexInfo.dir }}</span>
            <span class="panel-side">最近扫描 <b class="num">{{ indexInfo.lastRun }}</b></span>
          </div>
          <div class="stat-grid">
            <div class="stat"><span>已识别文件</span><b class="num">{{ indexInfo.files.toLocaleString() }}</b></div>
            <div class="stat"><span>已解析时间</span><b class="num">{{ indexInfo.parsed.toLocaleString() }}</b></div>
            <div class="stat"><span>已映射元素</span><b class="num">{{ indexInfo.mapped }}</b></div>
            <div class="stat"><span>未映射</span><b class="num warn">{{ indexInfo.unmapped }}</b></div>
            <div class="stat"><span>异常</span><b class="num danger">{{ indexInfo.errors }}</b></div>
            <div class="stat"><span>索引进度</span><b class="num">{{ indexInfo.progress }}%</b></div>
          </div>
          <div class="panel-note">录像按真实时间切片建立索引；未映射文件不参与检索，需人工补录。</div>
        </template>

        <!-- 正在分析 -->
        <template v-else-if="tab === 'analyzing'">
          <div class="panel-head">
            <span class="panel-title">正在分析</span>
            <span class="panel-sub">共 {{ visibleJobs.length }} 个作业 · 运行中 {{ running.length }}</span>
          </div>
          <div class="job-table">
            <div class="job-head">
              <span>作业</span><span>摄像头</span><span>文件</span><span>状态</span><span>进度</span><span />
            </div>
            <div v-for="job in visibleJobs" :key="job.id" class="job-line" :class="{ 'is-active': job.id === currentJob?.id }">
              <span class="mono job-id">{{ job.id }}</span>
              <span>{{ job.cameraId }}</span>
              <span class="truncate">{{ job.file }}</span>
              <span class="job-status" :data-status="job.status">{{ job.statusLabel }}</span>
              <span class="job-prog"><i :style="{ width: `${job.progress}%` }" /></span>
              <span class="num job-pct">{{ job.progress }}%</span>
            </div>
          </div>
        </template>

        <!-- 候选结果 -->
        <template v-else-if="tab === 'candidates'">
          <div class="panel-head">
            <span class="panel-title">候选结果</span>
            <span class="panel-sub">待核查 {{ reviewCount }} · 已排除 {{ excludedCount }} · 共 {{ candidateRows.length }}</span>
          </div>
          <div class="cand-table">
            <div v-for="item in candidateRows" :key="item.id" class="cand-line" @click="actions.selectObservation(item.id)">
              <span class="num cand-time">{{ item.timeLabel }}</span>
              <span class="cand-cam">{{ item.cameraId ?? "—" }}</span>
              <span class="truncate">{{ item.title }}</span>
              <span class="cand-status" :data-tone="item.reliability">
                {{ item.reliability === "verified" ? "已检验" : item.reliability === "invalid" ? "已排除" : item.reliability === "review" ? "待核查" : "检测候选" }}
              </span>
              <span class="cand-round num">{{ item.round }}</span>
            </div>
          </div>
        </template>

        <!-- 历史记录 -->
        <template v-else>
          <div class="panel-empty">
            <AppIcon name="clock" :size="18" />
            <strong>暂无历史轮次</strong>
            <span>每轮研判结束后，轮次、窗口与审核结果会记录在这里。</span>
          </div>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dock {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #080c12;
  border-top: 1px solid var(--line);
  flex: none;
}

.dock-grip {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 8px;
  cursor: ns-resize;
  flex: none;
}

.grip-bar {
  width: 42px;
  height: 3px;
  border-radius: 2px;
  background: var(--line-3);
}

.grip-label {
  font-size: 11px;
  color: var(--text-3);
}

.grip-summary {
  font-size: 10.5px;
  color: var(--text-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dock.is-collapsed .dock-grip {
  height: 30px;
  justify-content: flex-start;
  padding: 0 12px;
}

.grip-toggle {
  position: absolute;
  right: 8px;
  top: 1px;
  width: 24px;
  height: 18px;
}

.dock-body {
  display: grid;
  grid-template-columns: 116px minmax(0, 1fr);
  flex: 1;
  min-height: 0;
  border-top: 1px solid var(--line);
}

.dock-tabs {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 6px 8px;
  border-right: 1px solid var(--line);
  background: #070a0f;
}

.dock-tab {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 9px;
  border-radius: 7px;
  border: 1px solid transparent;
  color: var(--text-2);
  font-size: 12px;
  text-align: left;
}

.dock-tab:hover {
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-1);
}

.dock-tab.is-on {
  background: rgba(77, 141, 255, 0.12);
  border-color: rgba(77, 141, 255, 0.32);
  color: #cfe0ff;
}

.dock-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  min-height: 0;
  padding: 8px 12px 10px;
  overflow: auto;
}

/* -------- 轮次 + 步骤条 -------- */

.round-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--line);
  font-size: 12px;
  color: var(--text-2);
  flex: none;
}

.round-label {
  color: var(--text-1);
  font-weight: 600;
}

.round-chip {
  padding: 1px 8px;
  border-radius: 4px;
  background: #5b3fd0;
  color: #efe9ff;
  font-weight: 650;
}

.round-desc {
  color: var(--text-3);
}

.round-window {
  margin-left: auto;
  color: var(--text-3);
}

.stepper {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.stage {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 30px;
  padding: 0 12px 0 8px;
  border-radius: 15px;
  border: 1px solid var(--line-2);
  background: #0b1017;
  white-space: nowrap;
}

.stage.is-active {
  border-color: var(--accent-line);
  background: rgba(77, 141, 255, 0.12);
}

.stage-no {
  display: grid;
  place-items: center;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  color: var(--text-2);
  font-size: 10px;
}

.stage.is-active .stage-no {
  background: var(--accent);
  color: #fff;
}

.stage-label {
  font-size: 11.5px;
  color: var(--text-1);
}

.stage-pct {
  font-size: 11.5px;
  font-weight: 650;
  color: var(--ok);
}

.stage.is-active .stage-pct {
  color: #cfe0ff;
}

.stage-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.stage-actions .btn {
  height: 32px;
  padding: 0 14px;
  border-radius: 6px;
  font-size: 12px;
}

.btn--danger {
  background: #c0392b;
  border: 1px solid #d8483a;
  color: #fff;
}

.btn--danger:hover {
  background: #d14334;
}

/* -------- 折叠行 -------- */

.rows {
  display: grid;
  gap: 6px;
  min-height: 0;
}

.row {
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #0b1017;
  overflow: hidden;
}

.row-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  text-align: left;
  color: var(--text-2);
  font-size: 11.5px;
}

.row-chev {
  color: var(--text-4);
  transition: transform var(--dur) var(--ease);
}

.row.is-open .row-chev {
  transform: rotate(90deg);
}

.row-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-0);
  white-space: nowrap;
}

.row-badge {
  color: var(--text-2);
  font-weight: 400;
}

.row-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-meta b {
  color: var(--text-1);
  font-weight: 600;
}

.row-meta i {
  color: var(--line-3);
  font-style: normal;
}

.row-meta .danger {
  color: var(--danger);
}

.row-meta .warn {
  color: var(--warn);
}

.row-side {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: auto;
  color: var(--text-3);
  white-space: nowrap;
}

.row-body {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 10px 9px 32px;
  flex-wrap: wrap;
}

.row-body--col {
  flex-direction: column;
  align-items: stretch;
  gap: 0;
}

.row-dir {
  font-size: 11px;
  color: var(--text-2);
}

.stage-list {
  display: flex;
  gap: 8px;
}

.stage-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  color: var(--text-3);
}

.stage-item i {
  display: grid;
  place-items: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: rgba(47, 207, 146, 0.15);
  color: var(--ok);
  font-size: 9px;
}

.row-hint {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--text-4);
}

.job-row,
.job-line {
  display: grid;
  grid-template-columns: 70px 74px minmax(0, 1fr) 96px 120px 44px;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 5px 0;
  font-size: 11px;
  color: var(--text-2);
  border-bottom: 1px solid var(--line);
}

.job-row:last-child,
.job-line:last-child {
  border-bottom: 0;
}

.job-id {
  color: var(--text-3);
}

.job-cam {
  color: var(--text-1);
}

.job-file {
  color: var(--text-2);
}

.job-prog {
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}

.job-prog i {
  display: block;
  height: 100%;
  background: var(--accent);
}

.job-pct {
  text-align: right;
  color: var(--text-3);
}

.job-status {
  font-size: 10.5px;
  color: var(--text-2);
}

.job-status[data-status="detecting"],
.job-status[data-status="assembling"] {
  color: #8fb6ff;
}

.job-status[data-status="review"] {
  color: var(--warn);
}

.cand-row,
.cand-line {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 4px 0;
  font-size: 11px;
  color: var(--text-2);
  border-bottom: 1px solid var(--line);
  text-align: left;
}

.cand-time {
  color: var(--text-1);
  width: 58px;
}

.cand-cam {
  width: 76px;
  color: var(--text-3);
}

.cand-status {
  margin-left: auto;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 10.5px;
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-1);
}

.cand-status[data-tone="verified"] {
  background: rgba(47, 207, 146, 0.16);
  color: #7fe3bb;
}

.cand-status[data-tone="review"] {
  background: rgba(240, 180, 90, 0.16);
  color: #f6c98a;
}

.cand-status[data-tone="invalid"] {
  background: rgba(77, 141, 255, 0.16);
  color: #a8c6ff;
}

.cand-round {
  width: 26px;
  text-align: right;
  color: var(--text-4);
}

/* -------- 其它分区 -------- */

.panel-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.panel-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-0);
}

.panel-sub {
  font-size: 11px;
  color: var(--text-3);
}

.panel-side {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-3);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

.stat {
  display: grid;
  gap: 3px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #0b1017;
}

.stat span {
  font-size: 10.5px;
  color: var(--text-3);
}

.stat b {
  font-size: 17px;
  color: var(--text-0);
}

.stat b.warn {
  color: var(--warn);
}

.stat b.danger {
  color: var(--danger);
}

.panel-note {
  font-size: 11px;
  color: var(--text-3);
}

.job-table,
.cand-table {
  display: grid;
  gap: 2px;
}

.job-head {
  display: grid;
  grid-template-columns: 70px 74px minmax(0, 1fr) 96px 120px 44px;
  gap: 8px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--line-2);
  font-size: 10.5px;
  color: var(--text-4);
}

.job-line.is-active {
  background: rgba(77, 141, 255, 0.06);
}

.panel-empty {
  display: grid;
  justify-items: center;
  gap: 6px;
  padding: 24px 16px;
  color: var(--text-3);
  text-align: center;
}

.panel-empty strong {
  font-size: 12.5px;
  color: var(--text-1);
}

.panel-empty span {
  font-size: 11px;
}
</style>