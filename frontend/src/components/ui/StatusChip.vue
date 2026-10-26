<script setup lang="ts">
withDefaults(
  defineProps<{
    tone?: "ok" | "warn" | "danger" | "info" | "violet" | "cyan" | "muted";
    label: string;
    dot?: boolean;
  }>(),
  { tone: "muted", dot: false },
);
</script>

<template>
  <span class="chip" :data-tone="tone">
    <i v-if="dot" class="dot" />
    {{ label }}
  </span>
</template>
