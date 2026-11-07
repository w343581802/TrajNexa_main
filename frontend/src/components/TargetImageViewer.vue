<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import TargetCrop, { type CropBox } from "./TargetCrop.vue";

/** 目标截图大图查看窗：左侧按目标框裁剪，右侧完整图片。无遮罩，不拦截窗外点击。 */
const props = withDefaults(defineProps<{
  src: string;
  crop?: CropBox | null;
  title: string;
  meta?: string;
  cropLabel?: string;
  fullLabel?: string;
}>(), {
  crop: null,
  meta: "",
  cropLabel: "截图大图",
  fullLabel: "完整图片",
});

const emit = defineEmits<{ close: [] }>();

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));
</script>

<template>
  <Teleport to="body">
    <div class="iv" role="dialog" aria-modal="false" :aria-label="`${props.title}大图`">
      <section class="iv-panel" @click.stop>
        <header class="iv-head">
          <div class="iv-title">
            <h3>{{ props.title }}</h3>
            <p v-if="props.meta">{{ props.meta }}</p>
          </div>
          <button class="iv-close" type="button" aria-label="关闭大图" title="关闭（Esc）" @click="emit('close')">
            <AppIcon name="close" :size="15" />
          </button>
        </header>

        <div class="iv-body">
          <figure class="iv-pane">
            <TargetCrop
              class="iv-crop"
              :src="props.src"
              :box="props.crop"
              :alt="props.cropLabel"
              empty-text="没有留存截图"
              fit="contain"
            />
            <figcaption>{{ props.cropLabel }}</figcaption>
          </figure>
          <figure class="iv-pane">
            <TargetCrop
              class="iv-full"
              :src="props.src"
              :box="null"
              :alt="props.fullLabel"
              empty-text="没有留存截图"
              fit="contain"
            />
            <figcaption>{{ props.fullLabel }}</figcaption>
          </figure>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.iv {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 22px;
  pointer-events: none;
}

.iv-panel {
  display: flex;
  flex-direction: column;
  width: min(1360px, 94vw);
  height: min(800px, 84vh);
  min-height: 0;
  padding: 12px;
  border: 1px solid var(--line-3);
  border-radius: 10px;
  background: #0a1017;
  box-shadow: 0 22px 70px rgba(0, 0, 0, 0.62);
  pointer-events: auto;
}

.iv-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex: none;
  padding: 2px 2px 10px;
  border-bottom: 1px solid var(--line);
}

.iv-title {
  min-width: 0;
}

.iv-title h3 {
  margin: 0;
  color: var(--text-0);
  font-size: 14px;
  font-weight: 600;
}

.iv-title p {
  margin: 3px 0 0;
  color: var(--text-3);
  font-size: 10.5px;
}

.iv-close {
  display: grid;
  place-items: center;
  flex: none;
  width: 26px;
  height: 26px;
  border: 1px solid var(--line-2);
  border-radius: 5px;
  background: #111a24;
  color: var(--text-2);
}

.iv-close:hover {
  border-color: var(--line-3);
  color: var(--text-0);
}

.iv-body {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 12px;
  flex: 1;
  min-height: 0;
  padding-top: 10px;
}

.iv-pane {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  min-height: 0;
  margin: 0;
}

.iv-pane figcaption {
  flex: none;
  color: var(--text-3);
  font-size: 10.5px;
}

.iv-crop,
.iv-full {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: #05090e;
}

@media (max-width: 900px) {
  .iv { padding: 10px; }
  .iv-panel { height: 90vh; padding: 10px; }
  .iv-body { grid-template-columns: minmax(0, 1fr); overflow-y: auto; }
  .iv-pane { min-height: 220px; }
}
</style>
