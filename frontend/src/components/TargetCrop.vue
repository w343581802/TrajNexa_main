<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

export type CropBox = { x: number; y: number; w: number; h: number };

const props = withDefaults(defineProps<{
  src?: string | null;
  /** 归一化框：x/y/w/h，0–1。有框时按框铺满容器，不拉伸。 */
  box?: CropBox | null;
  alt?: string;
  emptyText?: string;
  /** 框外留一点上下文，避免人脸/车轮贴边被切 */
  padding?: number;
  /** cover 填满容器（会裁切）；contain 完整放在容器内（可能留边） */
  fit?: "cover" | "contain";
}>(), {
  src: "",
  box: null,
  alt: "目标截图",
  emptyText: "没有留存截图",
  padding: 0.06,
  fit: "cover",
});

const emit = defineEmits<{ error: [] }>();

const host = ref<HTMLElement | null>(null);
const natural = ref({ w: 0, h: 0 });
const size = ref({ w: 0, h: 0 });
const failed = ref(false);
let observer: ResizeObserver | null = null;

const imageReady = computed(() => natural.value.w > 0 && natural.value.h > 0);
const empty = computed(() => !props.src || failed.value);

function onLoad(event: Event) {
  const img = event.target as HTMLImageElement;
  natural.value = { w: img.naturalWidth, h: img.naturalHeight };
}

function onError() {
  failed.value = true;
  emit("error");
}

function clampBox(box: CropBox): CropBox {
  const x = Math.min(1, Math.max(0, box.x));
  const y = Math.min(1, Math.max(0, box.y));
  const w = Math.min(1 - x, Math.max(0.02, box.w));
  const h = Math.min(1 - y, Math.max(0.02, box.h));
  return { x, y, w, h };
}

/**
 * cover 裁切：把归一化框的中心对齐容器中心，按「框宽/框高」中更小的缩放比放大；
 * contain 则取更大的缩放比（即完整框进容器），可能留边但绝不截断目标。
 * 两种方式都不拉伸图片本身。
 */
const imageStyle = computed(() => {
  if (!props.box || !imageReady.value || !size.value.w || !size.value.h) return {};
  const box = clampBox(props.box);
  const pad = Math.max(0, Math.min(0.2, props.padding)) * Math.max(box.w, box.h);
  const x = Math.max(0, box.x - pad);
  const y = Math.max(0, box.y - pad);
  const w = Math.min(1 - x, box.w + pad * 2);
  const h = Math.min(1 - y, box.h + pad * 2);
  const scaleX = size.value.w / (w * natural.value.w);
  const scaleY = size.value.h / (h * natural.value.h);
  const scale = props.fit === "contain" ? Math.min(scaleX, scaleY) : Math.max(scaleX, scaleY);
  const width = natural.value.w * scale;
  const height = natural.value.h * scale;
  const centerX = (x + w / 2) * natural.value.w * scale;
  const centerY = (y + h / 2) * natural.value.h * scale;
  return {
    width: `${width}px`,
    height: `${height}px`,
    left: `${size.value.w / 2 - centerX}px`,
    top: `${size.value.h / 2 - centerY}px`,
  };
});

watch(() => props.src, () => {
  natural.value = { w: 0, h: 0 };
  failed.value = false;
});

onMounted(() => {
  if (!host.value) return;
  observer = new ResizeObserver((entries) => {
    const rect = entries[0]?.contentRect;
    if (rect) size.value = { w: rect.width, h: rect.height };
  });
  observer.observe(host.value);
  const rect = host.value.getBoundingClientRect();
  size.value = { w: rect.width, h: rect.height };
});

onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div ref="host" class="crop" :class="{ 'is-empty': empty }">
    <img
      v-if="!empty"
      :src="src ?? ''"
      :alt="alt"
      :style="props.box && imageReady ? imageStyle : undefined"
      :class="{ 'is-fit': !props.box || !imageReady, 'is-contain': props.fit === 'contain' }"
      loading="lazy"
      @load="onLoad"
      @error="onError"
    />
    <span v-else class="crop-empty">{{ failed ? "证据图加载失败" : emptyText }}</span>
  </div>
</template>

<style scoped>
.crop {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #0a0f15;
}

.crop img {
  position: absolute;
  top: 0;
  left: 0;
  max-width: none;
  pointer-events: none;
}

.crop img.is-fit {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
}

.crop img.is-fit.is-contain {
  object-fit: contain;
}

.crop.is-empty {
  display: grid;
  place-items: center;
  border: 1px dashed var(--line-2);
  border-radius: 6px;
  background: #0a0f15;
}

.crop-empty {
  padding: 0 8px;
  color: var(--text-4);
  font-size: 10px;
  line-height: 1.5;
  text-align: center;
}
</style>