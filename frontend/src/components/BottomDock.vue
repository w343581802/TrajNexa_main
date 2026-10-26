<script setup lang="ts">
import { computed, onBeforeUnmount } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import JobPanel from "./JobPanel.vue";
import TaskControlPanel from "./TaskControlPanel.vue";
import { indexView, roundView, ui, visibleElements, visibleJobs, wb } from "../store/workbench";

const collapsedSummary = computed(() => {
  if (wb.emptyMode) return "未选择录像目录 · 无目标要素 · 未启动研判";
  return `索引 ${indexView.value.progress}% · 要素 ${visibleElements.value.length} · 轮次 ${roundView.value.id} 分析中 ${roundView.value.progress}% · 作业 ${visibleJobs.value.length}`;
});

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
  <section class="dock" :class="{ 'is-collapsed': ui.dockCollapsed }" :style="{ height: ui.dockCollapsed ? '30px' : `${ui.dockHeight}px` }">
    <div
      class="dock-grip"
      role="separator"
      aria-orientation="horizontal"
      :aria-label="`任务操作台高度 ${ui.dockHeight} 像素，可拖动调整`"
      @pointerdown="startResize"
    >
      <span class="grip-label">任务操作台</span>
      <span class="grip-bar" />
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
      <TaskControlPanel />
      <JobPanel />
    </div>
  </section>
</template>

<style scoped>
.dock {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--bg-1);
  border-top: 1px solid var(--line-2);
  box-shadow: 0 -10px 30px rgba(0, 0, 0, 0.35);
  flex: none;
  position: relative;
  z-index: 8;
}

.dock-grip {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 20px;
  padding: 0 8px 0 12px;
  flex: none;
  cursor: ns-resize;
  background: linear-gradient(180deg, #0d141e, #0b121a);
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  touch-action: none;
}

.grip-label {
  font-size: 9px;
  letter-spacing: 0.1em;
  color: var(--text-4);
  white-space: nowrap;
  flex: none;
}

.grip-bar {
  position: absolute;
  top: 3px;
  left: 50%;
  width: 46px;
  height: 3px;
  transform: translateX(-50%);
  border-radius: 99px;
  background: var(--line-3);
  opacity: 0.7;
}

.dock-grip:hover .grip-bar {
  background: var(--accent-line);
  opacity: 1;
}

.grip-summary {
  font-size: 9.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.grip-toggle {
  margin-left: auto;
  width: 20px;
  height: 16px;
  flex: none;
  cursor: pointer;
}

.dock-body {
  display: grid;
  grid-template-columns: minmax(440px, 36%) minmax(0, 1fr);
  min-height: 0;
  flex: 1;
  overflow: hidden;
}

@media (max-width: 1180px) {
  .dock-body {
    grid-template-columns: minmax(0, 46%) minmax(0, 54%);
  }
}
</style>
