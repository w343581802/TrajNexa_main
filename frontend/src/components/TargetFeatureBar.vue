<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import MockFrame from "./ui/MockFrame.vue";
import { actions, featureBar, featureList, featureStats, visibleElements, wb } from "../store/workbench";

const toneOf = (status: string) =>
  status === "verified" ? "ok" : status === "review" ? "warn" : status === "conflict" ? "danger" : status === "proposed" ? "info" : "muted";

/** 目标主图：当前选中要素优先，最多展示 3 张，其余收进「全部」 */
const mainImages = computed(() => {
  const list = [...visibleElements.value];
  const index = list.findIndex((item) => item.id === wb.selectedElementId);
  if (index > 0) list.unshift(...list.splice(index, 1));
  return list.slice(0, 3);
});

function pickImage(id: string) {
  actions.selectAnchor(id);
}
</script>

<template>
  <section class="feature-bar" :class="{ 'is-collapsed': featureBar.collapsed }">
    <header class="fb-head">
      <div class="fb-title">
        <span class="card-title">目标特征栏</span>
        <span class="chip" data-tone="violet">T01</span>
        <span class="fb-update">特征更新 20:18 · 档案 v7</span>
        <span v-if="!wb.emptyMode" class="fb-counts">
          <span class="fb-pill" data-tone="warn">待核查 {{ featureStats.pending }}</span>
          <span class="fb-pill" data-tone="danger">冲突 {{ featureStats.conflicts }}</span>
          <span class="fb-pill" data-tone="muted">参考观测 {{ featureStats.refs }}</span>
        </span>
        <span v-else class="fb-update">暂无目标特征</span>
      </div>
      <div class="fb-actions">
        <button class="btn btn--sm btn--outline" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="12" />补充要素
        </button>
        <button class="btn btn--sm btn--ghost" :disabled="wb.emptyMode" @click="actions.openFeature()">
          <AppIcon name="clock" :size="12" />来源与历史
        </button>
        <button
          class="icon-btn"
          :aria-label="featureBar.collapsed ? '展开目标特征栏' : '收起目标特征栏'"
          :title="featureBar.collapsed ? '展开' : '收起'"
          @click="featureBar.collapsed = !featureBar.collapsed"
        >
          <AppIcon :name="featureBar.collapsed ? 'chevron-up' : 'chevron-down'" :size="14" />
        </button>
      </div>
    </header>

    <div v-if="!featureBar.collapsed" class="fb-body">
      <template v-if="!wb.emptyMode">
        <div class="fb-media">
          <header class="fb-media-head">
            <span class="fb-media-title">目标主图</span>
            <span class="chip" data-tone="violet">{{ visibleElements.length }}</span>
            <button class="fb-media-all" @click="actions.openElements()">全部</button>
          </header>
          <div class="fb-media-row">
            <button
              v-for="(image, index) in mainImages"
              :key="image.id"
              class="shot"
              :class="{ 'is-main': index === 0, 'is-active': wb.selectedElementId === image.id }"
              :title="`${image.id} · ${image.cameraLabel} · ${image.timeLabel}（${image.statusLabel}）`"
              @click="pickImage(image.id)"
            >
              <span class="shot-frame">
                <MockFrame :tone="image.tone" :quality="image.quality" shape="portrait" :chrome="false" />
              </span>
              <span class="shot-bar">
                <span class="shot-id">{{ image.id }}</span>
                <span class="shot-time num">{{ image.timeLabel }}</span>
              </span>
              <span v-if="index === 0" class="shot-tag">主图</span>
              <span
                class="shot-state"
                :data-tone="image.status === 'confirmed' ? 'ok' : image.status === 'candidate' ? 'warn' : 'muted'"
                :title="image.statusLabel"
              />
            </button>

            <button v-if="visibleElements.length > mainImages.length" class="shot shot--more" title="查看全部目标要素" @click="actions.openElements()">
              <span class="more-num num">+{{ visibleElements.length - mainImages.length }}</span>
              <span class="more-text">全部</span>
            </button>
          </div>
        </div>

        <div class="fb-grid">
          <button
            v-for="feature in featureList"
            :key="feature.id"
            class="feat"
            :class="[`feat--${feature.status}`, { 'is-active': wb.selectedFeatureId === feature.id }]"
            :title="feature.conflict ? `存在冲突：${feature.conflict.value}（${feature.conflict.reason}）` : feature.meta"
            @click="actions.openFeature(feature.id)"
          >
            <span class="feat-head">
              <span class="feat-attr">{{ feature.attr }}</span>
              <span class="feat-state" :data-tone="toneOf(feature.status)">{{ feature.statusLabel }}</span>
            </span>
            <span class="feat-value">{{ feature.value }}</span>
            <span class="feat-foot">
              <span>{{ feature.meta }}</span>
              <span v-if="feature.conflict" class="feat-conflict"><AppIcon name="warning" :size="10" />冲突</span>
            </span>
          </button>
        </div>

        <div class="fb-rail">
          <dl class="fb-stats">
            <div><dt>来源证据</dt><dd class="num">{{ featureStats.refs }}</dd></div>
            <div><dt>待核查变更</dt><dd class="num warn">{{ featureStats.pending }}</dd></div>
            <div><dt>冲突特征</dt><dd class="num danger">{{ featureStats.conflicts }}</dd></div>
          </dl>
          <p class="fb-note">特征不构成身份结论；未审核候选不会覆盖已核验值。</p>
          <button class="btn btn--sm btn--block btn--outline" @click="actions.openFeature()">
            <AppIcon name="shield" :size="12" />核验与修正
          </button>
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
        <p class="fb-empty-note">原始要素图片在「要素列表」中管理，不占用本栏。</p>
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
  background: var(--bg-1);
  border-top: 1px solid var(--line);
  flex: none;
}

.feature-bar.is-collapsed {
  height: 34px;
}

.fb-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  height: 34px;
  padding: 0 10px 0 12px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.fb-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.fb-update {
  font-size: 10px;
  color: var(--text-3);
  white-space: nowrap;
}

.fb-counts {
  display: flex;
  gap: 4px;
  overflow: hidden;
}

.fb-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 17px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 9.5px;
  border: 1px solid var(--line);
  color: var(--text-3);
  white-space: nowrap;
}

.fb-pill[data-tone="warn"] {
  color: #f3c47c;
  border-color: rgba(240, 180, 90, 0.28);
  background: var(--warn-soft);
}

.fb-pill[data-tone="danger"] {
  color: #ff9a95;
  border-color: rgba(242, 112, 106, 0.28);
  background: var(--danger-soft);
}

.fb-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.fb-body {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) 150px;
  grid-template-rows: minmax(0, 1fr);
  gap: 10px;
  padding: 8px 10px 8px 12px;
  min-height: 0;
  flex: 1;
  overflow: hidden;
}

/* 目标主图卡片 */
.fb-media {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 3px;
  min-height: 0;
}

.fb-media-head {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 13px;
}

.fb-media-title {
  font-size: 9px;
  letter-spacing: 0.06em;
  color: var(--text-3);
  white-space: nowrap;
}

.fb-media-head .chip {
  height: 14px;
  padding: 0 4px;
  font-size: 8px;
}

.fb-media-all {
  margin-left: auto;
  font-size: 9px;
  color: var(--accent-hi);
  white-space: nowrap;
}

.fb-media-all:hover {
  text-decoration: underline;
}

.fb-media-row {
  display: flex;
  align-items: stretch;
  gap: 5px;
  min-height: 0;
  overflow: hidden;
}

.shot {
  position: relative;
  width: 68px;
  flex: none;
  display: grid;
  grid-template-rows: minmax(0, 1fr) 14px;
  gap: 2px;
  padding: 0 0 1px;
  border-radius: 6px;
  border: 1px solid var(--line-2);
  background: var(--bg-2);
  overflow: hidden;
  text-align: left;
  transition: border-color var(--dur) var(--ease), box-shadow var(--dur) var(--ease);
}

.shot:hover {
  border-color: var(--accent-line);
}

.shot.is-main {
  border-color: var(--violet);
}

.shot.is-active {
  box-shadow: 0 0 0 1px var(--violet) inset;
}

.shot-frame {
  position: relative;
  min-height: 0;
  overflow: hidden;
  background: #0a1119;
}

.shot-frame :deep(.mock-frame) {
  position: absolute;
  inset: 0;
}

.shot-bar {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 0 4px;
  min-width: 0;
}

.shot-id {
  font-size: 8.5px;
  font-weight: 700;
  color: #c3aeff;
}

.shot-time {
  margin-left: auto;
  font-size: 8px;
  color: var(--text-3);
}

.shot-tag {
  position: absolute;
  top: 3px;
  left: 3px;
  padding: 0 3px;
  border-radius: 3px;
  font-size: 7.5px;
  line-height: 12px;
  color: #e6dcff;
  background: rgba(64, 45, 112, 0.9);
  border: 1px solid rgba(169, 139, 255, 0.5);
}

.shot-state {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-4);
  box-shadow: 0 0 0 1.5px rgba(6, 10, 15, 0.75);
}

.shot-state[data-tone="ok"] {
  background: var(--ok);
}

.shot-state[data-tone="warn"] {
  background: var(--warn);
}

.shot--more {
  width: 42px;
  place-content: center;
  justify-items: center;
  gap: 0;
  border-style: dashed;
  color: var(--text-2);
}

.more-num {
  font-size: 11px;
  line-height: 1.2;
}

.more-text {
  font-size: 8px;
  color: var(--text-3);
}

/* 特征网格 */
.fb-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-template-rows: repeat(2, minmax(0, 1fr));
  gap: 6px;
  min-width: 0;
}

.feat {
  display: grid;
  grid-template-rows: auto auto auto;
  gap: 2px;
  align-content: center;
  min-width: 0;
  padding: 5px 8px;
  border-radius: 7px;
  border: 1px solid var(--line);
  border-left: 2px solid var(--line-3);
  background: var(--bg-2);
  text-align: left;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.feat:hover {
  background: var(--bg-3);
  border-color: var(--line-2);
}

.feat.is-active {
  border-color: var(--accent-line);
  background: linear-gradient(180deg, rgba(77, 141, 255, 0.09), var(--bg-2));
}

.feat--verified {
  border-left-color: var(--ok);
}

.feat--review {
  border-left-color: var(--warn);
}

.feat--proposed {
  border-left-color: var(--accent);
}

.feat--unknown {
  border-left-color: #455568;
}

.feat--conflict {
  border-left-color: var(--danger);
}

.feat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.feat-attr {
  font-size: 9.5px;
  letter-spacing: 0.04em;
  color: var(--text-3);
}

.feat-state {
  font-size: 9px;
  padding: 0 4px;
  border-radius: 3px;
  color: var(--text-3);
  background: rgba(255, 255, 255, 0.04);
  white-space: nowrap;
}

.feat-state[data-tone="ok"] {
  color: #62dfb2;
  background: var(--ok-soft);
}

.feat-state[data-tone="warn"] {
  color: #f3c47c;
  background: var(--warn-soft);
}

.feat-state[data-tone="danger"] {
  color: #ff9a95;
  background: var(--danger-soft);
}

.feat-state[data-tone="info"] {
  color: #8fb6ff;
  background: var(--accent-soft);
}

.feat-value {
  font-size: 11.5px;
  font-weight: 550;
  color: var(--text-0);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feat--unknown .feat-value {
  color: var(--text-2);
  font-weight: 450;
}

.feat-foot {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 9px;
  color: var(--text-3);
  min-width: 0;
}

.feat-foot > span:first-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feat-conflict {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
  color: #ff9a95;
  white-space: nowrap;
}

/* 右侧统计 */
.fb-rail {
  display: grid;
  grid-template-rows: auto auto 1fr;
  gap: 6px;
  min-width: 0;
  padding-left: 10px;
  border-left: 1px solid var(--line);
}

.fb-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 4px;
}

.fb-stats div {
  display: grid;
  gap: 1px;
}

.fb-stats dt {
  font-size: 8.5px;
  color: var(--text-3);
  white-space: nowrap;
}

.fb-stats dd {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-0);
  line-height: 1.1;
}

.fb-stats .warn {
  color: var(--warn);
}

.fb-stats .danger {
  color: var(--danger);
}

.fb-note {
  font-size: 9px;
  line-height: 1.45;
  color: var(--text-3);
}

/* 空态 */
.fb-empty {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 4px;
}

.fb-empty-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  border: 1px dashed var(--line-3);
  color: var(--text-3);
  flex: none;
}

.fb-empty-text {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.fb-empty-text strong {
  font-size: var(--fs-label);
  color: var(--text-1);
  font-weight: 600;
}

.fb-empty-text span {
  font-size: 10px;
  color: var(--text-3);
}

.fb-empty-note {
  font-size: 9.5px;
  color: var(--text-4);
  margin-left: auto;
}

@media (max-width: 1300px) {
  .fb-body {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .fb-rail {
    display: none;
  }
}
</style>
