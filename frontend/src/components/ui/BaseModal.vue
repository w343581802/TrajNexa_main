<script setup lang="ts">
import { onBeforeUnmount, watch } from "vue";
import AppIcon from "./AppIcon.vue";

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    subtitle?: string;
    width?: "sm" | "md" | "lg" | "xl";
    height?: string;
    bodyClass?: string;
    closeOnBackdrop?: boolean;
  }>(),
  { width: "lg", height: "auto", closeOnBackdrop: true },
);

const emit = defineEmits<{ (event: "close"): void }>();

function onKey(event: KeyboardEvent) {
  if (event.key === "Escape") emit("close");
}

watch(
  () => props.open,
  (open) => {
    if (open) window.addEventListener("keydown", onKey);
    else window.removeEventListener("keydown", onKey);
  },
  { immediate: true },
);

onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <Transition name="modal">
      <div v-if="open" class="modal-root" role="presentation" @mousedown.self="closeOnBackdrop && emit('close')">
        <div
          class="modal-panel"
          :class="`modal-panel--${width}`"
          :style="{ height }"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <header class="modal-head">
            <div class="modal-head-main">
              <h2 class="modal-title">{{ title }}</h2>
              <p v-if="subtitle" class="modal-sub">{{ subtitle }}</p>
            </div>
            <div class="modal-head-extra">
              <slot name="head-extra" />
            </div>
            <button class="icon-btn modal-close" aria-label="关闭弹窗" @click="emit('close')">
              <AppIcon name="close" :size="16" />
            </button>
          </header>
          <div class="modal-body" :class="bodyClass">
            <slot />
          </div>
          <footer v-if="$slots.footer" class="modal-foot">
            <slot name="footer" />
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.modal-root {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 22px;
  background: rgba(3, 6, 10, 0.72);
  backdrop-filter: blur(4px);
}

.modal-panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-height: 100%;
  min-height: 0;
  background: var(--bg-1);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  box-shadow: 0 40px 90px rgba(0, 0, 0, 0.66), 0 0 0 1px rgba(255, 255, 255, 0.02) inset;
  overflow: hidden;
}

.modal-panel--sm {
  max-width: 540px;
}

.modal-panel--md {
  max-width: 760px;
}

.modal-panel--lg {
  max-width: 1080px;
}

.modal-panel--xl {
  max-width: 1560px;
}

.modal-head {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 14px 16px 13px 20px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.022), transparent);
  flex: none;
}

.modal-head-main {
  min-width: 0;
  flex: 1;
}

.modal-title {
  font-size: var(--fs-xl);
  font-weight: 620;
  letter-spacing: 0.01em;
  color: var(--text-0);
  line-height: 1.25;
}

.modal-sub {
  margin-top: 3px;
  font-size: var(--fs-note);
  color: var(--text-2);
  line-height: 1.45;
}

.modal-head-extra {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  padding-top: 2px;
}

.modal-close {
  flex: none;
  margin-top: -1px;
  border-color: var(--line-2);
  background: var(--bg-2);
}

.modal-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.modal-foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-top: 1px solid var(--line);
  background: var(--bg-2);
}

/* 过渡 */
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.18s var(--ease);
}

.modal-enter-active .modal-panel,
.modal-leave-active .modal-panel {
  transition: transform 0.2s var(--ease), opacity 0.18s var(--ease);
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from .modal-panel,
.modal-leave-to .modal-panel {
  transform: translateY(10px) scale(0.994);
  opacity: 0;
}
</style>
