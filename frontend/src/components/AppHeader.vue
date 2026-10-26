<script setup lang="ts">
import AppIcon from "./ui/AppIcon.vue";
import StatusChip from "./ui/StatusChip.vue";
import { snapshot } from "../data/mock";
import { actions, roundView, wb } from "../store/workbench";
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none">
          <path d="M5.5 17.5 11 8.5l7 5.5" stroke="url(#brand-line)" stroke-width="1.6" stroke-linecap="round" />
          <circle cx="5.5" cy="17.5" r="2.4" fill="#0b1119" stroke="#6fa5ff" stroke-width="1.5" />
          <circle cx="11" cy="8.5" r="2.9" fill="#6fa5ff" />
          <circle cx="18" cy="14" r="2.2" fill="#0b1119" stroke="#2fcf92" stroke-width="1.5" />
          <defs>
            <linearGradient id="brand-line" x1="5" y1="17" x2="18" y2="9" gradientUnits="userSpaceOnUse">
              <stop stop-color="#4d8dff" stop-opacity="0.75" />
              <stop offset="1" stop-color="#2fcf92" stop-opacity="0.75" />
            </linearGradient>
          </defs>
        </svg>
      </div>
      <div class="brand-text">
        <strong>TrajNexa</strong>
        <span>迹枢 · 主系统</span>
      </div>
    </div>

    <div class="topbar-sep" />

    <div class="task">
      <span class="task-label">当前任务</span>
      <span class="task-name truncate" :title="wb.taskName">{{ wb.taskName }}</span>
      <StatusChip v-if="!wb.emptyMode" :label="`研判中 · ${roundView.id}`" tone="info" dot />
      <StatusChip v-else label="空任务" tone="muted" dot />
      <span class="task-id mono">{{ wb.taskId }}</span>
    </div>

    <div class="topbar-grow" />

    <div class="meta meta--snapshot" title="空间点位 / FOV 均来自 TrajNexa_Map 已发布快照，主系统只读消费">
      <AppIcon name="map" :size="14" />
      <span>Map 快照 <b class="mono">{{ snapshot.revision }}</b></span>
      <span class="meta-sub">{{ snapshot.publishedAgo }} · 只读</span>
    </div>

    <div class="meta meta--auth" title="当前任务的授权范围；越权资源不会出现在可启动的任务选择中">
      <AppIcon name="shield" :size="14" />
      <span>{{ wb.authorized }}</span>
    </div>

    <button
      class="scene-switch"
      :class="{ 'is-on': wb.emptyMode }"
      :aria-pressed="wb.emptyMode"
      title="切换为「未选择录像目录」的空任务工作台，用于核对空态"
      @click="actions.switchScene(!wb.emptyMode)"
    >
      <span class="scene-track"><i /></span>
      {{ wb.emptyMode ? "示例任务" : "空任务态" }}
    </button>

    <div class="user">
      <span class="avatar">李</span>
      <span class="user-text">
        <strong>李调查员</strong>
        <span>研判员 · 可审核</span>
      </span>
    </div>

    <button class="icon-btn" aria-label="设置" title="设置">
      <AppIcon name="settings" />
    </button>
    <button class="icon-btn" aria-label="帮助" title="帮助">
      <AppIcon name="help" />
    </button>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: 10px;
  height: var(--header-h);
  padding: 0 12px 0 14px;
  background: linear-gradient(180deg, #0c131c, #090e15);
  border-bottom: 1px solid var(--line);
  flex: none;
  min-width: 0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  flex: none;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: radial-gradient(120% 120% at 20% 10%, #17273b, #0b1119 70%);
  border: 1px solid #22334a;
  box-shadow: 0 1px 0 rgba(255, 255, 255, 0.05) inset;
}

.brand-mark svg {
  width: 20px;
  height: 20px;
}

.brand-text {
  display: grid;
  line-height: 1.15;
}

.brand-text strong {
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--text-0);
}

.brand-text span {
  font-size: 9.5px;
  letter-spacing: 0.1em;
  color: var(--text-3);
}

.topbar-sep {
  width: 1px;
  height: 22px;
  background: var(--line-2);
  margin: 0 4px;
  flex: none;
}

.task {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
}

.task-label {
  font-size: var(--fs-micro);
  letter-spacing: 0.08em;
  color: var(--text-3);
  flex: none;
}

.task-name {
  font-size: var(--fs-strong);
  font-weight: 600;
  color: var(--text-0);
  max-width: 26ch;
}

.task-id {
  font-size: 10px;
  color: var(--text-4);
  flex: none;
}

.topbar-grow {
  flex: 1;
  min-width: 6px;
}

.meta {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 9px;
  border-radius: 999px;
  border: 1px solid var(--line-2);
  background: rgba(255, 255, 255, 0.018);
  font-size: var(--fs-note);
  color: var(--text-2);
  white-space: nowrap;
  flex: none;
}

.meta b {
  font-weight: 600;
  color: var(--text-1);
}

.meta-sub {
  color: var(--text-3);
  font-size: 10px;
}

.meta--snapshot :deep(svg) {
  color: var(--ok);
}

.meta--auth :deep(svg) {
  color: var(--accent-hi);
}

.scene-switch {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 9px 0 7px;
  border-radius: 999px;
  border: 1px solid var(--line-2);
  color: var(--text-3);
  font-size: var(--fs-note);
  flex: none;
  transition: color var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.scene-switch:hover {
  color: var(--text-1);
  border-color: var(--line-3);
}

.scene-track {
  position: relative;
  width: 20px;
  height: 11px;
  border-radius: 99px;
  background: var(--bg-4);
  border: 1px solid var(--line-2);
}

.scene-track i {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-3);
  transition: transform var(--dur) var(--ease), background var(--dur) var(--ease);
}

.scene-switch.is-on {
  color: #b6cffd;
  border-color: var(--accent-line);
}

.scene-switch.is-on .scene-track {
  background: var(--accent-soft);
  border-color: var(--accent-line);
}

.scene-switch.is-on .scene-track i {
  background: var(--accent-hi);
  transform: translateX(9px);
}

.user {
  display: flex;
  align-items: center;
  gap: 7px;
  padding-left: 6px;
  flex: none;
}

.avatar {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: linear-gradient(160deg, #2b4768, #16283c);
  border: 1px solid #33506f;
  color: #cfe2ff;
  font-size: 11px;
  font-weight: 600;
}

.user-text {
  display: grid;
  line-height: 1.2;
}

.user-text strong {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-1);
}

.user-text span {
  font-size: 9px;
  color: var(--text-3);
}

@media (max-width: 1460px) {
  .meta--snapshot,
  .meta--auth,
  .user-text {
    display: none;
  }
}

@media (max-width: 1180px) {
  .task-id,
  .scene-switch {
    display: none;
  }
}
</style>
