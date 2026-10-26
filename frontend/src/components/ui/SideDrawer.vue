<script setup lang="ts">
import { onBeforeUnmount, watch } from "vue";
import AppIcon from "./AppIcon.vue";

const props = withDefaults(
  defineProps<{
    open: boolean;
    title: string;
    subtitle?: string;
    width?: string;
  }>(),
  { width: "460px" },
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
    <Transition name="drawer">
      <div v-if="open" class="drawer-root" role="presentation" @mousedown.self="emit('close')">
        <aside
          class="drawer-panel"
          :style="{ width }"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <header class="drawer-head">
            <div class="drawer-head-main">
              <h2 class="drawer-title">{{ title }}</h2>
              <p v-if="subtitle" class="drawer-sub">{{ subtitle }}</p>
            </div>
            <slot name="head-extra" />
            <button class="icon-btn drawer-close" aria-label="关闭面板" @click="emit('close')">
              <AppIcon name="close" :size="15" />
            </button>
          </header>
          <div class="drawer-body">
            <slot />
          </div>
          <footer v-if="$slots.footer" class="drawer-foot">
            <slot name="footer" />
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-root {
  position: fixed;
  inset: 0;
  z-index: 110;
  display: flex;
  justify-content: flex-end;
  background: rgba(3, 6, 10, 0.55);
  backdrop-filter: blur(2px);
}

.drawer-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 94vw;
  background: var(--bg-1);
  border-left: 1px solid var(--line-2);
  box-shadow: -24px 0 60px rgba(0, 0, 0, 0.55);
}

.drawer-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 13px 12px 12px 16px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.drawer-head-main {
  flex: 1;
  min-width: 0;
}

.drawer-title {
  font-size: var(--fs-title);
  font-weight: 620;
  color: var(--text-0);
  line-height: 1.3;
}

.drawer-sub {
  margin-top: 2px;
  font-size: 10px;
  color: var(--text-3);
}

.drawer-close {
  flex: none;
  border-color: var(--line-2);
  background: var(--bg-2);
}

.drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--line-3) transparent;
  padding: 12px 14px 16px;
}

.drawer-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-top: 1px solid var(--line);
  background: var(--bg-2);
  flex: none;
}

.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.18s var(--ease);
}

.drawer-enter-active .drawer-panel,
.drawer-leave-active .drawer-panel {
  transition: transform 0.2s var(--ease);
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-from .drawer-panel,
.drawer-leave-to .drawer-panel {
  transform: translateX(24px);
}
</style>
