<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { mainApi } from "../services/mainApi";
import { actions, featureBar, featureList, featureStats, visibleElements, wb } from "../store/workbench";
import type { TargetElement } from "../data/mock";

const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false });

const toneOf = (status: string) =>
  status === "verified" ? "ok" : status === "review" ? "warn" : status === "conflict" ? "danger" : status === "proposed" ? "info" : "muted";

const target = computed<TargetElement | null>(
  () => visibleElements.value.find((item) => item.id === wb.selectedElementId) ?? visibleElements.value[0] ?? null,
);

const CATEGORY_TEXT: Record<string, string> = { person: "人员", vehicle: "车辆", other: "其他目标" };
const targetCategory = computed(() => (target.value ? CATEGORY_TEXT[target.value.category ?? "other"] ?? "目标" : "目标"));

/** 命中的要素才有截图；没有截图时留空，不拿假图占位 */
const shotUrl = computed(() => {
  const mediaId = target.value?.sourceMediaId;
  return mediaId ? mainApi.mediaContentUrl(mediaId) : "";
});

/** 归一化框 → CSS 裁剪：容器 overflow:hidden，图片按 1/w 放大再反向平移 */
function cropStyle(box: { x: number; y: number; w: number; h: number } | null | undefined) {
  if (!box) return {};
  const w = Math.max(0.02, Math.min(1, box.w));
  const h = Math.max(0.02, Math.min(1, box.h));
  return {
    width: `${100 / w}%`,
    height: `${100 / h}%`,
    left: `${(-box.x / w) * 100}%`,
    top: `${(-box.y / h) * 100}%`,
  };
}

const targetCropStyle = computed(() => cropStyle(target.value?.boxes?.[0] ?? null));

const assocCropStyle = computed(() => {
  const box = target.value?.assocBox;
  if (!box) return {};
  const [x1, y1, x2, y2] = box;
  return cropStyle({ x: x1, y: y1, w: x2 - x1, h: y2 - y1 });
});

const refCropStyle = (item: TargetElement) => cropStyle(item.boxes?.[0] ?? null);

/** 目标特征：优先只显示当前目标的，未归属要素的特征也保留（人工录入的历史值） */
const targetFeatures = computed(() => {
  const id = target.value?.id;
  return featureList.value.filter((feature) => !feature.elementId || !id || feature.elementId === id);
});

const updatedAt = computed(() => (wb.emptyMode ? "—" : (target.value?.timeLabel ?? "—")));

function commit(featureId: string, event: Event) {
  const value = (event.target as HTMLInputElement).value;
  void actions.updateElementFeature(featureId, value);
}
</script>

<template>
  <section class="feature-bar" :class="{ 'is-collapsed': featureBar.collapsed && !props.embedded, 'is-embedded': props.embedded }">
    <header class="fb-head">
      <span class="fb-mark"><AppIcon name="sliders" :size="13" /></span>
      <span class="fb-name">目标特征</span>
      <span class="fb-sep">·</span>
      <span class="fb-tid num">{{ target?.timeLabel ?? "—" }}</span>

      <div class="fb-badges">
        <span class="fb-badge" data-tone="warn"><AppIcon name="warning" :size="11" />待核验 <b class="num">{{ featureStats.pending }}</b></span>
        <span class="fb-badge" data-tone="danger"><AppIcon name="close" :size="11" />冲突 <b class="num">{{ featureStats.conflicts }}</b></span>
        <button class="fb-badge fb-badge--link" data-tone="info" @click="actions.openElements()">
          <AppIcon name="layers" :size="11" />参考要素 <span class="num">({{ visibleElements.length }})</span>
        </button>
      </div>

      <button
        v-if="!props.embedded"
        class="icon-btn fb-collapse"
        :aria-label="featureBar.collapsed ? '展开目标特征栏' : '收起目标特征栏'"
        :title="featureBar.collapsed ? '展开' : '收起'"
        @click="featureBar.collapsed = !featureBar.collapsed"
      >
        <AppIcon :name="featureBar.collapsed ? 'chevron-up' : 'chevron-down'" :size="14" />
      </button>
    </header>

    <div v-if="!featureBar.collapsed || props.embedded" class="fb-body">
      <div v-if="target" class="fb-split">
        <div class="fb-shots">
          <figure class="fb-shot">
            <div class="fb-crop" :class="{ 'is-empty': !shotUrl }">
              <img v-if="shotUrl" :src="shotUrl" :style="targetCropStyle" alt="目标截图" />
              <span v-else class="fb-crop-empty">没有留存截图</span>
            </div>
            <figcaption>
              <strong>{{ targetCategory }}</strong>
              <em class="num">{{ target.timeLabel }}</em>
            </figcaption>
          </figure>

          <figure class="fb-shot">
            <div class="fb-crop fb-crop--assoc" :class="{ 'is-empty': !target.assocBox }">
              <img v-if="shotUrl && target.assocBox" :src="shotUrl" :style="assocCropStyle" alt="关联对象截图" />
              <span v-else class="fb-crop-empty">未框选关联对象</span>
            </div>
            <figcaption>
              <strong>关联对象</strong>
              <em>{{ target.assocBox ? "证据" : "—" }}</em>
            </figcaption>
          </figure>
        </div>

        <div class="fb-fields">
          <div class="fb-fields-head">
            <span>特征（可直接改）</span>
            <em class="num">{{ target.cameraLabel }}</em>
          </div>
          <ul class="fb-list">
            <li v-for="feature in targetFeatures" :key="feature.id" class="fb-row">
              <label class="fb-row-label" :title="feature.meta">
                <span class="fb-attr">{{ feature.attr }}</span>
                <input
                  class="fb-input"
                  :value="feature.value === '未知' ? '' : feature.value"
                  placeholder="未标注"
                  @change="commit(feature.id, $event)"
                  @keyup.enter="(($event.target as HTMLInputElement).blur())"
                />
              </label>
              <span class="fb-state" :data-tone="toneOf(feature.status)">{{ feature.statusLabel }}</span>
            </li>
            <li v-if="!targetFeatures.length" class="fb-row fb-row--empty">
              还没有特征。补充「车辆颜色 / 上衣 / 携带物」等可见信息。
            </li>
          </ul>
          <div class="fb-actions">
            <button class="fb-btn" @click="actions.openLatestGroupEditor()">编辑目标组</button>
            <button class="fb-btn" @click="actions.openElements()">全部要素</button>
          </div>
        </div>
      </div>

      <div v-if="!target" class="fb-empty">
        <span class="fb-empty-icon"><AppIcon name="sliders" :size="18" /></span>
        <div class="fb-empty-text">
          <strong>还没有添加要素</strong>
          <span>添加目标要素后，这里按「目标截图 / 关联对象截图 + 可编辑特征」展示；没有可靠观察时保持未知。</span>
        </div>
        <button class="btn btn--sm btn--primary" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="12" />添加目标要素
        </button>
      </div>

      <div v-if="visibleElements.length" class="fb-refs">
        <div class="refs-row">
          <button
            v-for="item in visibleElements"
            :key="item.id"
            class="ref"
            :class="{ 'is-active': target?.id === item.id }"
            :title="`${item.timeLabel} · ${item.cameraLabel}`"
            @click="actions.selectAnchor(item.id)"
          >
            <span class="ref-img" :class="{ 'is-empty': !item.sourceMediaId }">
              <img v-if="item.sourceMediaId" :src="mainApi.mediaContentUrl(item.sourceMediaId)" :style="refCropStyle(item)" alt="要素截图" />
            </span>
            <span class="ref-time num">{{ item.timeLabel }}</span>
          </button>
          <button class="ref ref--add" @click="actions.openAddElement()">
            <AppIcon name="plus" :size="14" />
            <span>添加要素</span>
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.feature-bar {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--surface-2);
  border-top: 1px solid var(--line);
}

.is-embedded .fb-body {
  flex-direction: column;
  gap: 8px;
}

.fb-head {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border-bottom: 1px solid var(--line);
}

.fb-mark {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 4px;
  color: var(--accent-hi);
  background: var(--accent-soft);
}

.fb-name {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-1);
}

.fb-sep {
  color: var(--text-4);
}

.fb-tid {
  font-size: 11px;
  color: var(--text-2);
  letter-spacing: 0.02em;
}

.fb-badges {
  display: flex;
  gap: 4px;
  margin-left: auto;
}

.fb-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 10.5px;
  color: var(--text-2);
  border: 1px solid var(--line-2);
}

.fb-badge[data-tone="warn"] {
  color: var(--warn);
  border-color: rgba(245, 184, 78, 0.28);
}

.fb-badge[data-tone="danger"] {
  color: var(--danger);
  border-color: rgba(242, 112, 106, 0.28);
}

.fb-badge--link:hover {
  color: var(--accent-hi);
  border-color: var(--accent-line);
}

.fb-collapse {
  width: 24px;
  height: 24px;
}

.fb-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  flex: 1;
  padding: 8px 10px 10px;
  overflow: auto;
}

/* ---------------- 左图右特征 ---------------- */

.fb-split {
  display: grid;
  grid-template-columns: minmax(104px, 38%) minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.fb-shots {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.fb-shot {
  margin: 0;
  display: grid;
  gap: 4px;
}

.fb-crop {
  position: relative;
  overflow: hidden;
  aspect-ratio: 4 / 3;
  border-radius: 6px;
  border: 1px solid var(--line-2);
  background: #0a0f15;
}

.fb-crop img {
  position: absolute;
  max-width: none;
  object-fit: fill;
  pointer-events: none;
}

.fb-crop.is-empty {
  display: grid;
  place-items: center;
  border-style: dashed;
}

.fb-crop-empty {
  font-size: 10px;
  color: var(--text-4);
  text-align: center;
  padding: 0 6px;
}

.fb-shot figcaption {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  font-size: 10px;
  color: var(--text-3);
}

.fb-shot figcaption strong {
  font-weight: 500;
  color: var(--text-2);
}

.fb-shot figcaption em {
  font-style: normal;
  color: var(--text-4);
}

.fb-fields {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.fb-fields-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  font-size: 10.5px;
  color: var(--text-2);
}

.fb-fields-head em {
  font-style: normal;
  font-size: 10px;
  color: var(--text-4);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fb-list {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.fb-row {
  display: grid;
  gap: 2px;
}

.fb-row--empty {
  padding: 8px;
  border: 1px dashed var(--line-2);
  border-radius: 6px;
  font-size: 10.5px;
  color: var(--text-3);
  line-height: 1.5;
}

.fb-row-label {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
}

.fb-attr {
  font-size: 10.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fb-input {
  height: 24px;
  padding: 0 7px;
  border-radius: 5px;
  border: 1px solid var(--line-2);
  background: #0b1017;
  color: var(--text-0);
  font-size: 11.5px;
  min-width: 0;
}

.fb-input:focus {
  outline: none;
  border-color: var(--accent-line);
  background: #0d1219;
}

.fb-state {
  font-size: 9.5px;
  color: var(--text-4);
  padding-left: 64px;
}

.fb-state[data-tone="ok"] {
  color: var(--ok);
}

.fb-state[data-tone="warn"] {
  color: var(--warn);
}

.fb-state[data-tone="info"] {
  color: var(--info);
}

.fb-actions {
  display: flex;
  gap: 6px;
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

/* ---------------- 参考要素 ---------------- */

.fb-refs {
  border-top: 1px solid var(--line);
  padding-top: 6px;
}

.refs-row {
  display: flex;
  gap: 6px;
  overflow-x: auto;
  scrollbar-width: thin;
  padding-bottom: 2px;
}

.ref {
  display: grid;
  gap: 2px;
  justify-items: center;
  flex: none;
  width: 54px;
  padding: 3px;
  border-radius: 6px;
  border: 1px solid transparent;
}

.ref:hover {
  border-color: var(--line-3);
}

.ref.is-active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.ref-img {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: 4px;
  background: #0a0f15;
  border: 1px solid var(--line-2);
}

.ref-img.is-empty {
  border-style: dashed;
}

.ref-img img {
  position: absolute;
  max-width: none;
  object-fit: fill;
}

.ref-time {
  font-size: 9.5px;
  color: var(--text-3);
}

.ref--add {
  width: 54px;
  align-content: center;
  gap: 3px;
  border: 1px dashed var(--line-2);
  color: var(--text-3);
  font-size: 9.5px;
}

.ref--add:hover {
  border-color: var(--accent-line);
  color: var(--accent-hi);
}

/* ---------------- 空态 ---------------- */

.fb-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 1px dashed var(--line-2);
  border-radius: 8px;
}

.fb-empty-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  color: var(--text-3);
  background: #0b1017;
}

.fb-empty-text {
  display: grid;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.fb-empty-text strong {
  font-size: 12px;
  color: var(--text-1);
}

.fb-empty-text span {
  font-size: 10.5px;
  color: var(--text-3);
  line-height: 1.5;
}
</style>