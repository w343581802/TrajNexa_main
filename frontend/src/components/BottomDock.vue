<script setup lang="ts">
import { computed, onBeforeUnmount } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import TargetFeatureBar from "./TargetFeatureBar.vue";
import { roundInfo } from "../data/mock";
import {
  actions,
  activityView,
  indexView,
  jobStats,
  roundView,
  ui,
  visibleElements,
  wb,
} from "../store/workbench";

const collapsedSummary = computed(() => {
  if (wb.emptyMode) return "空任务 · 无目标要素 · 研判未启动";
  const stats = jobStats.value;
  const review = stats.review ? ` · 待审核 ${stats.review}` : "";
  return `要素 ${visibleElements.value.length} · 分析中 ${stats.running}${review} · 轮次 ${roundView.value.id}`;
});

let cleanup: (() => void) | null = null;

function startResize(event: PointerEvent) {
  if (event.button !== 0) return;
  if (document.body.classList.contains("tm-drawer-open") || document.body.classList.contains("tm-preview-open")) return;
  event.preventDefault();
  const startY = event.clientY;
  const startHeight = ui.dockHeight;
  document.body.style.cursor = "ns-resize";
  document.body.style.userSelect = "none";

  const onMove = (moveEvent: PointerEvent) => {
    const next = startHeight - (moveEvent.clientY - startY);
    const max = Math.round(window.innerHeight * 0.62);
    ui.dockHeight = Math.round(Math.min(Math.max(next, 208), max));
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

onBeforeUnmount(() => {
  cleanup?.();
});
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
      <div class="dock-panel">
        <!-- 任务控制：左控制 / 右分析卡片 -->
        <div class="split split--control">
          <aside class="col col--left">
            <div class="round-line">
              <span class="round-chip num">{{ roundInfo.id }}</span>
              <span class="round-desc truncate">{{ roundInfo.anchor }} · {{ roundInfo.directionLabel }}</span>
              <span class="round-window num">{{ wb.from }}–{{ wb.to }}</span>
            </div>

            <!-- 启动后在做什么：只写文字，不给百分比（研判本身不确定） -->
            <div class="activity" :data-tone="activityView.tone">
              <span class="act-dot" />
              <div class="act-main">
                <span class="act-label">{{ activityView.label }}</span>
                <span class="act-detail">{{ activityView.detail }}</span>
              </div>
            </div>

            <div class="ctl-actions">
              <button class="btn btn--primary" :disabled="wb.emptyMode" @click="actions.resumeRound()">
                <AppIcon name="play" :size="13" />继续分析
              </button>
              <button class="btn btn--outline" :disabled="wb.emptyMode" @click="actions.pauseRound()">
                <AppIcon name="pause" :size="13" />暂停
              </button>
              <button class="btn btn--danger" :disabled="wb.emptyMode" @click="actions.stopRound()">
                <AppIcon name="stop" :size="13" />终止
              </button>
              <button class="btn btn--outline" @click="actions.openTracking()">
                <AppIcon name="target" :size="13" />追踪任务
              </button>
            </div>

            <div class="ctl-facts num">
              <span>要素 <b>{{ visibleElements.length }}</b></span>
              <span>作业 <b>{{ jobStats.total }}</b></span>
              <span>分析中 <b>{{ jobStats.running }}</b></span>
              <span :class="{ warn: jobStats.review }">待审核 <b>{{ jobStats.review }}</b></span>
            </div>

            <div class="idx-line">
              <AppIcon name="folder" :size="12" />
              <span class="truncate">
                录像索引 <b class="num">{{ indexView.files.toLocaleString() }}</b> 文件 · 有时间
                <b class="num">{{ indexView.parsed.toLocaleString() }}</b> · 匹配 <b class="num">{{ indexView.mapped }}</b>
              </span>
              <button class="btn btn--sm btn--ghost" @click="actions.openRecordingPicker()"><AppIcon name="plus" :size="12" />添加</button>
            </div>
          </aside>

          <div class="col col--right col--features">
            <TargetFeatureBar embedded />
          </div>
        </div>
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

/* ------------------------------ 拖拽条 ------------------------------ */

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
  font-size: 12px;
  color: var(--text-3);
}

.grip-summary {
  font-size: 11.5px;
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

/* ------------------------------ 骨架 ------------------------------ */

.dock-body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  border-top: 1px solid var(--line);
}

.dock-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  padding: 7px 10px 8px;
  overflow: hidden;
}

/* ------------------------------ 两栏骨架 ------------------------------ */

.split {
  display: grid;
  gap: 10px;
  height: 100%;
  min-height: 0;
}

.split--control {
  grid-template-columns: 322px minmax(0, 1fr);
}

.col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  min-height: 0;
}

.col--left {
  padding-right: 10px;
  border-right: 1px solid var(--line);
  overflow-y: auto;
}

.col--features {
  gap: 0;
  min-height: 0;
  overflow: hidden;
}

/* ------------------------------ 任务控制 · 左栏 ------------------------------ */

.round-line {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  font-size: var(--fs-body);
  color: var(--text-2);
}

.round-chip {
  flex: none;
  padding: 0 6px;
  border: 1px solid var(--accent-line);
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent-hi);
  font-size: 12px;
  line-height: 18px;
}

.round-desc {
  color: var(--text-2);
}

.round-window {
  flex: none;
  margin-left: auto;
  color: var(--text-3);
}

/* 启动后在做什么：一段文字，不是进度条 */
.activity {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--line-2);
  border-left: 2px solid var(--line-3);
  border-radius: var(--radius-sm);
  background: var(--bg-1);
}

.act-dot {
  flex: none;
  width: 7px;
  height: 7px;
  margin-top: 6px;
  border-radius: 50%;
  background: var(--text-4);
}

.act-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.act-label {
  font-size: var(--fs-strong);
  font-weight: 600;
  color: var(--text-0);
}

.act-detail {
  font-size: var(--fs-body);
  line-height: 1.55;
  color: var(--text-2);
  text-wrap: pretty;
}

.activity[data-tone="run"] {
  border-left-color: var(--accent);
}

.activity[data-tone="run"] .act-dot {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.activity[data-tone="review"] {
  border-left-color: var(--warn);
  background: var(--warn-soft);
}

.activity[data-tone="review"] .act-dot {
  background: var(--warn);
}

.activity[data-tone="paused"] {
  border-left-color: var(--warn);
}

.activity[data-tone="paused"] .act-dot {
  background: var(--warn);
}

.activity[data-tone="fail"] {
  border-left-color: var(--danger);
}

.activity[data-tone="fail"] .act-dot {
  background: var(--danger);
}

.activity[data-tone="done"] {
  border-left-color: var(--ok);
}

.activity[data-tone="done"] .act-dot {
  background: var(--ok);
}

.ctl-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ctl-actions .btn {
  padding: 0 8px;
}

.ctl-facts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 3px 12px;
  font-size: var(--fs-body);
  color: var(--text-3);
}

.ctl-facts b {
  color: var(--text-0);
  font-weight: 600;
}

.ctl-facts .warn,
.ctl-facts .warn b {
  color: var(--warn);
}

.idx-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-top: auto;
  padding-top: 6px;
  border-top: 1px solid var(--line);
  font-size: var(--fs-note);
  color: var(--text-3);
}

/* 索引数可能很长：只让文字收缩，别把「添加」挤出去 */
.idx-line > .truncate {
  flex: 1;
  min-width: 0;
}

.idx-line > .btn {
  flex: none;
}

.idx-line b {
  color: var(--text-1);
}
</style>