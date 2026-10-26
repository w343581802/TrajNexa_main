<script setup lang="ts">
import { computed } from "vue";
import { icons, type IconName } from "./icons";

const props = withDefaults(defineProps<{ name: IconName; size?: number; stroke?: number }>(), {
  size: 16,
  stroke: 1.7,
});

const paths = computed(() => icons[props.name]);
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="stroke"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(d, index) in paths" :key="index" :d="d" />
  </svg>
</template>
