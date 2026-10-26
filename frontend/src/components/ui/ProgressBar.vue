<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    value: number;
    tone?: "accent" | "ok" | "warn" | "danger" | "muted";
    height?: number;
    indeterminate?: boolean;
    label?: string;
  }>(),
  { tone: "accent", height: 4, indeterminate: false },
);

const clamp = (n: number) => Math.max(0, Math.min(100, Number.isFinite(n) ? n : 0));
</script>

<template>
  <div
    class="progress"
    :data-tone="props.tone"
    :class="{ 'is-indeterminate': props.indeterminate }"
    :style="{ height: `${props.height}px` }"
    role="progressbar"
    :aria-valuenow="indeterminate ? undefined : clamp(value)"
    aria-valuemin="0"
    aria-valuemax="100"
    :aria-label="label ?? '进度'"
  >
    <i class="progress-fill" :style="{ width: indeterminate ? '40%' : `${clamp(value)}%` }" />
  </div>
</template>

<style scoped>
.progress {
  position: relative;
  width: 100%;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.055);
  overflow: hidden;
}

.progress-fill {
  display: block;
  height: 100%;
  border-radius: 99px;
  background: linear-gradient(90deg, #3f7cf0, #6ba2ff);
  transition: width 0.4s var(--ease);
}

.progress[data-tone="ok"] .progress-fill {
  background: linear-gradient(90deg, #1e9d6d, #3ddba0);
}

.progress[data-tone="warn"] .progress-fill {
  background: linear-gradient(90deg, #c98b2e, #f0b45a);
}

.progress[data-tone="danger"] .progress-fill {
  background: linear-gradient(90deg, #c25049, #f2706a);
}

.progress[data-tone="muted"] .progress-fill {
  background: linear-gradient(90deg, #3a4658, #5a6a80);
}

.progress.is-indeterminate .progress-fill {
  animation: progress-slide 1.5s var(--ease) infinite;
}

@keyframes progress-slide {
  0% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(260%);
  }
}
</style>
