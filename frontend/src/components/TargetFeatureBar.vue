<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import TargetCrop, { type CropBox } from "./TargetCrop.vue";
import {
  candidateEvidenceUrl,
  keyframeUrl,
  mainApi,
  type TimelineCard,
} from "../services/mainApi";
import {
  actions,
  featureBar,
  featureList,
  loadTimeline,
  loadTrackingRuns,
  timelineCards,
  visibleElements,
  wb,
} from "../store/workbench";
import type { TargetElement, TargetFeature } from "../data/mock";

const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false });

const CATEGORY_TEXT: Record<string, string> = { person: "人员", vehicle: "车辆", other: "其他目标" };

const target = computed<TargetElement | null>(
  () => visibleElements.value.find((item) => item.id === wb.selectedElementId) ?? visibleElements.value[0] ?? null,
);
const targetCategory = computed(() => (target.value ? CATEGORY_TEXT[target.value.category ?? "other"] ?? "目标" : "目标"));

/** 命中的要素才有截图；没有截图时留空，不拿假图占位 */
const shotUrl = computed(() => {
  const mediaId = target.value?.sourceMediaId;
  return mediaId ? mainApi.mediaContentUrl(mediaId) : "";
});

const targetBox = computed<CropBox | null>(() => {
  const box = target.value?.boxes?.[0];
  return box ? { x: box.x, y: box.y, w: box.w, h: box.h } : null;
});

const assocBox = computed<CropBox | null>(() => {
  const box = target.value?.assocBox;
  if (!box) return null;
  return { x: box[0], y: box[1], w: box[2] - box[0], h: box[3] - box[1] };
});

/* ---------------------------- 截图大图查看窗 ---------------------------- */

type ImageViewerState = {
  kind: "target" | "assoc";
  title: string;
  crop: CropBox | null;
};

const imageViewer = ref<ImageViewerState | null>(null);

function openImageViewer(kind: "target" | "assoc"): void {
  if (!shotUrl.value) return;
  imageViewer.value = {
    kind,
    title: kind === "target" ? "目标截图" : "关联对象截图",
    crop: kind === "target" ? targetBox.value : assocBox.value,
  };
}

function closeImageViewer(): void {
  imageViewer.value = null;
}

function onImageViewerKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") closeImageViewer();
}

function normalized(value: string): string {
  return value.trim().toLowerCase().replace(/\s+/g, "");
}

/**
 * 只取「当前要素自己的特征」+「用户固定/当前选中的卡片带来的特征」。
 * 不把时间轴上所有卡片的特征一股脑塞进来；同属性同值去重，同属性不同值标冲突。
 */
const targetFeatures = computed<TargetFeature[]>(() => {
  const id = target.value?.id;
  if (!id) return [];
  const selectedCard = wb.selectedCardId;
  const raw = [
    ...featureList.value.filter((feature) => feature.elementId === id),
    ...featureList.value.filter((feature) => !feature.elementId
      && (feature.pinned || (feature.sourceCardId != null && feature.sourceCardId === selectedCard))),
  ];
  const byValue = new Map<string, TargetFeature>();
  const valuesByAttr = new Map<string, Set<string>>();
  for (const feature of raw) {
    const key = `${feature.attr}::${normalized(feature.value)}`;
    if (!byValue.has(key)) byValue.set(key, { ...feature });
    if (!valuesByAttr.has(feature.attr)) valuesByAttr.set(feature.attr, new Set());
    valuesByAttr.get(feature.attr)?.add(normalized(feature.value));
  }
  for (const feature of byValue.values()) {
    const values = valuesByAttr.get(feature.attr);
    if (values && values.size > 1) feature.conflict = { value: "同属性有不同值", reason: "需要人工核对" };
  }
  return [...byValue.values()];
});

const conflictCount = computed(() => targetFeatures.value.filter((feature) => feature.conflict).length);
const pendingCount = computed(() => targetFeatures.value.filter(
  (feature) => feature.status === "review" || feature.status === "proposed",
).length);
const featureCount = computed(() => targetFeatures.value.length);

/* ------------------------------ 目标特征说明 · 小卡片 ------------------------------ */

const editingFeatureId = ref<string | null>(null);
const editingValue = ref("");
const editInput = ref<HTMLInputElement | null>(null);
const savingFeature = ref(false);
const addingFeature = ref(false);
const newAttr = ref("");
const newValue = ref("");
const newAttrInput = ref<HTMLInputElement | null>(null);
const editingFeature = computed<TargetFeature | null>(() => (
  editingFeatureId.value
    ? targetFeatures.value.find((item) => item.id === editingFeatureId.value) ?? null
    : null
));

function featureTone(feature: TargetFeature): "danger" | "warn" | "ok" | "muted" {
  if (feature.conflict) return "danger";
  if (feature.status === "review" || feature.status === "proposed") return "warn";
  if (feature.status === "verified") return "ok";
  return "muted";
}

function startEdit(feature: TargetFeature): void {
  if (!feature.elementId) return;
  editingFeatureId.value = feature.id;
  editingValue.value = feature.value === "未知" ? "" : feature.value;
  void nextTick(() => editInput.value?.focus());
}

function cancelEdit(): void {
  editingFeatureId.value = null;
  editingValue.value = "";
}

async function saveEdit(feature: TargetFeature): Promise<void> {
  if (savingFeature.value) return;
  savingFeature.value = true;
  try {
    await actions.updateElementFeature(feature.id, editingValue.value);
    cancelEdit();
  } finally {
    savingFeature.value = false;
  }
}

async function removeFeature(feature: TargetFeature): Promise<void> {
  if (savingFeature.value) return;
  savingFeature.value = true;
  try {
    const removed = await actions.removeElementFeature(feature.id);
    if (removed) cancelEdit();
  } finally {
    savingFeature.value = false;
  }
}

async function removeTarget(): Promise<void> {
  const elementId = target.value?.id;
  if (!elementId) return;
  await actions.deleteElement(elementId);
}

async function removeAssoc(): Promise<void> {
  const elementId = target.value?.id;
  if (!elementId) return;
  await actions.removeAssociatedObject(elementId);
}

function beginAddFeature(): void {
  addingFeature.value = true;
  newAttr.value = "";
  newValue.value = "";
  editingFeatureId.value = null;
  void nextTick(() => newAttrInput.value?.focus());
}

function cancelAddFeature(): void {
  addingFeature.value = false;
  newAttr.value = "";
  newValue.value = "";
}

async function submitNewFeature(): Promise<void> {
  const elementId = target.value?.id;
  const attr = newAttr.value.trim();
  if (!elementId || !attr || savingFeature.value) return;
  savingFeature.value = true;
  try {
    const saved = await actions.addElementFeature(elementId, attr, newValue.value);
    if (saved) cancelAddFeature();
  } finally {
    savingFeature.value = false;
  }
}

watch(() => target.value?.id, () => {
  cancelEdit();
  cancelAddFeature();
  closeImageViewer();
});

/* --------------------------- 其他补充小图（证据帧） --------------------------- */

type SupplementItem = {
  id: string;
  src: string;
  box?: CropBox | null;
  title: string;
  meta: string;
  tag: string;
  cardId?: string;
  elementId?: string;
  selected?: boolean;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function elementBox(item: TargetElement): CropBox | null {
  const box = item.boxes?.[0];
  return box ? { x: box.x, y: box.y, w: box.w, h: box.h } : null;
}

/** 把时间轴卡片映射成它自己的一张证据图；映射不出来就不生成占位图。 */
function imageForCard(card: TimelineCard): { src: string; key: string } | null {
  const caseId = wb.caseId;
  if (!caseId) return null;
  const evidence = asRecord(card.evidence);
  const candidateId = asText(evidence.candidateId);
  const runId = asText(evidence.runId);
  const mediaId = asText(evidence.mediaId);

  if (candidateId && runId) {
    return {
      src: candidateEvidenceUrl(caseId, runId, candidateId, evidence.bboxPixels ? "crop" : "full"),
      key: `candidate:${candidateId}`,
    };
  }
  if (mediaId) {
    return { src: mainApi.mediaContentUrl(mediaId), key: `media:${mediaId}` };
  }
  const clipId = card.playback?.clipId;
  if (clipId) {
    const offset = card.playback?.offsetSeconds ?? 0;
    return { src: keyframeUrl(caseId, clipId, offset), key: `clip:${clipId}:${offset}` };
  }
  return null;
}

function cardTime(card: TimelineCard): string {
  const value = card.observedAt;
  if (!value) return card.subtitle ?? "证据帧";
  const match = value.match(/T(\d{2}:\d{2})/);
  return match ? `${match[1]} · ${card.cameraLabel ?? "镜头未知"}` : card.subtitle ?? "证据帧";
}

/**
 * 补充图只收“已确定”的证据：目标锚点、已确认轨迹/空间观测、用户手动添加的要素。
 * 候选、区域排查、镜头任务和 gap 都还在待核验阶段，不抢在确认前进入补充区。
 */
function isConfirmedCard(card: TimelineCard): boolean {
  if (card.kind === "anchor") return true;
  if (card.kind !== "track_segment" && card.kind !== "spatial_observation") return false;
  return card.identityStatus === "confirmed" || card.identityStatus === "human_confirmed";
}

/**
 * 右上补充图顺序：当前选中的已确认卡片 → 其它已确认证据帧
 * → 手动添加的其它要素截图。按证据 key 去重，不重复目标大图；最多 12 张。
 */
const allSupplements = computed<SupplementItem[]>(() => {
  const current = target.value;
  if (!current) return [];
  const seen = new Set<string>();
  const out: SupplementItem[] = [];

  const cards = [...timelineCards.value]
    .filter(isConfirmedCard)
    .sort((a, b) => (b.observedAt ?? "").localeCompare(a.observedAt ?? ""));
  const selectedId = wb.selectedCardId;
  const selectedCard = cards.find((card) => card.cardId === selectedId);
  const ordered = selectedCard
    ? [selectedCard, ...cards.filter((card) => card.cardId !== selectedCard.cardId)]
    : cards;

  for (const card of ordered) {
    const image = imageForCard(card);
    if (!image || seen.has(image.key)) continue;
    // 当前目标自己的锚点大图已经在左边，不在补充区重复
    if (card.kind === "anchor" && current.sourceMediaId && image.key === `media:${current.sourceMediaId}`) continue;
    seen.add(image.key);
    out.push({
      id: `card:${card.cardId}`,
      src: image.src,
      title: card.title,
      meta: cardTime(card),
      tag: card.kind === "anchor" ? "目标" : "已确认",
      cardId: card.cardId,
      selected: card.cardId === selectedId,
    });
    if (out.length >= 12) break;
  }

  for (const item of visibleElements.value) {
    if (item.id === current.id || !item.sourceMediaId) continue;
    const key = `media:${item.sourceMediaId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      id: `element:${item.id}`,
      src: mainApi.mediaContentUrl(item.sourceMediaId),
      box: elementBox(item),
      title: `${CATEGORY_TEXT[item.category ?? "other"] ?? "目标"}截图`,
      meta: `${item.timeLabel} · ${item.cameraLabel}`,
      tag: "要素",
      elementId: item.id,
    });
    if (out.length >= 12) break;
  }

  return out;
});

/* 补充图最多两行：按实际宽度算一行能放几张；拉高底栏时才允许第二行。 */
const failedSupplementIds = ref<Set<string>>(new Set());
const supplements = computed(() => allSupplements.value.filter((item) => !failedSupplementIds.value.has(item.id)));
const thumbHost = ref<HTMLElement | null>(null);
const thumbColumns = ref(3);
const thumbRows = ref(1);
const visibleSupplements = computed(() => supplements.value.slice(0, thumbColumns.value * thumbRows.value));
let thumbObserver: ResizeObserver | null = null;

function markSupplementFailed(id: string): void {
  failedSupplementIds.value = new Set([...failedSupplementIds.value, id]);
}

function selectSupplement(item: SupplementItem): void {
  if (item.cardId) actions.selectTimelineCard(item.cardId);
  else if (item.elementId) actions.selectAnchor(item.elementId);
}

function bindThumbObserver(): void {
  thumbObserver?.disconnect();
  const el = thumbHost.value;
  if (!el) return;
  const measure = () => {
    const width = el.clientWidth;
    thumbColumns.value = Math.max(1, Math.floor((width + 5) / 87));
    // 底栏矮时补充图只占一行，把高度留给特征卡片；拉高后最多两行。
    const barHeight = el.closest(".feature-bar")?.clientHeight ?? 0;
    thumbRows.value = barHeight >= 340 ? 2 : 1;
  };
  measure();
  thumbObserver = new ResizeObserver(measure);
  thumbObserver.observe(el);
  const bar = el.closest(".feature-bar");
  if (bar) thumbObserver.observe(bar);
}

watch(target, () => {
  void nextTick(bindThumbObserver);
});

onMounted(async () => {
  window.addEventListener("keydown", onImageViewerKeydown);
  void nextTick(bindThumbObserver);
  // 底部特征栏先于时间轴出现：静默补一次时间轴数据，右上才有真实证据帧可展示
  if (!wb.caseId || timelineCards.value.length) return;
  await loadTrackingRuns();
  if (!timelineCards.value.length) await loadTimeline();
  void nextTick(bindThumbObserver);
});

onBeforeUnmount(() => {
  thumbObserver?.disconnect();
  window.removeEventListener("keydown", onImageViewerKeydown);
});
</script>

<template>
  <section class="feature-bar" :class="{ 'is-collapsed': featureBar.collapsed && !props.embedded, 'is-embedded': props.embedded }">
    <header class="fb-head">
      <span class="fb-mark"><AppIcon name="sliders" :size="13" /></span>
      <span class="fb-name">目标特征</span>
      <span class="fb-sep">·</span>
      <span class="fb-tid num">{{ target?.timeLabel ?? "—" }}</span>

      <div class="fb-badges">
        <span class="fb-badge" data-tone="warn"><AppIcon name="warning" :size="11" />待核验 <b class="num">{{ pendingCount }}</b></span>
        <span v-if="conflictCount" class="fb-badge" data-tone="danger"><AppIcon name="close" :size="11" />冲突 <b class="num">{{ conflictCount }}</b></span>
        <span class="fb-badge" data-tone="info"><AppIcon name="layers" :size="11" />{{ featureCount }} 项</span>
        <button class="fb-add" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="11" />添加要素
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
      <div v-if="target" class="fb-layout">
        <!-- 左：两张大图。高度跟随右侧内容，不再锁 4:3 -->
        <div class="fb-shots">
          <figure class="fb-shot">
            <button
              class="fb-shot-open"
              type="button"
              :disabled="!shotUrl"
              :title="shotUrl ? '点击查看截图大图和完整图片' : '没有留存截图'"
              aria-label="查看目标截图大图和完整图片"
              @click="openImageViewer('target')"
            >
              <TargetCrop
                class="fb-crop"
                :src="shotUrl"
                :box="targetBox"
                alt="目标截图"
                empty-text="没有留存截图"
                fit="contain"
              />
            </button>
            <figcaption>
              <strong>{{ targetCategory }}</strong>
              <em class="num">{{ target.timeLabel }}</em>
            </figcaption>
          </figure>

          <figure class="fb-shot">
            <button
              class="fb-shot-open"
              type="button"
              :disabled="!shotUrl"
              :title="shotUrl ? '点击查看关联对象截图和完整图片' : '没有留存截图'"
              aria-label="查看关联对象截图和完整图片"
              @click="openImageViewer('assoc')"
            >
              <TargetCrop
                class="fb-crop"
                :src="shotUrl"
                :box="assocBox"
                alt="关联对象截图"
                empty-text="未框选关联对象"
                fit="contain"
              />
            </button>
            <figcaption>
              <strong>关联对象</strong>
              <em>{{ assocBox ? "证据" : "—" }}</em>
              <button
                v-if="assocBox"
                class="fb-shot-del"
                type="button"
                title="移除关联对象（主目标与截图保留）"
                @click="removeAssoc"
              >
                <AppIcon name="trash" :size="10" />移除
              </button>
            </figcaption>
          </figure>
        </div>

        <div class="fb-side">
          <!-- 右上：其它补充小图（时间轴证据帧 + 手动添加的要素） -->
          <section class="fb-extra">
            <div class="fb-side-head">
              <span>其他补充图</span>
              <em class="num">{{ visibleSupplements.length }}/{{ supplements.length }}</em>
              <button class="fb-add-mini" title="添加目标要素" @click="actions.openAddElement()">
                <AppIcon name="plus" :size="11" />
                <span>添加</span>
              </button>
            </div>
            <div ref="thumbHost" class="fb-thumbs">
              <button
                v-for="item in visibleSupplements"
                :key="item.id"
                class="fb-thumb"
                :class="{ 'is-selected': item.selected }"
                :title="`${item.title} · ${item.meta}`"
                :aria-label="`查看 ${item.title}`"
                @click="selectSupplement(item)"
              >
                <TargetCrop :src="item.src" :box="item.box" :alt="item.title" empty-text="无证据帧" @error="markSupplementFailed(item.id)" />
                <span class="fb-thumb-tag">{{ item.tag }}</span>
              </button>
            </div>
            <div v-if="!supplements.length" class="fb-extra-empty">
              只展示已确认的证据帧；待核验候选在确认后才会进入这里。
            </div>
          </section>

          <!-- 右下：目标特征小卡片，点击就地编辑；底部新增特征 / 编辑目标组 -->
          <section class="fb-summary">
            <div class="fb-side-head">
              <span>目标特征说明</span>
              <em class="num">{{ featureCount }} 项特征</em>
              <div class="fb-head-actions">
                <button class="fb-btn fb-btn--xs fb-btn--add" @click="beginAddFeature">
                  <AppIcon name="plus" :size="11" />新增
                </button>
                <button class="fb-btn fb-btn--xs" @click="actions.openLatestGroupEditor()">编辑组</button>
                <button
                  v-if="target && target.id"
                  class="fb-btn fb-btn--xs fb-btn--danger"
                  title="删除这个要素（目标特征、定位点与时间轴卡片一起删除）"
                  @click="removeTarget"
                >
                  <AppIcon name="trash" :size="11" />删除目标
                </button>
              </div>
            </div>
            <div class="fb-cards">
              <div
                v-for="feature in targetFeatures"
                :key="feature.id"
                v-show="editingFeatureId !== feature.id"
                class="fb-card"
                :class="[`is-${featureTone(feature)}`, { 'is-pinned': feature.pinned, 'is-readonly': !feature.elementId }]"
                role="button"
                tabindex="0"
                :title="feature.elementId
                  ? `${feature.meta}${feature.conflict ? ' · ' + feature.conflict.reason : ''}（点击编辑）`
                  : `${feature.meta}（来自证据帧，编辑请用「编辑目标组」）`"
                @click="startEdit(feature)"
                @keydown.enter.prevent="startEdit(feature)"
              >
                <span class="fb-card-attr">{{ feature.attr }}</span>
                <strong class="fb-card-value">{{ feature.value }}</strong>
                <button
                  v-if="feature.pinned && !feature.elementId"
                  class="fb-card-pin"
                  title="取消固定"
                  @click.stop="actions.toggleFeaturePin(feature.id)"
                >
                  <AppIcon name="pin" :size="11" />
                </button>
              </div>

              <div v-if="editingFeature" class="fb-card fb-card--edit">
                <span class="fb-card-attr">{{ editingFeature.attr }}</span>
                <input
                  ref="editInput"
                  v-model="editingValue"
                  class="fb-card-input"
                  maxlength="120"
                  placeholder="输入特征内容，留空表示未知"
                  @keydown.enter.prevent="saveEdit(editingFeature)"
                  @keydown.esc.prevent="cancelEdit"
                />
                <span class="fb-card-actions">
                  <button class="fb-card-save" :disabled="savingFeature" title="保存" @click="saveEdit(editingFeature)">
                    <AppIcon name="check" :size="11" />
                  </button>
                  <button class="fb-card-cancel" @click="cancelEdit" title="取消">
                    <AppIcon name="close" :size="11" />
                  </button>
                  <button class="fb-card-del" :disabled="savingFeature" title="删除这条特征" @click="removeFeature(editingFeature)">
                    <AppIcon name="trash" :size="11" />
                  </button>
                </span>
              </div>

              <button v-if="!targetFeatures.length && !addingFeature" class="fb-card fb-card--empty" @click="beginAddFeature">
                <AppIcon name="plus" :size="12" />
                新增第一条特征
              </button>
            </div>

            <form v-if="addingFeature" class="fb-new" @submit.prevent="submitNewFeature">
              <input ref="newAttrInput" v-model="newAttr" class="fb-new-attr" maxlength="40" placeholder="特征名，如 上衣颜色" />
              <input v-model="newValue" class="fb-new-value" maxlength="120" placeholder="内容，如 白色T恤" />
              <button class="fb-btn" type="submit" :disabled="!newAttr.trim() || savingFeature">保存</button>
              <button class="fb-btn" type="button" @click="cancelAddFeature">取消</button>
            </form>
          </section>
        </div>
      </div>

      <div v-if="!target" class="fb-empty">
        <span class="fb-empty-icon"><AppIcon name="sliders" :size="18" /></span>
        <div class="fb-empty-text">
          <strong>还没有添加要素</strong>
          <span>添加目标要素后，这里按「目标截图 / 关联对象截图 + 补充证据帧 + 可编辑说明」展示；没有可靠观察时保持未知。</span>
        </div>
        <button class="btn btn--sm btn--primary" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="12" />添加目标要素
        </button>
      </div>
    </div>
  </section>

  <Teleport to="body">
    <div v-if="imageViewer" class="fb-viewer" role="dialog" aria-modal="false" :aria-label="`${imageViewer.title}大图`">
      <section class="fb-viewer-panel" @click.stop>
        <header class="fb-viewer-head">
          <div class="fb-viewer-title">
            <h3>{{ imageViewer.title }}</h3>
            <p>{{ targetCategory }} · {{ target?.timeLabel ?? "—" }} · 左：截图大图　右：完整图片</p>
          </div>
          <button class="fb-viewer-close" type="button" aria-label="关闭大图" title="关闭（Esc）" @click="closeImageViewer">
            <AppIcon name="close" :size="15" />
          </button>
        </header>

        <div class="fb-viewer-body">
          <figure class="fb-viewer-pane">
            <TargetCrop
              class="fb-viewer-crop"
              :src="shotUrl"
              :box="imageViewer.crop"
              :alt="imageViewer.title"
              empty-text="没有留存截图"
              fit="contain"
            />
            <figcaption>截图大图</figcaption>
          </figure>
          <figure class="fb-viewer-pane">
            <TargetCrop
              class="fb-viewer-full"
              :src="shotUrl"
              :box="null"
              alt="完整图片"
              empty-text="没有留存截图"
              fit="contain"
            />
            <figcaption>完整图片</figcaption>
          </figure>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.feature-bar {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  height: 100%;
  background: var(--surface-2);
  border-top: 1px solid var(--line);
}

.is-embedded .fb-body {
  padding: 7px 10px 8px;
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
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-1);
}

.fb-sep {
  color: var(--text-4);
}

.fb-tid {
  font-size: 12px;
  color: var(--text-2);
  letter-spacing: 0.02em;
}

.fb-badges {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
}

.fb-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 11.5px;
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

.fb-badge[data-tone="info"] {
  color: var(--info);
  border-color: rgba(96, 165, 250, 0.26);
}

.fb-add {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 9px;
  border-radius: 5px;
  border: 1px solid var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent-hi);
  font-size: 12px;
}

.fb-add:hover {
  border-color: var(--accent);
}

.fb-collapse {
  width: 24px;
  height: 24px;
}

.fb-body {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  padding: 8px 10px 10px;
  overflow: hidden;
}

/* ---------------- 左图右说明：整体高度跟随底部 dock ---------------- */

.fb-layout {
  display: grid;
  grid-template-columns: minmax(300px, 30%) minmax(0, 1fr);
  gap: 10px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.fb-shots {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.fb-shot {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  margin: 0;
  gap: 4px;
  overflow: hidden;
}

.fb-shot-open {
  display: flex;
  flex: 1;
  min-height: 0;
  width: 100%;
  padding: 0;
  border: 0;
  border-radius: 6px;
  overflow: hidden;
  background: transparent;
  color: inherit;
  cursor: zoom-in;
}

.fb-shot-open:disabled {
  cursor: default;
}

.fb-shot-open:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.fb-shot-open:hover .fb-crop {
  border-color: var(--line-3);
}

.fb-crop {
  flex: 1;
  min-height: 0;
  border-radius: 6px;
  border: 1px solid var(--line-2);
}

.fb-shot figcaption {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  flex: none;
  font-size: 11.5px;
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

/* ---------------- 右侧上下两段 ---------------- */

.fb-side {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  gap: 6px;
}

.fb-extra {
  flex: none;
  min-width: 0;
}

.fb-side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-2);
  height: 18px;
  flex: none;
}

.fb-side-head em {
  font-style: normal;
  font-size: 10.5px;
  color: var(--text-4);
  margin-right: auto;
}

.fb-add-mini {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 18px;
  padding: 0 5px;
  border: 1px solid var(--line-2);
  border-radius: 4px;
  background: transparent;
  color: var(--text-3);
  font-size: 11px;
}

.fb-add-mini:hover {
  border-color: var(--accent-line);
  color: var(--accent-hi);
}

.fb-head-actions {
  display: flex;
  align-items: center;
  gap: 3px;
  flex: none;
  margin-left: auto;
}

.fb-btn--xs {
  height: 18px;
  padding: 0 5px;
  gap: 2px;
  border-radius: 4px;
  font-size: 11px;
}

.fb-thumbs {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: 5px;
  max-height: 119px;
  overflow: hidden;
  min-height: 0;
}

.fb-thumb {
  position: relative;
  display: block;
  flex: none;
  width: 82px;
  height: 56px;
  padding: 0;
  overflow: hidden;
  border-radius: 5px;
  border: 1px solid var(--line-2);
  background: #0a0f15;
}

.fb-thumb:hover {
  border-color: var(--line-3);
}

.fb-thumb.is-selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.fb-thumb :deep(.crop) {
  border: 0;
  border-radius: 0;
  background: #0a0f15;
}

.fb-thumb-tag {
  position: absolute;
  left: 3px;
  bottom: 3px;
  padding: 0 4px;
  border-radius: 3px;
  background: rgba(5, 9, 14, 0.76);
  color: var(--text-1);
  font-size: 9.5px;
  line-height: 14px;
  pointer-events: none;
}

.fb-extra-empty {
  margin-top: 4px;
  padding: 5px 7px;
  border: 1px dashed var(--line-2);
  border-radius: 5px;
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--text-3);
}

.fb-summary {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  gap: 6px;
}

/* 小卡片流式排列；点卡片就地变成编辑框 */
.fb-cards {
  display: flex;
  flex-wrap: wrap;
  align-content: flex-start;
  gap: 5px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
  padding: 1px;
}

.fb-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  min-width: 92px;
  max-width: 210px;
  padding: 4px 7px;
  border: 1px solid var(--line-2);
  border-left-width: 2px;
  border-radius: 6px;
  background: #0b1017;
  color: var(--text-0);
  text-align: left;
  cursor: text;
}

.fb-card:hover {
  border-color: var(--accent-line);
  background: #0d1219;
}

.fb-card.is-warn {
  border-left-color: var(--warn);
}

.fb-card.is-danger {
  border-left-color: var(--danger);
}

.fb-card.is-ok {
  border-left-color: var(--ok);
}

.fb-card.is-muted {
  border-left-color: var(--line-3);
}

.fb-card.is-pinned {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.fb-card.is-readonly {
  cursor: default;
}

.fb-card-attr {
  max-width: 100%;
  overflow: hidden;
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fb-card-value {
  max-width: 100%;
  color: var(--text-0);
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.fb-card.is-warn .fb-card-value {
  color: var(--warn);
}

.fb-card.is-danger .fb-card-value {
  color: var(--danger);
}

.fb-card-pin {
  position: absolute;
  top: 2px;
  right: 2px;
  display: grid;
  place-items: center;
  width: 17px;
  height: 17px;
  border-radius: 3px;
  color: var(--accent-hi);
  background: #0b1017;
}

.fb-card--edit {
  flex: 1 1 210px;
  max-width: 100%;
  flex-direction: row;
  align-items: center;
  gap: 4px;
  border-color: var(--accent-line);
  background: #0d1219;
  cursor: default;
}

.fb-card--edit .fb-card-attr {
  flex: none;
  max-width: 68px;
  font-size: 10.5px;
}

.fb-card-input {
  flex: 1;
  min-width: 0;
  width: auto;
  height: 24px;
  padding: 0 5px;
  border: 1px solid var(--line-3);
  border-radius: 4px;
  background: #080c12;
  color: var(--text-0);
  font-size: 12.5px;
  font-family: inherit;
}

.fb-card-input:focus {
  outline: none;
  border-color: var(--accent);
}

.fb-card-actions {
  display: flex;
  gap: 3px;
  align-self: center;
  flex: none;
}

.fb-card-save,
.fb-card-cancel {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  border: 1px solid var(--line-3);
  color: var(--text-2);
}

.fb-card-save {
  color: var(--ok);
  border-color: rgba(76, 201, 146, 0.35);
}

.fb-card-del {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 4px;
  border: 1px solid rgba(242, 112, 106, 0.35);
  color: var(--danger);
}

.fb-card-del:hover:not(:disabled) {
  background: rgba(242, 112, 106, 0.12);
}

.fb-btn--danger {
  border-color: rgba(242, 112, 106, 0.35);
  color: var(--danger);
}

.fb-btn--danger:hover:not(:disabled) {
  border-color: var(--danger);
  color: var(--danger);
}

.fb-shot-del {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 16px;
  padding: 0 4px;
  border: 1px solid rgba(242, 112, 106, 0.3);
  border-radius: 3px;
  background: transparent;
  color: var(--danger);
  font-size: 10px;
  line-height: 1;
}

.fb-shot-del:hover {
  background: rgba(242, 112, 106, 0.12);
}

.fb-card-cancel {
  color: var(--text-3);
}

.fb-card--empty {
  flex-direction: row;
  align-items: center;
  gap: 4px;
  border-style: dashed;
  color: var(--text-2);
  font-size: 12px;
  cursor: pointer;
}

.fb-new {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  flex: none;
  padding: 6px;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: #0b1017;
}

.fb-new input {
  height: 26px;
  padding: 0 6px;
  border: 1px solid var(--line-3);
  border-radius: 4px;
  background: #080c12;
  color: var(--text-0);
  font-size: 12px;
  font-family: inherit;
}

.fb-new input:focus {
  outline: none;
  border-color: var(--accent);
}

.fb-new-attr {
  width: 118px;
}

.fb-new-value {
  flex: 1;
  min-width: 130px;
}

.fb-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 10px;
  border-radius: 5px;
  border: 1px solid var(--line-3);
  color: var(--text-1);
  font-size: 12px;
}

.fb-btn:hover:not(:disabled) {
  border-color: var(--accent-line);
  color: var(--accent-hi);
}

.fb-btn--add {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent-hi);
}

.fb-btn:disabled {
  opacity: 0.45;
  cursor: default;
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
/* ---------------- 截图大图查看窗（无遮罩） ---------------- */

.fb-viewer {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 22px;
  /* 无遮罩：不铺暗色背景，也不拦截窗口外的点击 */
  pointer-events: none;
}

.fb-viewer-panel {
  display: flex;
  flex-direction: column;
  width: min(1360px, 94vw);
  height: min(800px, 84vh);
  min-height: 0;
  padding: 12px;
  border: 1px solid var(--line-3);
  border-radius: 10px;
  background: #0a1017;
  box-shadow: 0 22px 70px rgba(0, 0, 0, 0.62);
  pointer-events: auto;
}

.fb-viewer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex: none;
  padding: 2px 2px 10px;
  border-bottom: 1px solid var(--line);
}

.fb-viewer-title {
  min-width: 0;
}

.fb-viewer-title h3 {
  margin: 0;
  color: var(--text-0);
  font-size: 14px;
  font-weight: 600;
}

.fb-viewer-title p {
  margin: 3px 0 0;
  color: var(--text-3);
  font-size: 10.5px;
}

.fb-viewer-close {
  display: grid;
  place-items: center;
  flex: none;
  width: 26px;
  height: 26px;
  border: 1px solid var(--line-2);
  border-radius: 5px;
  background: #111a24;
  color: var(--text-2);
}

.fb-viewer-close:hover {
  border-color: var(--line-3);
  color: var(--text-0);
}

.fb-viewer-body {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 12px;
  flex: 1;
  min-height: 0;
  padding-top: 10px;
}

.fb-viewer-pane {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
  min-height: 0;
  margin: 0;
}

.fb-viewer-pane figcaption {
  flex: none;
  color: var(--text-3);
  font-size: 10.5px;
}

.fb-viewer-crop,
.fb-viewer-full {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: #05090e;
}

@media (max-width: 900px) {
  .fb-viewer {
    padding: 10px;
  }

  .fb-viewer-panel {
    height: 90vh;
    padding: 10px;
  }

  .fb-viewer-body {
    grid-template-columns: minmax(0, 1fr);
    overflow-y: auto;
  }

  .fb-viewer-pane {
    min-height: 220px;
  }
}
</style>