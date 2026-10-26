<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import ProgressBar from "./ui/ProgressBar.vue";
import StatusChip from "./ui/StatusChip.vue";
import { actions, canStartRound, indexView, roundView, startConditions, toast, visibleElements, wb } from "../store/workbench";

const latestElement = computed(() => visibleElements.value[visibleElements.value.length - 1] ?? null);
const selectedElement = computed(() => visibleElements.value.find((e) => e.id === wb.selectedAnchorId) ?? null);
const unmet = computed(() => startConditions.value.filter((c) => !c.ok).map((c) => c.label));
const allOk = computed(() => unmet.value.length === 0);
const roundActive = computed(() => wb.roundStatus === "running" || wb.roundStatus === "paused");

const roundTone = computed(() => {
  if (wb.roundStatus === "running") return "info";
  if (wb.roundStatus === "paused") return "warn";
  if (wb.roundStatus === "review") return "ok";
  if (wb.roundStatus === "done") return "muted";
  return "muted";
});

const roundLabel = computed(() => {
  const map: Record<string, string> = {
    running: "分析中",
    paused: "已暂停",
    review: "待人工复核",
    done: "已终止 / 完成",
    ready: "可启动",
    draft: "未启动",
  };
  return map[wb.roundStatus] ?? wb.roundStatus;
});

const formatBytes = (n: number) => n.toLocaleString("zh-CN");

function pickDirectory() {
  toast("目录选择器需要后端 allowed_roots 支持（当前为 UI 原型，未接线）", "info");
}

function refreshIndex() {
  toast("已触发增量索引（演示动作，未接线）", "ok");
}

function applyRangePreset(hours: number | "anchor") {
  const to = wb.to;
  const [h, m] = to.split(":").map(Number);
  const toMinutes = (h || 0) * 60 + (m || 0);
  const from = hours === "anchor" ? 1080 : Math.max(0, toMinutes - hours * 60);
  const fmt = (total: number) => `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  actions.setRange(hours === "anchor" ? "18:00" : fmt(from), to);
}
</script>

<template>
  <section class="task-panel scroll-y" aria-label="任务操作区">
    <!-- 录像目录 -->
    <article class="card block">
      <header class="block-head">
        <span class="card-title">录像目录</span>
        <StatusChip :label="indexView.status" :tone="indexView.dir ? 'warn' : 'muted'" dot />
        <span class="block-head-meta">{{ indexView.dir ? `上次索引 ${indexView.lastRun}` : "待选择" }}</span>
        <button class="btn btn--sm btn--ghost" @click="actions.openCoverage()">
          <AppIcon name="video" :size="12" />覆盖时间轴
        </button>
        <button class="btn btn--sm btn--outline" @click="pickDirectory">
          <AppIcon name="folder" :size="12" />选择目录
        </button>
      </header>

      <div class="dir-row">
        <span class="dir-icon"><AppIcon name="folder" :size="13" /></span>
        <span class="dir-path mono truncate" :class="{ 'is-empty': !indexView.dir }">
          {{ indexView.dir ?? "未选择服务端录像目录（浏览器本地路径不可直接使用）" }}
        </span>
        <span class="dir-pct num">{{ indexView.progress }}%</span>
        <button class="icon-btn" aria-label="增量刷新目录" title="增量刷新（不重启研判）" @click="refreshIndex">
          <AppIcon name="refresh" :size="13" />
        </button>
        <ProgressBar class="dir-bar" :value="indexView.progress" tone="ok" :height="2" label="资源索引进度" />
      </div>

      <div class="metrics">
        <div><dt>文件</dt><dd class="num">{{ formatBytes(indexView.files) }}</dd></div>
        <div><dt>已解析时间</dt><dd class="num">{{ formatBytes(indexView.parsed) }}</dd></div>
        <div><dt>已映射</dt><dd class="num">{{ indexView.mapped }}</dd></div>
        <div><dt>未映射</dt><dd class="num warn">{{ indexView.unmapped }}</dd></div>
        <div><dt>异常</dt><dd class="num danger">{{ indexView.errors }}</dd></div>
        <div class="metrics-note"><dt>索引 ≠ 目标检测</dt></div>
      </div>
    </article>

    <!-- 目标要素 -->
    <article class="card block">
      <header class="block-head">
        <span class="card-title">目标要素</span>
        <span class="chip" data-tone="violet">{{ visibleElements.length }} 个</span>
        <span class="block-head-meta">
          {{ latestElement ? `最近 ${latestElement.id} · ${latestElement.timeLabel}` : "尚未添加" }}
        </span>
        <button class="btn btn--sm btn--outline" @click="actions.openAddElement()">
          <AppIcon name="plus" :size="12" />添加要素
        </button>
        <button class="btn btn--sm btn--ghost" @click="actions.openElements()">
          <AppIcon name="list" :size="12" />列表
        </button>
      </header>

      <div v-if="selectedElement" class="element-row">
        <span class="element-tag">{{ selectedElement.id }}</span>
        <span class="element-main truncate">{{ selectedElement.cameraLabel }} · {{ selectedElement.timeLabel }}</span>
        <span class="chip" :data-tone="selectedElement.status === 'confirmed' ? 'ok' : selectedElement.status === 'candidate' ? 'warn' : 'muted'">
          {{ selectedElement.statusLabel }}
        </span>
        <button class="btn btn--sm btn--ghost" @click="actions.openElements()">定位</button>
        <button class="btn btn--sm btn--ghost" @click="actions.openAddElement(selectedElement.id)">编辑</button>
      </div>
      <p v-else class="empty-line">尚无目标要素；添加后可在时间线与地图生成起始锚点。</p>
    </article>

    <!-- 研判控制 -->
    <article class="card block block--control">
      <header class="block-head">
        <span class="card-title">研判控制</span>
        <span class="chip" data-tone="violet">轮次 {{ wb.emptyMode ? "—" : roundView.id }}</span>
        <StatusChip :label="roundLabel" :tone="roundTone" dot />
        <span class="block-head-meta">{{ wb.emptyMode ? "未启动" : `${roundView.stage} · ${wb.roundProgress}%` }}</span>
        <span class="grow" />
        <span class="dim round-auth">授权：{{ wb.authorized }}</span>
      </header>

      <div class="control-grid">
        <div class="control-cell">
          <span class="field-label">追查方向</span>
          <div class="dir-toggle">
            <button :class="{ 'is-on': wb.direction === 'backward' }" :aria-pressed="wb.direction === 'backward'" @click="actions.setDirection('backward')">
              <AppIcon name="arrow-left" :size="13" />向前倒查
            </button>
            <button :class="{ 'is-on': wb.direction === 'forward' }" :aria-pressed="wb.direction === 'forward'" @click="actions.setDirection('forward')">
              向后追查<AppIcon name="arrow-right" :size="13" />
            </button>
          </div>
        </div>

        <div class="control-cell control-cell--range">
          <span class="field-label">时间范围</span>
          <div class="range-row">
            <input v-model="wb.from" class="input input--time num" type="time" step="60" aria-label="起始时间" />
            <span class="dim">→</span>
            <input v-model="wb.to" class="input input--time num" type="time" step="60" aria-label="结束时间" />
            <div class="range-presets">
              <button class="chip-btn" @click="applyRangePreset(2)">2h</button>
              <button class="chip-btn" @click="applyRangePreset(4)">4h</button>
              <button class="chip-btn" @click="applyRangePreset('anchor')">至锚点</button>
            </div>
          </div>
        </div>

        <div class="control-cell control-cell--actions">
          <template v-if="wb.roundStatus === 'running'">
            <button class="btn btn--sm btn--outline" @click="actions.pauseRound()">
              <AppIcon name="pause" :size="12" />暂停
            </button>
            <button class="btn btn--sm btn--danger" @click="actions.stopRound()">
              <AppIcon name="stop" :size="12" />终止
            </button>
          </template>
          <template v-else-if="wb.roundStatus === 'paused'">
            <button class="btn btn--sm btn--primary" @click="actions.resumeRound()">
              <AppIcon name="play" :size="12" />继续
            </button>
            <button class="btn btn--sm btn--danger" @click="actions.stopRound()">
              <AppIcon name="stop" :size="12" />终止
            </button>
          </template>
          <template v-else>
            <button class="btn btn--primary btn--sm" :disabled="!canStartRound" @click="actions.startRound()">
              <AppIcon name="play" :size="12" />开始此轮研判
            </button>
          </template>
        </div>
      </div>

      <div class="ready-line" :class="{ 'is-ok': allOk && !roundActive, 'is-active': roundActive }">
        <AppIcon :name="roundActive ? 'scan' : allOk ? 'check' : 'warning'" :size="12" />
        <span v-if="roundActive">本轮{{ wb.roundStatus === "paused" ? "已暂停" : "进行中" }}：已分析 {{ roundView.done }} / {{ roundView.done + roundView.queued }} 段 · 暂停不会丢失证据</span>
        <span v-else-if="allOk">启动条件已满足（4 / 4）：目录可访问 · 有效锚点 · 方向已选 · 范围在授权内</span>
        <span v-else>未满足：{{ unmet.join(" · ") }}</span>
        <span class="ready-hint">方向变更需新增轮次，不能静默改写运行历史</span>
      </div>
    </article>
  </section>
</template>

<style scoped>
.task-panel {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 5px 8px;
  min-height: 0;
  border-right: 1px solid var(--line);
}

.block {
  padding: 5px 8px;
  display: grid;
  gap: 4px;
  flex: none;
}

.block-head {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.block-head-meta {
  font-size: 9.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.block-head .btn {
  margin-left: 0;
}

.block-head .btn:last-child {
  margin-left: auto;
}

.grow {
  flex: 1;
}

.round-auth {
  font-size: 9.5px;
  white-space: nowrap;
}

/* 目录 */
.dir-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  height: 24px;
  padding: 0 4px 3px 8px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--bg-input);
  min-width: 0;
  overflow: hidden;
}

.dir-bar {
  position: absolute;
  left: 4px;
  right: 4px;
  bottom: 2px;
  width: auto;
}

.dir-pct {
  flex: none;
  font-size: 9px;
  color: var(--text-3);
}

.dir-icon {
  color: var(--text-3);
  display: inline-flex;
}

.dir-path {
  font-size: 10.5px;
  color: var(--text-1);
  flex: 1;
  min-width: 0;
}

.dir-path.is-empty {
  color: var(--text-4);
  font-family: var(--font);
}

.dir-row .icon-btn {
  width: 20px;
  height: 20px;
}

.metrics {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 2px 12px;
}

.metrics > div {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

.metrics dt {
  font-size: 8.5px;
  color: var(--text-3);
  white-space: nowrap;
}

.metrics dd {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-0);
  line-height: 1.2;
}

.metrics .warn {
  color: var(--warn);
}

.metrics .danger {
  color: var(--danger);
}

.metrics-note {
  margin-left: auto;
}

.metrics-note dt {
  color: var(--text-4);
  font-size: 8.5px;
}

/* 要素 */
.element-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  height: 23px;
  padding: 0 4px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.022);
  border: 1px solid var(--line);
}

.element-tag {
  flex: none;
  font-size: 10px;
  font-weight: 700;
  color: #c3aeff;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--violet-soft);
}

.element-main {
  flex: 1;
  font-size: 10.5px;
  color: var(--text-1);
}

.element-row .btn {
  height: 20px;
  font-size: 9.5px;
  padding: 0 6px;
}

.empty-line {
  font-size: 10px;
  color: var(--text-3);
}

/* 控制 */
.control-grid {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  min-width: 0;
}

.control-cell {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.control-cell--range {
  flex: 1;
}

.control-cell--actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 5px;
  flex: none;
}

.dir-toggle {
  display: flex;
  padding: 2px;
  border-radius: 6px;
  background: var(--bg-2);
  border: 1px solid var(--line);
}

.dir-toggle button {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 21px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 10px;
  color: var(--text-2);
  white-space: nowrap;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}

.dir-toggle button:hover {
  color: var(--text-0);
}

.dir-toggle button.is-on {
  background: var(--accent-soft);
  color: var(--accent-hi);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.range-row {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.input--time {
  width: 64px;
  height: 24px;
  font-size: 10.5px;
  padding: 0 4px;
}

.input--time::-webkit-calendar-picker-indicator {
  display: none;
}

.range-presets {
  display: flex;
  gap: 3px;
}

.chip-btn {
  height: 20px;
  padding: 0 4px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  color: var(--text-3);
  font-size: 9px;
  white-space: nowrap;
}

.chip-btn:hover {
  color: var(--text-1);
  border-color: var(--line-3);
}

.ready-line {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 3px 6px;
  border-radius: 5px;
  font-size: 9px;
  color: #f3c47c;
  background: var(--warn-soft);
  border: 1px solid rgba(240, 180, 90, 0.22);
  min-width: 0;
}

.ready-line.is-ok {
  color: #6fe4b6;
  background: var(--ok-soft);
  border-color: rgba(47, 207, 146, 0.22);
}

.ready-line.is-active {
  color: #a9c8ff;
  background: var(--accent-soft);
  border-color: var(--accent-line);
}

.ready-line span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ready-hint {
  margin-left: auto;
  color: var(--text-3);
  flex: none;
}

/* 左栏收窄后，时间范围预设让位给起止时间输入 */
@media (max-width: 1500px) {
  .range-presets {
    display: none;
  }
}
</style>
