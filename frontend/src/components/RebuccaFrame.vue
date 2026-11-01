<script setup lang="ts">
import { computed, ref } from "vue";

/**
 * 小模型 / 大模型页面直接嵌 Rebucca 自己的管理页，不再在 TrajNexa 里重复实现一套。
 * Rebucca 的 Django 已带增删改、权重上传、探测与在线测试，且允许被 iframe 嵌入
 * （X-Frame-Options: ALLOWALL）。
 * 地址优先级：URL 参数 ?rebucca= > 构建期 VITE_REBUCCA_WEB_URL > 本机默认 10001。
 */
const props = defineProps<{ title: string; path: string; hint?: string }>();

function pickBase(): string {
  const fromQuery = typeof window !== "undefined"
    ? new URLSearchParams(window.location.search).get("rebucca") : null;
  const fromEnv = (import.meta.env.VITE_REBUCCA_WEB_URL as string | undefined)?.trim();
  return (fromQuery || fromEnv || "http://127.0.0.1:10001").replace(/\/+$/, "");
}
const base = ref(pickBase());
const src = computed(() => base.value + props.path);
const frameKey = ref(0);
function reload() { frameKey.value += 1; }
function openExternal() { window.open(src.value, "_blank", "noopener"); }
</script>

<template>
  <section class="rebucca-frame" aria-label="Rebucca 原生管理页">
    <header class="frame-head">
      <div class="frame-title">
        <h2>{{ props.title }}</h2>
        <p v-if="props.hint">{{ props.hint }}</p>
      </div>
      <div class="frame-actions">
        <code class="frame-url">{{ src }}</code>
        <button type="button" class="ghost-btn" @click="reload">重新加载</button>
        <button type="button" class="ghost-btn" @click="openExternal">新标签打开</button>
      </div>
    </header>
    <p class="frame-tip">
      这里显示的是 Rebucca 自己的页面。若出现登录页，用它自己的账号登录后即可操作；
      登录态属于 127.0.0.1:10001，TrajNexa 不保存也不代理 Rebucca 的会话。
    </p>
    <iframe :key="frameKey" :src="src" class="frame-body"
      :title="props.title" referrerpolicy="no-referrer" allow="clipboard-read; clipboard-write" />
  </section>
</template>

<style scoped>
.rebucca-frame { display: flex; flex-direction: column; gap: 10px; height: 100%;
  min-height: 0; padding: 14px 16px; box-sizing: border-box; }
.frame-head { display: flex; align-items: flex-start; justify-content: space-between;
  gap: 12px; flex-wrap: wrap; }
.frame-title h2 { margin: 0; font-size: 15px; font-weight: 600; }
.frame-title p { margin: 3px 0 0; font-size: 12px; opacity: .72; }
.frame-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.frame-url { font-size: 11px; opacity: .7; background: rgba(127,127,127,.12);
  padding: 3px 6px; border-radius: 4px; }
.ghost-btn { font-size: 12px; padding: 4px 9px; border-radius: 5px; cursor: pointer;
  border: 1px solid rgba(127,127,127,.35); background: transparent; color: inherit; }
.ghost-btn:hover { background: rgba(127,127,127,.14); }
.frame-tip { margin: 0; font-size: 12px; opacity: .68; line-height: 1.5; }
.frame-body { flex: 1 1 auto; width: 100%; min-height: 0; border: 1px solid rgba(127,127,127,.28);
  border-radius: 8px; background: #fff; }
</style>
