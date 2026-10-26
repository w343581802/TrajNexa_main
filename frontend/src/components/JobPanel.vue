<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import MockFrame from "./ui/MockFrame.vue";
import ProgressBar from "./ui/ProgressBar.vue";
import StatusChip from "./ui/StatusChip.vue";
import { cameras, type Job } from "../data/mock";
import { actions, indexView, roundView, selectedJob, visibleJobs, wb } from "../store/workbench";

const toneOf = (job: Job): "ok" | "warn" | "danger" | "info" | "muted" => {
  switch (job.status) {
    case "review":
      return "warn";
    case "failed":
    case "canceled":
      return "danger";
    case "done":
      return "ok";
    case "queued":
    case "nodetect":
      return "muted";
    default:
      return "info";
  }
};

const barTone = (job: Job) => {
  const tone = toneOf(job);
  return tone === "info" ? "accent" : tone;
};

const cameraName = (id: string) => {
  const camera = cameras.find((c) => c.id === id);
  return camera ? `${camera.id} ${camera.name}` : `${id} · 待映射`;
};

const reviewCount = computed(() => roundView.value.review);

function preview(job: Job) {
  wb.previewJobId = job.id;
  actions.togglePreview(true);
  actions.selectJob(job.id);
}

function togglePreview(checked: boolean) {
  actions.togglePreview(checked);
  if (checked && !wb.previewJobId) {
    wb.previewJobId = visibleJobs.value[0]?.id ?? null;
  }
}
</script>

<template>
  <section class="job-panel" aria-label="索引进度、分析作业与视频预览">
    <div class="status-row">
      <article class="prog-card">
        <header class="prog-head">
          <span class="prog-name">资源索引</span>
          <span class="prog-sub dim">{{ indexView.status }}</span>
          <span class="num prog-val">{{ indexView.progress }}%</span>
        </header>
        <ProgressBar :value="indexView.progress" tone="ok" :height="3" label="资源索引进度" />
        <div class="prog-meta">
          <span>文件 <b class="num">{{ indexView.files.toLocaleString("zh-CN") }}</b></span>
          <span>已解析 <b class="num">{{ indexView.parsed.toLocaleString("zh-CN") }}</b></span>
          <span>已映射 <b class="num">{{ indexView.mapped }}</b></span>
          <span>未映射 <b class="num warn">{{ indexView.unmapped }}</b></span>
          <span>异常 <b class="num danger">{{ indexView.errors }}</b></span>
        </div>
      </article>

      <article class="prog-card">
        <header class="prog-head">
          <span class="prog-name">目标研判</span>
          <span class="chip" data-tone="violet">{{ wb.emptyMode ? "—" : roundView.id }}</span>
          <span class="prog-sub">{{ wb.emptyMode ? "未启动" : roundView.stage }}</span>
          <span class="num prog-val">{{ wb.emptyMode ? 0 : roundView.progress }}%</span>
        </header>
        <ProgressBar :value="wb.emptyMode ? 0 : roundView.progress" tone="accent" :height="3" label="目标研判进度" />
        <div class="prog-meta">
          <span>待分析 <b class="num">{{ roundView.queued }}</b></span>
          <span>完成 <b class="num">{{ roundView.done }}</b></span>
          <span>失败 <b class="num danger">{{ roundView.failed }}</b></span>
          <span>待复核 <b class="num warn">{{ reviewCount }}</b></span>
        </div>
      </article>

      <div class="quick">
        <div class="quick-metric">
          <span>待复核</span>
          <b class="num">{{ reviewCount }}</b>
        </div>
        <label class="switch-row" title="默认关闭；关闭预览不影响后端分析">
          <input type="checkbox" :checked="wb.previewOn" @change="togglePreview(($event.target as HTMLInputElement).checked)" />
          <span class="switch-track"><i /></span>
          <span>视频预览</span>
        </label>
      </div>
    </div>

    <div class="jobs-area">
      <div class="jobs-col">
        <header class="jobs-head">
          <span class="sec-title">分析作业</span>
          <span class="chip" data-tone="muted">{{ visibleJobs.length }}</span>
          <span class="dim jobs-hint">点击作业定位地图摄像头；预览默认关闭</span>
        </header>

        <div class="jobs-list scroll-x">
          <div v-if="!visibleJobs.length" class="jobs-empty">
            <AppIcon name="video" :size="16" />
            <p>尚无研判作业</p>
            <span>选择录像目录、添加目标要素并开始一轮研判后，作业会出现在这里（索引本身不会触发检测）。</span>
          </div>

          <article
            v-for="job in visibleJobs"
            :key="job.id"
            class="job"
            :class="[`job--${toneOf(job)}`, { 'is-selected': wb.selectedJobId === job.id }]"
            @click="actions.selectJob(job.id)"
          >
            <header class="job-head">
              <span class="job-id mono">{{ job.id }}</span>
              <StatusChip :label="job.statusLabel" :tone="toneOf(job)" dot />
              <button class="icon-btn job-preview" aria-label="预览作业" title="按需预览片段" @click.stop="preview(job)">
                <AppIcon name="play" :size="12" />
              </button>
            </header>
            <p class="job-file truncate" :title="job.file">{{ job.file }}</p>
            <p class="job-sub truncate">{{ cameraName(job.cameraId) }} · <span class="num">{{ job.from }}–{{ job.to }}</span></p>
            <ProgressBar :value="job.progress" :tone="barTone(job)" :height="2" :label="`${job.id} 进度`" />
            <footer class="job-foot">
              <span class="job-counts">
                <span>候选 <b class="num">{{ job.candidates }}</b></span>
                <span v-if="job.undetected > 0">未检出 <b class="num">{{ job.undetected }}</b></span>
              </span>
              <span v-if="job.note" class="job-note truncate" :title="job.note">{{ job.note }}</span>
              <span v-else class="num job-progress">{{ job.progress }}%</span>
            </footer>
          </article>
        </div>
      </div>

      <aside v-if="wb.previewOn" class="preview">
        <header class="preview-head">
          <span class="card-title">按需预览</span>
          <span class="chip" data-tone="warn">单路</span>
          <button class="icon-btn" aria-label="关闭预览" title="关闭预览（不影响后端分析）" @click="actions.togglePreview(false)">
            <AppIcon name="close" :size="13" />
          </button>
        </header>
        <div class="preview-frame">
          <MockFrame
            v-if="selectedJob"
            :label="cameraName(selectedJob.cameraId)"
            :time="`2026-10-25 ${selectedJob.from}:00`"
            shape="wide"
            :chrome="true"
          />
          <div v-else class="preview-placeholder">选择作业后加载授权片段</div>
        </div>
        <p class="preview-meta">
          <span v-if="selectedJob" class="truncate">{{ selectedJob.file }}</span>
          <span v-else>—</span>
        </p>
        <p class="preview-note">原始录像由后端按时间范围读取；前端只取授权片段。关闭预览不暂停分析。</p>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.job-panel {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 8px 10px;
  min-height: 0;
  min-width: 0;
}

.status-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr) 116px;
  gap: 7px;
  flex: none;
}

.prog-card {
  display: grid;
  gap: 5px;
  padding: 7px 9px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background: var(--bg-2);
  min-width: 0;
}

.prog-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.prog-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-1);
  white-space: nowrap;
}

.prog-sub {
  font-size: 9.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.prog-val {
  margin-left: auto;
  font-size: 13px;
  font-weight: 650;
  color: var(--text-0);
  line-height: 1;
}

.prog-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: 9px;
  color: var(--text-3);
}

.prog-meta b {
  color: var(--text-1);
  font-weight: 600;
}

.prog-meta .warn {
  color: var(--warn);
}

.prog-meta .danger {
  color: var(--danger);
}

.quick {
  display: grid;
  align-content: center;
  gap: 7px;
  padding: 6px 9px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  background: var(--bg-2);
}

.quick-metric {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}

.quick-metric span {
  font-size: 9.5px;
  color: var(--text-3);
}

.quick-metric b {
  font-size: 16px;
  font-weight: 650;
  color: var(--warn);
  line-height: 1;
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 9.5px;
  color: var(--text-2);
  cursor: pointer;
  white-space: nowrap;
}

.switch-row input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.switch-track {
  position: relative;
  width: 24px;
  height: 13px;
  border-radius: 99px;
  background: var(--bg-4);
  border: 1px solid var(--line-2);
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
  flex: none;
}

.switch-track i {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--text-3);
  transition: transform var(--dur) var(--ease), background var(--dur) var(--ease);
}

.switch-row input:checked + .switch-track {
  background: var(--accent-soft);
  border-color: var(--accent-line);
}

.switch-row input:checked + .switch-track i {
  background: var(--accent-hi);
  transform: translateX(11px);
}

.switch-row input:focus-visible + .switch-track {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* 作业区 */
.jobs-area {
  display: flex;
  gap: 7px;
  flex: 1;
  min-height: 0;
}

.jobs-col {
  display: flex;
  flex-direction: column;
  gap: 5px;
  flex: 1;
  min-width: 0;
  min-height: 0;
}

.jobs-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.jobs-hint {
  font-size: 9.5px;
  margin-left: auto;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.jobs-list {
  display: flex;
  gap: 7px;
  align-items: stretch;
  flex: 1;
  min-height: 0;
  padding-bottom: 2px;
}

.jobs-empty {
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 4px;
  width: 100%;
  padding: 12px;
  border: 1px dashed var(--line-2);
  border-radius: var(--radius);
  color: var(--text-3);
  text-align: center;
}

.jobs-empty p {
  font-size: 11px;
  color: var(--text-1);
  font-weight: 600;
}

.jobs-empty span {
  font-size: 9.5px;
  line-height: 1.5;
  max-width: 48ch;
}

.job {
  display: grid;
  grid-template-rows: auto auto auto auto auto;
  gap: 4px;
  width: 226px;
  flex: none;
  padding: 7px 9px;
  border-radius: var(--radius);
  border: 1px solid var(--line);
  border-left: 2px solid var(--line-3);
  background: var(--bg-2);
  cursor: pointer;
  min-height: 0;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.job:hover {
  background: var(--bg-3);
}

.job.is-selected {
  border-color: var(--accent-line);
  background: linear-gradient(180deg, rgba(77, 141, 255, 0.08), var(--bg-2));
}

.job--info {
  border-left-color: var(--accent);
}

.job--warn {
  border-left-color: var(--warn);
}

.job--ok {
  border-left-color: var(--ok);
}

.job--danger {
  border-left-color: var(--danger);
}

.job--muted {
  border-left-color: #455568;
}

.job-head {
  display: flex;
  align-items: center;
  gap: 5px;
}

.job-id {
  font-size: 9.5px;
  color: var(--text-2);
}

.job-preview {
  margin-left: auto;
  width: 20px;
  height: 20px;
}

.job-file {
  font-size: 10.5px;
  color: var(--text-1);
  font-weight: 500;
}

.job-sub {
  font-size: 9.5px;
  color: var(--text-3);
}

.job-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 9px;
  color: var(--text-3);
  min-width: 0;
}

.job-counts {
  display: flex;
  gap: 8px;
  flex: none;
}

.job-counts b {
  color: var(--text-1);
  font-weight: 600;
}

.job-note {
  color: var(--warn);
  flex: 1;
  min-width: 0;
}

.job-progress {
  margin-left: auto;
  color: var(--text-3);
}

/* 预览 */
.preview {
  width: 250px;
  flex: none;
  display: grid;
  grid-template-rows: auto 1fr auto auto;
  gap: 5px;
  padding: 7px;
  border-radius: var(--radius);
  border: 1px solid var(--line-2);
  background: var(--bg-2);
  min-height: 0;
}

.preview-head {
  display: flex;
  align-items: center;
  gap: 5px;
}

.preview-head .icon-btn {
  margin-left: auto;
  width: 20px;
  height: 20px;
}

.preview-frame {
  min-height: 0;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--line);
  background: #060b10;
}

.preview-placeholder {
  display: grid;
  place-items: center;
  height: 100%;
  font-size: 10px;
  color: var(--text-3);
}

.preview-meta {
  font-size: 9px;
  color: var(--text-2);
  min-width: 0;
}

.preview-note {
  font-size: 8.5px;
  line-height: 1.45;
  color: var(--text-3);
}

@media (max-width: 1360px) {
  .preview {
    display: none;
  }
}
</style>
