<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import MockFrame from "../ui/MockFrame.vue";
import StatusChip from "../ui/StatusChip.vue";
import { cameras } from "../../data/mock";
import { actions, toast, ui } from "../../store/workbench";

type Box = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  kind: "person" | "face" | "vehicle";
  label: string;
  auto: boolean;
};

const boxes = ref<Box[]>([
  { id: "b-1", x: 0.482, y: 0.28, w: 0.185, h: 0.6, kind: "person", label: "人物", auto: false },
  { id: "b-2", x: 0.532, y: 0.295, w: 0.078, h: 0.095, kind: "face", label: "人脸", auto: true },
  { id: "b-3", x: 0.335, y: 0.55, w: 0.062, h: 0.17, kind: "person", label: "远处人形", auto: true },
]);

const selectedBoxId = ref<string | null>("b-1");
const selectedBox = computed(() => boxes.value.find((box) => box.id === selectedBoxId.value) ?? null);

const tool = ref<"select" | "draw">("select");
const scale = ref(1);
const canvas = ref<HTMLElement | null>(null);
const stage = ref<HTMLElement | null>(null);

const timeLabel = ref("20:12:08");
const timeUnknown = ref(false);
const cameraQuery = ref("东门入口 01");
const cameraCandidates = computed(() => {
  const keyword = cameraQuery.value.trim();
  const matched = cameras.filter((camera) => !keyword || `${camera.name} ${camera.site} ${camera.id}`.includes(keyword));
  const pool = matched.length ? matched : cameras.slice(0, 3);
  return pool.slice(0, 4).map((camera, index) => ({
    camera,
    score: Math.max(52, 96 - index * 9),
  }));
});
const confirmedCamera = ref<string | null>("CAM-01");
const sourceStatus = ref<"candidate" | "confirmed" | "unlocated">("confirmed");

const category = ref<"person" | "vehicle" | "other">("person");

const attributes = reactive([
  { key: "上衣", value: "深色长款外套", unknown: false },
  { key: "下装", value: "", unknown: true },
  { key: "鞋", value: "浅色运动鞋", unknown: false },
  { key: "随身物", value: "单肩包（右肩）", unknown: false },
]);

const note = ref("人工核对原录像通道号后添加；画面角标时间已与文件名交叉核对。");

const nextElementId = computed(() => `E${String(ui.nextElementSeq).padStart(2, "0")}`);

/* --------------------------- 框选交互 --------------------------- */

type DragState =
  | { mode: "move"; box: Box; startX: number; startY: number; originX: number; originY: number }
  | { mode: "resize"; box: Box; startX: number; startY: number; originW: number; originH: number }
  | { mode: "draw"; startX: number; startY: number; temp: Box };

let drag: DragState | null = null;
let tempId = 0;

const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

function normalize(event: PointerEvent) {
  const rect = stage.value?.getBoundingClientRect();
  if (!rect) return { x: 0, y: 0 };
  return { x: clamp01((event.clientX - rect.left) / rect.width), y: clamp01((event.clientY - rect.top) / rect.height) };
}

function beginMove(box: Box, event: PointerEvent) {
  if (tool.value === "draw") return;
  selectedBoxId.value = box.id;
  const point = normalize(event);
  drag = { mode: "move", box, startX: point.x, startY: point.y, originX: box.x, originY: box.y };
  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", endDrag);
}

function beginResize(box: Box, event: PointerEvent) {
  const point = normalize(event);
  drag = { mode: "resize", box, startX: point.x, startY: point.y, originW: box.w, originH: box.h };
  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", endDrag);
}

function beginDraw(event: PointerEvent) {
  const point = normalize(event);
  if (tool.value !== "draw") {
    selectedBoxId.value = null;
    return;
  }
  tempId += 1;
  const temp: Box = { id: `new-${tempId}`, x: point.x, y: point.y, w: 0.001, h: 0.001, kind: "person", label: "手动框", auto: false };
  boxes.value.push(temp);
  selectedBoxId.value = temp.id;
  drag = { mode: "draw", startX: point.x, startY: point.y, temp };
  window.addEventListener("pointermove", onDragMove);
  window.addEventListener("pointerup", endDrag);
}

function onDragMove(event: PointerEvent) {
  if (!drag) return;
  const point = normalize(event);
  if (drag.mode === "move") {
    drag.box.x = clamp01(drag.originX + (point.x - drag.startX));
    drag.box.y = clamp01(drag.originY + (point.y - drag.startY));
    drag.box.x = Math.min(drag.box.x, 1 - drag.box.w);
    drag.box.y = Math.min(drag.box.y, 1 - drag.box.h);
  } else if (drag.mode === "resize") {
    drag.box.w = Math.min(1 - drag.box.x, Math.max(0.03, point.x - drag.box.x));
    drag.box.h = Math.min(1 - drag.box.y, Math.max(0.03, point.y - drag.box.y));
  } else {
    drag.temp.x = Math.min(drag.startX, point.x);
    drag.temp.y = Math.min(drag.startY, point.y);
    drag.temp.w = Math.max(0.001, Math.abs(point.x - drag.startX));
    drag.temp.h = Math.max(0.001, Math.abs(point.y - drag.startY));
  }
}

function endDrag() {
  const state = drag;
  if (state?.mode === "draw" && (state.temp.w < 0.02 || state.temp.h < 0.02)) {
    boxes.value = boxes.value.filter((box) => box.id !== state.temp.id);
    selectedBoxId.value = null;
  }
  drag = null;
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", endDrag);
}

onBeforeUnmount(() => {
  window.removeEventListener("pointermove", onDragMove);
  window.removeEventListener("pointerup", endDrag);
});

function removeSelected() {
  if (!selectedBoxId.value) return;
  boxes.value = boxes.value.filter((box) => box.id !== selectedBoxId.value);
  selectedBoxId.value = null;
}

const boxStyle = (box: Box) => ({
  left: `${box.x * 100}%`,
  top: `${box.y * 100}%`,
  width: `${box.w * 100}%`,
  height: `${box.h * 100}%`,
});

/* --------------------------- 保存 --------------------------- */

watch(
  () => ui.addElementOpen,
  (open) => {
    if (!open) return;
    scale.value = 1;
    tool.value = "select";
    selectedBoxId.value = "b-1";
  },
);

function save(keepOpen: boolean) {
  if (!confirmedCamera.value) {
    toast("请先确认摄像头来源，或标注为「仅确定区域」", "warn");
    return;
  }
  const camera = cameras.find((c) => c.id === confirmedCamera.value);
  const status = sourceStatus.value === "confirmed" ? "confirmed" : "candidate";
  actions.saveElement({
    timeLabel: timeUnknown.value ? "时间未知" : timeLabel.value || "时间未知",
    cameraId: camera?.id ?? null,
    cameraLabel: camera ? `${camera.id} ${camera.name}` : "区域：南巷北口（待定位）",
    status,
    statusLabel: status === "confirmed" ? "来源已确认" : "候选待核验",
    note: note.value,
    tone: camera ? "color" : "gray",
    quality: camera ? "good" : "low",
  });
  ui.addElementOpen = keepOpen;
}
</script>

<template>
  <BaseModal
    :open="ui.addElementOpen"
    :title="`添加目标要素 · ${nextElementId}`"
    subtitle="左侧选择或手绘检测框，右侧核对角标时间、摄像头来源与可观察属性；原图与裁剪图分别保存。"
    width="xl"
    height="86vh"
    @close="ui.addElementOpen = false"
  >
    <div class="ae">
      <section class="ae-left">
        <div class="ae-tools">
          <div class="tool-group">
            <button :class="{ 'is-on': tool === 'select' }" @click="tool = 'select'">
              <AppIcon name="scan" :size="13" />选择
            </button>
            <button :class="{ 'is-on': tool === 'draw' }" @click="tool = 'draw'">
              <AppIcon name="crop" :size="13" />画框
            </button>
            <button :disabled="!selectedBoxId" @click="removeSelected">
              <AppIcon name="trash" :size="13" />删除
            </button>
          </div>
          <span class="tool-sep" />
          <div class="tool-group">
            <button aria-label="缩小画面" title="缩小" @click="scale = Math.max(1, Number((scale - 0.25).toFixed(2)))">
              <AppIcon name="minus" :size="13" />
            </button>
            <span class="zoom-text num">{{ Math.round(scale * 100) }}%</span>
            <button aria-label="放大画面" title="放大" @click="scale = Math.min(2.5, Number((scale + 0.25).toFixed(2)))">
              <AppIcon name="plus" :size="13" />
            </button>
            <button aria-label="适配画面" title="适配" @click="scale = 1"><AppIcon name="scan" :size="13" /></button>
          </div>
          <span class="tool-hint">
            {{ tool === "draw" ? "在画面中拖拽绘制新框" : "点击检测框选中；拖拽移动，拖右下角缩放" }}
          </span>
          <span class="tool-badge">示例画面（占位）</span>
        </div>

        <div
          ref="canvas"
          class="ae-canvas"
          :class="{ 'is-draw': tool === 'draw' }"
          @pointerdown="beginDraw"
        >
          <div ref="stage" class="ae-stage" :style="{ transform: `scale(${scale})` }">
            <MockFrame time="2026-10-25 20:12:08" label="CAM-01 东门入口 01" shape="wide" />

            <div
              v-for="box in boxes"
              :key="box.id"
              class="ae-box"
              :class="[`ae-box--${box.kind}`, { 'is-selected': selectedBoxId === box.id }]"
              :style="boxStyle(box)"
              @pointerdown.stop="beginMove(box, $event)"
            >
              <span class="ae-box-label">
                {{ box.label }}<span v-if="box.auto" class="ae-box-auto">自动</span>
              </span>
              <span v-if="selectedBoxId === box.id" class="ae-handle" @pointerdown.stop="beginResize(box, $event)" />
            </div>
          </div>
        </div>

        <div class="ae-under">
          <div class="ae-crop">
            <div class="ae-crop-frame">
              <MockFrame time="" label="" shape="portrait" :chrome="false" tone="color" />
            </div>
            <div class="ae-crop-meta">
              <span class="dim">当前选中框</span>
              <strong>{{ selectedBox ? selectedBox.label : "未选择" }}</strong>
              <span class="mono dim" v-if="selectedBox">
                x{{ selectedBox.x.toFixed(3) }} y{{ selectedBox.y.toFixed(3) }} w{{ selectedBox.w.toFixed(3) }} h{{ selectedBox.h.toFixed(3) }}
              </span>
            </div>
          </div>
          <div class="ae-boxes">
            <span class="dim">检测框</span>
            <span v-for="box in boxes" :key="`chip-${box.id}`" class="box-chip" :class="{ 'is-on': selectedBoxId === box.id }" @click="selectedBoxId = box.id">
              {{ box.label }}
              <i v-if="box.auto">自动</i>
            </span>
            <span class="dim ae-hint">裁剪图保存框选区域，原图及原坐标系坐标同时保留；不得生成式补造被遮挡细节。</span>
          </div>
        </div>
      </section>

      <aside class="ae-right scroll-y">
        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">画面角标时间</span>
            <button class="chip-btn" :class="{ 'is-on': timeUnknown }" @click="timeUnknown = !timeUnknown">
              {{ timeUnknown ? "已标记未知" : "标记未知" }}
            </button>
          </div>
          <input v-model="timeLabel" class="input num" :disabled="timeUnknown" aria-label="画面角标时间" />
          <p class="field-hint">OCR 原值保留；人工修改产生新版本，不覆盖原始识别结果。</p>
        </div>

        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">摄像头角标名称</span>
            <span class="chip" :data-tone="sourceStatus === 'confirmed' ? 'ok' : sourceStatus === 'candidate' ? 'warn' : 'muted'">
              {{ sourceStatus === "confirmed" ? "来源已确认" : sourceStatus === "candidate" ? "候选待核验" : "仅确定区域" }}
            </span>
          </div>
          <div class="ae-search">
            <AppIcon name="search" :size="13" />
            <input v-model="cameraQuery" type="text" placeholder="输入角标名称后自动查库" aria-label="摄像头角标名称" />
          </div>
          <ul class="candidate-list">
            <li v-for="item in cameraCandidates" :key="item.camera.id">
              <label class="candidate" :class="{ 'is-on': confirmedCamera === item.camera.id }">
                <input v-model="confirmedCamera" type="radio" :value="item.camera.id" @change="sourceStatus = 'confirmed'" />
                <span class="candidate-main">
                  <span class="candidate-name">{{ item.camera.name }}</span>
                  <span class="candidate-sub">{{ item.camera.site }} · {{ item.camera.channel }} · <span class="mono">{{ item.camera.uuid }}</span></span>
                </span>
                <span class="candidate-score num">{{ item.score }}%</span>
                <span class="tag">Map {{ item.camera.source }}</span>
              </label>
            </li>
          </ul>
          <p class="field-hint">相似度只表示名称匹配程度，<b>不是</b>目标身份概率，也不表示视频确实来自该镜头。</p>
          <div class="source-actions">
            <button class="btn btn--sm btn--outline" @click="sourceStatus = 'unlocated'; confirmedCamera = null">
              <AppIcon name="map" :size="12" />仅确定区域
            </button>
            <button class="btn btn--sm btn--ghost" @click="toast('可在地图上点选镜头或圈定大致区域（演示）', 'info')">
              从地图选镜头
            </button>
          </div>
        </div>

        <div class="ae-section">
          <span class="field-label">目标类别</span>
          <div class="seg">
            <button :class="{ 'is-on': category === 'person' }" @click="category = 'person'">人员</button>
            <button :class="{ 'is-on': category === 'vehicle' }" @click="category = 'vehicle'">车辆</button>
            <button :class="{ 'is-on': category === 'other' }" @click="category = 'other'">其他</button>
          </div>
        </div>

        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">可观察属性（特征候选）</span>
            <span class="field-hint">保存后进入待核查</span>
          </div>
          <div v-for="attr in attributes" :key="attr.key" class="attr-row">
            <span class="attr-key">{{ attr.key }}</span>
            <input v-model="attr.value" class="input" :disabled="attr.unknown" :placeholder="attr.unknown ? '未知' : '可观察描述'" />
            <button class="chip-btn" :class="{ 'is-on': attr.unknown }" @click="attr.unknown = !attr.unknown">{{ attr.unknown ? "未知" : "标未知" }}</button>
          </div>
        </div>

        <div class="ae-section">
          <span class="field-label">备注 / 来源说明</span>
          <textarea v-model="note" class="textarea" rows="2" />
        </div>

        <div class="ae-section ae-section--muted">
          <span class="field-label">来源与画面质量</span>
          <p class="field-hint">原始文件：东门入口01_20261025_190700_201700.mp4 · 角标 OCR 置信度 0.86 · 画面夜间低照度，颜色不可靠。</p>
          <div class="quality-row">
            <StatusChip label="夜间低照度" tone="warn" />
            <StatusChip label="颜色证据不可靠" tone="warn" />
            <StatusChip label="原件只读" tone="ok" />
          </div>
        </div>
      </aside>
    </div>

    <template #footer>
      <p class="ae-foot-note">保存后不会自动启动或扩大研判；未核验属性仅作为特征候选，不会覆盖已核验特征。</p>
      <span class="grow" />
      <button class="btn btn--ghost" @click="ui.addElementOpen = false">取消</button>
      <button class="btn btn--outline" @click="save(true)">保存并继续添加</button>
      <button class="btn btn--primary" @click="save(false)">保存为目标要素 {{ nextElementId }}</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.ae {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 384px;
  gap: 0;
  min-height: 0;
  flex: 1;
}

.ae-left {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px 12px;
  min-width: 0;
  min-height: 0;
  border-right: 1px solid var(--line);
}

.ae-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.tool-group {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--bg-2);
}

.tool-group button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 7px;
  border-radius: 4px;
  font-size: 10px;
  color: var(--text-2);
}

.tool-group button:hover:not(:disabled) {
  background: var(--bg-3);
  color: var(--text-0);
}

.tool-group button.is-on {
  background: var(--accent-soft);
  color: var(--accent-hi);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.tool-group button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.zoom-text {
  font-size: 9.5px;
  color: var(--text-3);
  padding: 0 2px;
}

.tool-sep {
  width: 1px;
  height: 16px;
  background: var(--line-2);
}

.tool-hint {
  font-size: 9.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-badge {
  margin-left: auto;
  font-size: 9px;
  color: var(--text-4);
  border: 1px dashed var(--line-2);
  border-radius: 4px;
  padding: 1px 6px;
  white-space: nowrap;
}

.ae-canvas {
  position: relative;
  flex: 1;
  min-height: 0;
  border-radius: var(--radius);
  border: 1px solid var(--line-2);
  overflow: hidden;
  background: #060a0f;
  touch-action: none;
  cursor: crosshair;
}

.ae-canvas.is-draw {
  cursor: crosshair;
}

.ae-stage {
  position: absolute;
  inset: 0;
  transform-origin: center;
  transition: transform 0.16s var(--ease);
}

.ae-box {
  position: absolute;
  border: 1.5px solid rgba(77, 141, 255, 0.8);
  background: rgba(77, 141, 255, 0.08);
  border-radius: 2px;
  cursor: move;
}

.ae-box--face {
  border-color: rgba(79, 209, 217, 0.85);
  background: rgba(79, 209, 217, 0.08);
}

.ae-box--person {
  border-color: rgba(47, 207, 146, 0.85);
  background: rgba(47, 207, 146, 0.07);
}

.ae-box.is-selected {
  border-color: #cfe0ff;
  border-width: 2px;
  box-shadow: 0 0 0 1px rgba(6, 10, 15, 0.9), 0 0 14px rgba(77, 141, 255, 0.4);
  background: rgba(77, 141, 255, 0.12);
}

.ae-box-auto {
  margin-left: 3px;
  font-size: 7px;
  color: var(--text-3);
  background: rgba(0, 0, 0, 0.4);
  border-radius: 2px;
  padding: 0 2px;
}

.ae-box-label {
  position: absolute;
  top: -17px;
  left: -1px;
  display: inline-flex;
  align-items: center;
  font-size: 9px;
  color: #dbe8ff;
  background: rgba(10, 16, 24, 0.92);
  border: 1px solid rgba(77, 141, 255, 0.45);
  border-radius: 3px;
  padding: 0 4px;
  line-height: 14px;
  white-space: nowrap;
}

.ae-box--person .ae-box-label {
  border-color: rgba(47, 207, 146, 0.45);
  color: #c9f7e6;
}

.ae-box--face .ae-box-label {
  border-color: rgba(79, 209, 217, 0.45);
  color: #c8f4f7;
}

.ae-handle {
  position: absolute;
  right: -4px;
  bottom: -4px;
  width: 9px;
  height: 9px;
  border-radius: 2px;
  background: #cfe0ff;
  border: 1px solid #0a1018;
  cursor: nwse-resize;
}

.ae-under {
  display: flex;
  gap: 12px;
  flex: none;
  min-height: 0;
}

.ae-crop {
  display: flex;
  gap: 8px;
  align-items: center;
  flex: none;
}

.ae-crop-frame {
  width: 52px;
  height: 66px;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--line-2);
  background: #060a0f;
}

.ae-crop-meta {
  display: grid;
  gap: 2px;
  font-size: 9px;
  min-width: 0;
}

.ae-crop-meta strong {
  font-size: 11px;
  color: var(--text-1);
  font-weight: 600;
}

.ae-crop-meta .mono {
  font-size: 8px;
  color: var(--text-4);
}

.ae-boxes {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
  font-size: 9.5px;
}

.box-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 18px;
  padding: 0 6px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  color: var(--text-2);
  font-size: 9.5px;
  cursor: pointer;
}

.box-chip.is-on {
  border-color: var(--accent-line);
  color: var(--accent-hi);
  background: var(--accent-soft);
}

.box-chip i {
  font-style: normal;
  font-size: 7.5px;
  color: var(--text-4);
  border: 1px solid var(--line-2);
  border-radius: 2px;
  padding: 0 2px;
}

.ae-hint {
  flex-basis: 100%;
  font-size: 9px;
  line-height: 1.4;
}

/* 右侧字段 */
.ae-right {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 14px 14px;
  min-height: 0;
}

.ae-section {
  display: grid;
  gap: 5px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.ae-section:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.ae-section--muted {
  padding: 8px;
  border: 1px dashed var(--line-2);
  border-radius: var(--radius-sm);
}

.ae-field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.ae-field-head .field-hint {
  font-size: 9px;
}

.chip-btn {
  height: 20px;
  padding: 0 6px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  color: var(--text-3);
  font-size: 9.5px;
  white-space: nowrap;
}

.chip-btn:hover {
  color: var(--text-1);
  border-color: var(--line-3);
}

.chip-btn.is-on {
  color: #f3c47c;
  border-color: rgba(240, 180, 90, 0.4);
  background: var(--warn-soft);
}

.ae-search {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid var(--line-2);
  background: var(--bg-input);
  color: var(--text-3);
}

.ae-search input {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  outline: none;
  font-size: 10.5px;
  color: var(--text-1);
}

.candidate-list {
  display: grid;
  gap: 3px;
}

.candidate {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 6px;
  border-radius: 5px;
  border: 1px solid var(--line);
  background: var(--bg-2);
  cursor: pointer;
  min-width: 0;
}

.candidate:hover {
  border-color: var(--line-3);
}

.candidate.is-on {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.candidate input {
  accent-color: var(--accent);
  width: 11px;
  height: 11px;
  margin: 0;
  flex: none;
}

.candidate-main {
  display: grid;
  min-width: 0;
  line-height: 1.25;
}

.candidate-name {
  font-size: 10.5px;
  color: var(--text-1);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-sub {
  font-size: 8.5px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.candidate-score {
  font-size: 9px;
  color: var(--text-3);
  flex: none;
}

.candidate .tag {
  flex: none;
  font-size: 7.5px;
}

.source-actions {
  display: flex;
  gap: 5px;
}

.seg {
  display: flex;
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--bg-2);
  width: fit-content;
}

.seg button {
  height: 22px;
  padding: 0 12px;
  border-radius: 4px;
  font-size: 10px;
  color: var(--text-2);
}

.seg button.is-on {
  background: var(--accent-soft);
  color: var(--accent-hi);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.attr-row {
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px;
}

.attr-key {
  font-size: 10px;
  color: var(--text-3);
}

.attr-row .input {
  height: 26px;
  font-size: 10.5px;
}

.quality-row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.ae-foot-note {
  font-size: 9.5px;
  color: var(--text-3);
}

.grow {
  flex: 1;
}

@media (max-width: 1200px) {
  .ae {
    grid-template-columns: minmax(0, 1fr) 330px;
  }

  .tool-hint {
    display: none;
  }
}
</style>
