<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import type { DockThumbMode, JobCardView } from "../../store/workbench";

const props = defineProps<{
  card: JobCardView;
  /** 统一由全局开关决定：缩略图 / 缩略视频 */
  mode: DockThumbMode;
  active: boolean;
}>();

const emit = defineEmits<{ (event: "open"): void }>();

const job = computed(() => props.card.job);

/** 状态色只管界面：待审核=琥珀、失败=红、已完成=绿、分析中=蓝 */
const tone = computed(() => {
  if (props.card.review) return "warn";
  switch (job.value.status) {
    case "failed":
      return "danger";
    case "done":
      return "ok";
    case "nodetect":
    case "canceled":
    case "queued":
      return "muted";
    default:
      return "info";
  }
});
</script>

<template>
  <button
    type="button"
    class="job-card"
    :class="{ 'is-review': card.review, 'is-active': active }"
    :aria-label="`${card.cameraName} ${card.timeText} ${card.statusText}`"
    @click="emit('open')"
  >
    <span class="jc-thumb" :data-mode="mode">
      <img v-if="mode === 'image' && card.thumbUrl" class="jc-media" :src="card.thumbUrl" :alt="`${card.cameraName} 缩略帧`" />
      <video
        v-else-if="mode === 'video' && card.clipUrl"
        class="jc-media"
        :src="card.clipUrl"
        muted
        playsinline
        preload="metadata"
      />
      <span v-else class="jc-placeholder">
        <AppIcon :name="mode === 'video' ? 'video' : 'camera'" :size="16" />
        <i>{{ mode === "video" ? "缩略视频待生成" : "缩略帧待生成" }}</i>
      </span>
      <span v-if="mode === 'video'" class="jc-play"><AppIcon name="play" :size="10" /></span>
      <span v-if="card.duration" class="jc-duration num">{{ card.duration }}</span>
    </span>

    <span class="jc-body">
      <span class="jc-name truncate" :title="card.cameraName">{{ card.cameraName }}</span>
      <span class="jc-time num">{{ card.timeText }}</span>
      <span class="jc-state" :data-tone="tone">
        <AppIcon v-if="card.review" name="warning" :size="10" />
        {{ card.review ? "待人工审核" : card.statusText }}
      </span>
    </span>
  </button>
</template>

<style scoped>
.job-card {
  position: relative;
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  padding: 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--bg-2);
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.job-card:hover {
  border-color: var(--accent-line);
  background: var(--bg-3);
}

.job-card:focus-visible {
  outline: 2px solid var(--accent-line);
  outline-offset: 1px;
}

.job-card.is-active {
  border-color: var(--accent);
}

/* 待人工审核：靠前 + 高亮（不做闪烁，避免长期盯屏被干扰） */
.job-card.is-review {
  border-color: rgba(240, 180, 90, 0.6);
  background: linear-gradient(180deg, rgba(240, 180, 90, 0.11), var(--bg-2) 70%);
  box-shadow: inset 2px 0 0 var(--warn);
}

.job-card.is-review:hover {
  border-color: var(--warn);
  background: linear-gradient(180deg, rgba(240, 180, 90, 0.16), var(--bg-3) 70%);
}

.jc-thumb {
  position: relative;
  display: block;
  width: 96px;
  height: 54px;
  overflow: hidden;
  border-radius: var(--radius-xs);
  background: var(--bg-0);
  border: 1px solid var(--line);
}

.jc-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.jc-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: var(--text-4);
}

.jc-placeholder i {
  font-style: normal;
  font-size: 10px;
  line-height: 1.1;
}

.jc-play {
  position: absolute;
  left: 4px;
  bottom: 4px;
  display: grid;
  place-items: center;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: rgba(6, 10, 16, 0.66);
  color: var(--text-0);
}

.jc-duration {
  position: absolute;
  right: 3px;
  bottom: 3px;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(6, 10, 16, 0.72);
  color: var(--text-1);
  font-size: 10px;
  line-height: 14px;
}

.jc-body {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.jc-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-0);
}

.jc-time {
  font-size: 11.5px;
  color: var(--text-3);
}

.jc-state {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  line-height: 1.2;
  color: var(--text-2);
}

.jc-state[data-tone="warn"] {
  color: var(--warn);
}

.jc-state[data-tone="danger"] {
  color: var(--danger);
}

.jc-state[data-tone="ok"] {
  color: var(--ok);
}

.jc-state[data-tone="info"] {
  color: var(--accent-hi);
}
</style>
