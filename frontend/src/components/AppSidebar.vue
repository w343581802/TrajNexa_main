<script setup lang="ts">
import { ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";

type SidebarSection = "workspace" | "recordings" | "history" | "small-models" | "vision-models" | "model-test";
const props = defineProps<{ active: SidebarSection }>();
const emit = defineEmits<{ (event: "select", section: SidebarSection): void }>();

function initialCollapsed(): boolean {
  try { return localStorage.getItem("trajnexa.sidebar.collapsed") === "1"; }
  catch { return false; }
}
const collapsed = ref(initialCollapsed());
function toggle() {
  collapsed.value = !collapsed.value;
  try { localStorage.setItem("trajnexa.sidebar.collapsed", collapsed.value ? "1" : "0"); }
  catch { /* optional local display preference */ }
}

const nav = [
  { id: "workspace" as const, label: "研判工作台", desc: "地图与多镜头任务", icon: "target" as const },
  { id: "recordings" as const, label: "录像资源", desc: "目录登记与文件核对", icon: "folder" as const },
  { id: "history" as const, label: "历史记录", desc: "已归档的研判轮次", icon: "clock" as const },
  { id: "small-models" as const, label: "小模型管理", desc: "Rebucca 检测模型", icon: "layers" as const },
  { id: "vision-models" as const, label: "大模型管理", desc: "视觉模型与外观分析", icon: "scan" as const },
  { id: "model-test" as const, label: "模型测试", desc: "图片多模型对比与 ROI 提示词实验", icon: "scan" as const },
];
</script>

<template>
  <aside class="app-sidebar" :class="{ 'is-collapsed': collapsed }" aria-label="主功能菜单">
    <div class="side-head">
      <span v-if="!collapsed" class="side-title">工作空间</span>
      <button type="button" class="collapse" :title="collapsed ? '展开侧栏' : '收起侧栏'"
        :aria-label="collapsed ? '展开侧栏' : '收起侧栏'" :aria-expanded="!collapsed"
        @click="toggle">
        <AppIcon :name="collapsed ? 'chevron-right' : 'chevron-left'" :size="15" />
      </button>
    </div>
    <nav class="side-nav" aria-label="工作台及模型管理">
      <template v-for="(item, index) in nav" :key="item.id">
        <div v-if="index === 3" class="side-divider">
          <span v-if="!collapsed">AI 模型中心</span>
        </div>
        <button type="button" class="side-link" :class="{ 'is-active': props.active === item.id }"
          :title="collapsed ? item.label : item.desc"
          :aria-current="props.active === item.id ? 'page' : undefined"
          @click="emit('select', item.id)">
          <AppIcon :name="item.icon" :size="17" />
          <span v-if="!collapsed" class="link-text">{{ item.label }}</span>
          <AppIcon v-if="!collapsed && props.active === item.id" name="chevron-right" :size="13" />
        </button>
      </template>
    </nav>
    <div v-if="!collapsed" class="side-foot">
      <span class="side-foot-tag">M5 · 模型实验</span>
      <p>模型实验统一进入「模型测试」；业务工作台使用默认模型组合。</p>
    </div>
  </aside>
</template>

<style scoped>
.app-sidebar {
  width: 198px;
  flex: 0 0 198px;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #0c1520, #0a111a 75%);
  border-right: 1px solid var(--line-2);
  transition: width .16s var(--ease), flex-basis .16s var(--ease);
}
.app-sidebar.is-collapsed { width: 54px; flex-basis: 54px; }
.side-head { height: 48px; flex: none; display: flex; align-items: center; justify-content: space-between;
  padding: 0 10px 0 17px; border-bottom: 1px solid var(--line); }
.side-title { font-size: 10px; color: var(--text-3); letter-spacing: .12em; font-weight: 700; }
.collapse { display: grid; place-items: center; width: 27px; height: 27px; cursor: pointer;
  color: var(--text-3); border: 1px solid var(--line-2); border-radius: 6px; }
.collapse:hover { color: var(--text-0); border-color: var(--accent-line); }
.side-nav { display: flex; flex-direction: column; gap: 5px; padding: 11px 8px; }
.side-divider { display: flex; align-items: center; gap: 7px; height: 37px; margin-top: 10px;
  color: var(--text-4); font-size: 9px; font-weight: 700; letter-spacing: .08em;
  padding: 0 8px; border-top: 1px solid var(--line); }
.side-link { display: flex; gap: 11px; align-items: center; text-align: left;
  width: 100%; min-height: 38px; padding: 0 10px; border-radius: 7px;
  color: var(--text-2); cursor: pointer; font-size: 12px; border: 1px solid transparent;
  transition: background .13s ease, border-color .13s ease; }
.side-link:hover { background: var(--bg-3); color: var(--text-0); }
.side-link.is-active { background: rgba(59, 129, 179, .16); color: #b7e6ff; border-color: #28668f; }
.side-link :deep(svg) { flex: none; }
.link-text { flex: 1; }
.side-foot { margin-top: auto; border-top: 1px solid var(--line); padding: 15px 14px 18px; }
.side-foot-tag { color: var(--accent-hi); font-size: 10px; font-weight: 650; }
.side-foot p { color: var(--text-4); font-size: 10px; line-height: 1.65; margin: 7px 0 0; }
.is-collapsed .side-head { justify-content: center; padding: 0; }
.is-collapsed .side-nav { padding: 11px 5px; }
.is-collapsed .side-link { justify-content: center; padding: 0; }
.is-collapsed .side-divider { margin: 10px 5px 0; padding: 0; height: 21px; }
@media (max-width: 720px) {
  .app-sidebar { width: 54px; flex-basis: 54px; }
  .app-sidebar:not(.is-collapsed) { width: 54px; flex-basis: 54px; }
  .app-sidebar .side-title, .app-sidebar .link-text, .app-sidebar .side-foot,
  .app-sidebar .side-link > :deep(svg:last-child:not(:first-child)) { display: none; }
  .app-sidebar .side-link { justify-content: center; padding: 0; }
}
</style>
