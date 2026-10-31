<script setup lang="ts">
import AppIcon from "./ui/AppIcon.vue";
import { ui } from "../store/workbench";
</script>

<template>
  <nav class="toolbar" aria-label="小工具">
    <span class="toolbar-label">工具</span>
    <button
      class="tool"
      type="button"
      title="读取视频开头几秒画面，OCR 摄像头名称和时间，重命名文件并生成表格"
      @click="ui.videoNamingOpen = true"
    >
      <AppIcon name="video" :size="14" />
      <span>视频命名</span>
      <span class="tool-tag">OCR</span>
    </button>
  </nav>
</template>

<style scoped>
.toolbar {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 100%;
  padding: 0;
  background: transparent;
  flex: none;
  min-width: 0;
}

.toolbar-label {
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-4);
  flex: none;
}

.tool {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 26px;
  padding: 0 9px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-2);
  font-size: 11.5px;
  cursor: pointer;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease),
    color var(--dur) var(--ease);
}

.tool:hover {
  background: var(--bg-2);
  border-color: var(--line-2);
  color: var(--text-0);
}

.tool :deep(svg) {
  color: var(--accent-hi);
}

.tool-tag {
  height: 15px;
  padding: 0 4px;
  border-radius: 3px;
  border: 1px solid var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent-hi);
  font-size: 8.5px;
  font-weight: 700;
  line-height: 13px;
  letter-spacing: 0.04em;
}
</style>
