<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import MockFrame from "./ui/MockFrame.vue";
import { actions, featureBar, featureList, featureStats, visibleElements, wb } from "../store/workbench";

const toneOf = (status: string) =>
  status === "verified" ? "ok" : status === "review" ? "warn" : status === "conflict" ? "danger" : status === "proposed" ? "info" : "muted";

const targetImage = computed(() => visibleElements.value[0] ?? null);
const refs = computed(() => {
  const list = visibleElements.value;
  if (list.length <= 4) return list;
  const head = list.slice(0, 4);
  const selected = list.find((item) => item.id === wb.selectedElementId);
  if (selected && !head.includes(selected)) return [...head.slice(0, 3), selected];
  return head;
});

const updatedAt = computed(() => (wb.emptyMode ? "—" : (visibleElements.value.at(-1)?.time ?? "—")));
</script>

<template>
  <section class="feature-bar" :class="{ 'is-collapsed': featureBar.collapsed }">
    <header class="fb-head">
      <span class="fb-mark"><AppIcon name="sliders" :size="13" /></span>
      <span class="fb-name">目标特征</span>
      <span class="fb-sep">·</span>
      <span class="fb-tid num">T01</span>

      <div class="fb-badges">
        <span class="fb-badge" data-tone="warn"><AppIcon name="warning" :size="11" />待校验 <b class="num">{{ featureStats.pending }}</b></span>
        <span class="fb-badge" data-tone="danger"><AppIcon name="close" :size="11" />冲突 <b class="num">{{ featureStats.conflicts }}</b></span>
        <button class="fb-badge fb-badge--link" data-tone="info" @click="actions.openElements()">
          <AppIcon name="layers" :size="11" />参考要素 <span class="num">({{ visibleElements.length }})</span>
        </button>
      </div>

      <button
        class="icon-btn fb-collapse"
        :aria-label="featureBar.collapsed ? '展开目标特征栏' : '收起目标特征栏'"
        :title="featureBar.collapsed ? '展开' : '收起'"
        @click="featureBar.collapsed = !featureBar.collapsed"
      >
        <AppIcon :name="featureBar.collapsed ? 'chevron-up' : 'chevron-down'" :size="14" />
      </button>
    </header>

    <div v-if="!featureBar.collapsed" class="fb-body">
      <template v-if="!wb.emptyMode">
        <div class="fb-target">
          <span class="fb-photo">
            <MockFrame v-if="targetImage" :tone="targetImage.tone" :quality="targetImage.quality" shape="portrait" :chrome="false" />
            <span v-else class="fb-photo-empty"><AppIcon name="user" :size="18" /></span>
          </span>
          <div class="fb-target-meta">
            <strong>目标 T01</strong>
            <span class="fb-updated">更新于 <i class="num">{{ updatedAt }}</i></span>
            <button class="fb-btn" @click="actions.openFeature()">查看详情</button>
            <button class="fb-btn" @click="actions.openFeature()">编辑特征</button>
          </div>
        </div>

        <div class="fb-cells">
          <button
            v-for="feature in featureList"
            :key="feature.id"
            class="fcell"
            :class="`fcell--${feature.status}`"
            :title="feature.conflict ? `存在冲突：${feature.conflict.value}（${feature.conflict.reason}）` : feature.meta"
            @click="actions.openFeature(feature.id)"
          >
            <span class="fcell-img">
              <MockFrame :chrome="false" shape="portrait" :tone="feature.status === 'unknown' ? 'gray' : 'color'" :quality="feature.status === 'unknown' ? 'low' : 'good'" />
            </span>
            <span class="fcell-txt">
              <em>{{ feature.attr }}</em>
              <b>{{ feature.value }}</b>
            </span>
            <span class="fcell-conf" :data-tone="toneOf(feature.status)">
              <AppIcon name="shield" :size="11" />
              <template v-if="feature.confidence !== undefined">{{ feature.confidence }}%</template>
              <template v-else-if="feature.status === 'unknown'">未知</template>
              <template v-else-if="feature.elementId">待核验</template>
              <template v-else>未知</template>
            </span>
            <span class="fcell-edit"><AppIcon name="edit" :size="12" /></span>
            <span v-if="feature.conflict" class="fcell-conflict">冲突</span>
          </button>
        </div>

        <div class="fb-refs">
          <span class="refs-title">参考要素</span>
          <div class="refs-row">
            <button
              v-for="item in refs"
              :key="item.id"
              class="ref"
              :class="{ 'is-active': wb.selectedElementId === item.id }"
              :title="`${item.id} · ${item.cameraLabel} · ${item.timeLabel}`"
              @click="actions.selectAnchor(item.id)"
            >
              <span class="ref-img"><MockFrame :tone="item.tone" :quality="item.quality" shape="portrait" :chrome="false" /></span>
              <span class="ref-id num">{{ item.id }}</span>
              <span class="ref-time num">{{ item.timeLabel }}</span>
            </button>
            <button class="ref ref--add" @click="actions.openAddElement()">
              <AppIcon name="plus" :size="14" />
              <span>添加要素</span>
            </button>
          </div>
        </div>
      </template>

      <div v-else class="fb-empty">
        <span class="fb-empty-icon"><AppIcon name="sliders" :size="18" /></span>
        <div class="fb-empty-text">
          <strong>暂无目标特征</strong>
          <span>添加目标要素后，系统将在此汇总可观察特征；没有可靠观察时保持未知，不自动猜测。</span>
        </div>
        <button class="btn btn--sm btn--primary" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="12" />添加目标要素
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.feature-bar {
  display: flex;
  flex-direction: column;
  height: var(--feature-h);
  min-height: 0;
  background: #080c12;
  border-top: 1px solid var(--line);
  flex: none;
}

.feature-bar.is-collapsed {
  height: 36px;
}

.fb-head {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 36px;
  padding: 0 10px 0 12px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.fb-mark {
  display: grid;
  place-items: center;
  color: var(--accent-hi);
}

.fb-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-0);
}

.fb-sep {
  color: var(--text-4);
}

.fb-tid {
  font-size: 13.5px;
  font-weight: 650;
  color: var(--text-0);
}

.fb-badges {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.fb-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 5px;
  border: 1px solid var(--line-2);
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-2);
  font-size: 11px;
  white-space: nowrap;
}

.fb-badge b {
  font-weight: 650;
}

.fb-badge[data-tone="warn"] {
  color: #f6c98a;
  border-color: rgba(240, 180, 90, 0.3);
  background: rgba(240, 180, 90, 0.1);
}

.fb-badge[data-tone="danger"] {
  color: #ffb0ab;
  border-color: rgba(242, 112, 106, 0.32);
  background: rgba(242, 112, 106, 0.1);
}

.fb-badge[data-tone="info"] {
  color: #a8c6ff;
  border-color: rgba(77, 141, 255, 0.32);
  background: rgba(77, 141, 255, 0.1);
}

.fb-collapse {
  width: 24px;
  height: 24px;
  margin-left: 2px;
}

.fb-body {
  display: flex;
  gap: 12px;
  align-items: stretch;
  min-height: 0;
  flex: 1;
  padding: 8px 10px 9px 12px;
}

/* ---------------- 目标 ---------------- */

.fb-target {
  display: flex;
  gap: 8px;
  flex: none;
  padding: 6px 8px 6px 6px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #0b1017;
}

.fb-photo {
  display: block;
  width: 62px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--line-2);
  background: #0a0f15;
}

.fb-photo :deep(.mock-frame) {
  width: 100%;
  height: 100%;
  border-radius: 0;
}

.fb-photo-empty {
  display: grid;
  place-items: center;
  height: 100%;
  color: var(--text-4);
}

.fb-target-meta {
  display: grid;
  align-content: center;
  gap: 4px;
  min-width: 96px;
}

.fb-target-meta strong {
  font-size: 13px;
  color: var(--text-0);
}

.fb-updated {
  font-size: 10.5px;
  color: var(--text-3);
  margin-bottom: 2px;
}

.fb-btn {
  height: 22px;
  padding: 0 9px;
  border-radius: 5px;
  border: 1px solid var(--line-3);
  color: var(--text-1);
  font-size: 11px;
}

.fb-btn:hover {
  border-color: var(--accent-line);
  color: var(--accent-hi);
}

/* ---------------- 特征格 ---------------- */

.fb-cells {
  display: flex;
  gap: 8px;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: thin;
}

.fcell {
  position: relative;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  grid-template-rows: auto auto;
  gap: 4px 8px;
  align-content: start;
  min-width: 0;
  flex: 1 1 108px;
  min-width: 104px;
  padding: 8px 8px 24px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #0b1017;
  text-align: left;
  transition: border-color var(--dur) var(--ease);
}

.fcell:hover {
  border-color: var(--line-3);
}

.fcell--conflict {
  border-color: rgba(242, 112, 106, 0.4);
}

.fcell-img {
  grid-row: 1 / 3;
  display: block;
  width: 42px;
  height: 58px;
  border-radius: 5px;
  overflow: hidden;
  border: 1px solid var(--line-2);
  background: #0a0f15;
}

.fcell-img :deep(.mock-frame) {
  width: 100%;
  height: 100%;
  border-radius: 0;
}

.fcell-txt {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.fcell-txt em {
  font-style: normal;
  font-size: 10.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fcell-txt b {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-0);
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.fcell-conf {
  grid-column: 2;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  align-self: start;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-2);
  background: rgba(255, 255, 255, 0.05);
  justify-self: start;
}

.fcell-conf[data-tone="ok"] {
  color: #2fcf92;
  background: rgba(47, 207, 146, 0.12);
}

.fcell-conf[data-tone="warn"] {
  color: #f0b45a;
  background: rgba(240, 180, 90, 0.12);
}

.fcell-conf[data-tone="info"] {
  color: #8fb6ff;
  background: rgba(77, 141, 255, 0.12);
}

.fcell-conf[data-tone="danger"] {
  color: #ff8f88;
  background: rgba(242, 112, 106, 0.12);
}

.fcell-edit {
  position: absolute;
  right: 8px;
  bottom: 7px;
  color: var(--text-4);
}

.fcell-conflict {
  position: absolute;
  left: 8px;
  bottom: 7px;
  font-size: 10px;
  color: #ff8f88;
}

/* ---------------- 参考要素 ---------------- */

.fb-refs {
  flex: none;
  display: grid;
  align-content: start;
  gap: 5px;
  padding-left: 2px;
}

.refs-title {
  font-size: 10.5px;
  color: var(--text-3);
}

.refs-row {
  display: flex;
  gap: 6px;
}

.ref {
  display: grid;
  justify-items: center;
  gap: 2px;
  width: 46px;
  padding: 3px 2px 4px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #0b1017;
}

.ref.is-active {
  border-color: var(--accent-line);
  background: rgba(77, 141, 255, 0.1);
}

.ref-img {
  display: block;
  width: 100%;
  height: 38px;
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid var(--line-2);
}

.ref-img :deep(.mock-frame) {
  width: 100%;
  height: 100%;
  border-radius: 0;
}

.ref-id {
  font-size: 10px;
  color: var(--text-1);
}

.ref-time {
  font-size: 9.5px;
  color: var(--text-4);
}

.ref--add {
  width: 54px;
  place-content: center;
  justify-items: center;
  gap: 3px;
  border-style: dashed;
  color: var(--text-3);
  font-size: 10px;
}

.ref--add:hover {
  border-color: var(--accent-line);
  color: var(--accent-hi);
}

.fb-empty {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  justify-content: center;
}

.fb-empty-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px dashed var(--line-3);
  color: var(--text-3);
}

.fb-empty-text {
  display: grid;
  gap: 2px;
  max-width: 380px;
}

.fb-empty-text strong {
  font-size: 12.5px;
  color: var(--text-1);
}

.fb-empty-text span {
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-3);
}

/* 窄屏（≤1680）时特征格变窄，隐藏格子里的画面，把宽度让给“属性 + 取值 + 可信度” */
@media (max-width: 1680px) {
  .fcell {
    grid-template-columns: minmax(0, 1fr);
    padding: 8px 9px 22px;
  }

  .fcell-img {
    display: none;
  }

  .fcell-conf {
    grid-column: 1;
  }
}
</style>