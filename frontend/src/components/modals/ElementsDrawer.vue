<script setup lang="ts">
import AppIcon from "../ui/AppIcon.vue";
import MockFrame from "../ui/MockFrame.vue";
import SideDrawer from "../ui/SideDrawer.vue";
import { actions, ui, visibleElements, wb, observationList } from "../../store/workbench";

function locate(id: string) {
  actions.selectAnchor(id);
  ui.elementsOpen = false;
}

function trace(elementId: string) {
  const observation = observationList.value.find((item) => item.elementId === elementId);
  if (observation) actions.selectObservation(observation.id);
  ui.elementsOpen = false;
}
</script>

<template>
  <SideDrawer
    :open="ui.elementsOpen"
    title="目标要素列表"
    :subtitle="`${visibleElements.length} 个独立要素 · 只新增不覆盖，修改产生新版本`"
    width="424px"
    @close="ui.elementsOpen = false"
  >
    <template #head-extra>
      <button class="btn btn--sm btn--outline" @click="ui.elementsOpen = false; actions.openAddElement()">
        <AppIcon name="plus" :size="12" />添加
      </button>
    </template>

    <div v-if="!visibleElements.length" class="elements-empty">
      <span class="elements-empty-icon"><AppIcon name="folder" :size="18" /></span>
      <p>尚无目标要素</p>
      <span>添加目标截图后，可在时间线与地图生成起始锚点；运行中追加要素不会清空已有任务与证据。</span>
      <button class="btn btn--sm btn--primary" @click="ui.elementsOpen = false; actions.openAddElement()">
        <AppIcon name="plus" :size="12" />添加目标要素
      </button>
    </div>

    <ul v-else class="elements">
      <li
        v-for="element in visibleElements"
        :key="element.id"
        class="element"
        :class="{ 'is-active': wb.selectedElementId === element.id }"
      >
        <div class="element-thumb">
          <MockFrame :tone="element.tone" :quality="element.quality" :shape="'portrait'" :chrome="false" />
        </div>
        <div class="element-info">
          <div class="element-top">
            <span class="element-id">{{ element.id }}</span>
            <span class="num element-time">{{ element.timeLabel }}</span>
            <span class="chip" :data-tone="element.status === 'confirmed' ? 'ok' : element.status === 'candidate' ? 'warn' : 'muted'">
              {{ element.statusLabel }}
            </span>
          </div>
          <p class="element-cam truncate">{{ element.cameraLabel }}</p>
          <p class="element-note">{{ element.note }}</p>
          <div class="element-actions">
            <button class="btn btn--sm btn--ghost" @click="locate(element.id)">
              <AppIcon name="target" :size="11" />定位
            </button>
            <button class="btn btn--sm btn--ghost" @click="actions.openAddElement(element.id)">
              <AppIcon name="edit" :size="11" />编辑
            </button>
            <button class="btn btn--sm btn--ghost" @click="trace(element.id)">
              <AppIcon name="clock" :size="11" />追溯
            </button>
          </div>
        </div>
      </li>
    </ul>

    <template #footer>
      <p class="elements-foot-note">原始图与裁剪图分别保存；锚点只是操作起点，不代表跨镜头身份已确认。</p>
    </template>
  </SideDrawer>
</template>

<style scoped>
.elements-empty {
  display: grid;
  justify-items: center;
  gap: 6px;
  padding: 30px 18px;
  text-align: center;
}

.elements-empty-icon {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  border: 1px dashed var(--line-3);
  color: var(--text-3);
}

.elements-empty p {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-1);
}

.elements-empty span {
  font-size: 10px;
  line-height: 1.55;
  color: var(--text-3);
  max-width: 34ch;
}

.elements {
  display: grid;
  gap: 7px;
}

.element {
  display: flex;
  gap: 9px;
  padding: 8px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background: var(--bg-2);
}

.element.is-active {
  border-color: var(--violet);
  background: linear-gradient(180deg, rgba(169, 139, 255, 0.07), var(--bg-2));
}

.element-thumb {
  width: 62px;
  height: 76px;
  flex: none;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--line-2);
}

.element-info {
  display: grid;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.element-top {
  display: flex;
  align-items: center;
  gap: 6px;
}

.element-id {
  font-size: 12px;
  font-weight: 700;
  color: #c3aeff;
}

.element-time {
  font-size: 10px;
  color: var(--text-1);
}

.element-top .chip {
  margin-left: auto;
  height: 17px;
  font-size: 8.5px;
}

.element-cam {
  font-size: 10px;
  color: var(--text-2);
}

.element-note {
  font-size: 9px;
  line-height: 1.4;
  color: var(--text-3);
}

.element-actions {
  display: flex;
  gap: 2px;
  margin-top: 2px;
}

.element-actions .btn {
  height: 20px;
  font-size: 9px;
  padding: 0 5px;
}

.elements-foot-note {
  font-size: 9px;
  line-height: 1.45;
  color: var(--text-3);
}
</style>
