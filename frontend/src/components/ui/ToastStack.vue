<script setup lang="ts">
import AppIcon from "./AppIcon.vue";
import type { IconName } from "./icons";
import { ui, type ToastTone } from "../../store/workbench";

const iconFor = (tone: ToastTone): IconName =>
  tone === "ok" ? "check" : tone === "warn" ? "warning" : tone === "danger" ? "x-circle" : "info";
</script>

<template>
  <Teleport to="body">
    <div class="toasts" role="status" aria-live="polite">
      <TransitionGroup name="toast">
        <div v-for="t in ui.toasts" :key="t.id" class="toast" :data-tone="t.tone">
          <AppIcon :name="iconFor(t.tone)" :size="14" />
          <span>{{ t.text }}</span>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toasts {
  position: fixed;
  top: 64px;
  right: 18px;
  z-index: 200;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 380px;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line-2);
  background: #101a26;
  color: var(--text-1);
  font-size: var(--fs-label);
  box-shadow: var(--shadow-pop);
}

.toast[data-tone="ok"] {
  border-color: rgba(47, 207, 146, 0.34);
  color: #a9f0d2;
}

.toast[data-tone="warn"] {
  border-color: rgba(240, 180, 90, 0.34);
  color: #f8d9a6;
}

.toast[data-tone="danger"] {
  border-color: rgba(242, 112, 106, 0.34);
  color: #ffb3ae;
}

.toast[data-tone="info"] {
  border-color: var(--accent-line);
  color: #b6cffd;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.2s var(--ease), transform 0.2s var(--ease);
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(12px);
}
</style>
