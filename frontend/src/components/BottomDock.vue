<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import JobCard from "./dock/JobCard.vue";
import RecordingClipNameModal from "./modals/RecordingClipNameModal.vue";
import { roundInfo } from "../data/mock";
import {
  actions,
  activityView,
  allAutoScan,
  analyzedView,
  currentCaseName,
  indexView,
  jobCards,
  jobStats,
  loadRecordings,
  observationList,
  recordingBusy,
  recordingClips,
  recordingError,
  recordingLoading,
  recordingSources,
  recordingSummary,
  roundView,
  toast,
  ui,
  visibleElements,
  wb,
} from "../store/workbench";

type DockTab = "control" | "resources" | "candidates" | "history";

const tabs: { id: DockTab; label: string; icon: "list" | "folder" | "target" | "clock" }[] = [
  { id: "control", label: "任务控制", icon: "list" },
  { id: "resources", label: "录像资源", icon: "folder" },
  { id: "candidates", label: "候选结果", icon: "target" },
  { id: "history", label: "历史记录", icon: "clock" },
];

const tab = ref<DockTab>("control");

const collapsedSummary = computed(() => {
  if (wb.emptyMode) return "空任务 · 无目标要素 · 研判未启动";
  const stats = jobStats.value;
  const review = stats.review ? ` · 待审核 ${stats.review}` : "";
  return `要素 ${visibleElements.value.length} · 分析中 ${stats.running}${review} · 轮次 ${roundView.value.id}`;
});

const candidateRows = computed(() => observationList.value.filter((item) => item.kind !== "gap"));

/* ------------------------------ 录像资源 ------------------------------ */

const SCAN_STATUS: Record<string, string> = {
  idle: "未扫描",
  queued: "排队中",
  running: "扫描中",
  succeeded: "已完成",
  failed: "扫描失败",
};

const statusText = (status: string) => SCAN_STATUS[status] ?? status;

const matchLabel = (status: string) => {
  if (status === "matched") return "已匹配";
  if (status === "ambiguous") return "同名多台";
  if (status === "no_name" || status === "ocr_disabled") return "无名称";
  return "未匹配";
};

const editingClip = ref<import("../services/mainApi").RecordingClip | null>(null);

/** 点「审核」：打开核对弹窗，左边看截图，右边改识别名称 */
const openReview = (clip: import("../services/mainApi").RecordingClip) => {
  editingClip.value = clip;
};

const closeReview = () => {
  editingClip.value = null;
};

const saveReview = async (name: string, note: string) => {
  const clip = editingClip.value;
  if (!clip) return;
  const saved = await actions.setRecordingClipName(clip.id, name, note || undefined);
  if (saved) closeReview();
};

const fileRowTitle = (clip: { relpath: string; reviewer_note: string | null }) =>
  clip.reviewer_note ? clip.relpath + "\n依据：" + clip.reviewer_note : clip.relpath;


const unmatchedCount = computed(() => {
  const files = recordingSummary.value?.file_count ?? 0;
  const matched = recordingSummary.value?.matched_count ?? 0;
  return Math.max(0, files - matched);
});

/** 录像段按开始时间倒序；表格只渲染前 200 行，避免几千条一起上屏 */
const MAX_CLIP_ROWS = 200;
const sortedClips = computed(() =>
  [...recordingClips.value].sort((a, b) => (b.start_local ?? "").localeCompare(a.start_local ?? "")),
);
const visibleClips = computed(() => sortedClips.value.slice(0, MAX_CLIP_ROWS));

const clipTime = (clip: { start_local: string | null; start_source: string }) => {
  if (!clip.start_local) return "时间未知";
  const text = clip.start_local.replace("T", " ");
  return clip.start_source === "filename" ? `${text}（文件名）` : text;
};

const durationText = (seconds: number | null) => {
  if (!seconds || seconds <= 0) return "—";
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
};

const clipsEmptyText = computed(() => {
  if (!wb.caseId) return "先选择案件，再添加视频或挂载目录";
  if (recordingLoading.value) return "正在读取扫描结果…";
  if (recordingError.value) return "暂时读不到视频文件：确认 api 服务已包含录像资源接口后点「刷新」";
  return "还没有视频文件：左栏「添加视频」或「挂载目录」后会按目录扫描，只读开头角标，不改动录像";
});

const openRecording = () => actions.openRecordingPicker("video");

const toggleAuto = (sourceId: string, checked: boolean) =>
  void actions.toggleRecordingAutoScan(sourceId, checked);

const rescanSource = (sourceId: string) => void actions.scanRecording(sourceId);

const rematchSource = (sourceId: string) => void actions.rematchRecordingSource(sourceId);

const removeSource = (sourceId: string, label: string) => {
  if (!window.confirm(`移除录像目录「${label}」？只会删掉主系统的扫描记录，磁盘上的录像文件不会动。`)) return;
  void actions.removeRecordingDir(sourceId);
};

/** 录像资源面板打开时轮询扫描进度；切走标签页就停，避免后台空转 */
let recordingTimer: number | undefined;
const stopRecordingPoll = () => {
  if (recordingTimer !== undefined) {
    window.clearInterval(recordingTimer);
    recordingTimer = undefined;
  }
};
const startRecordingPoll = () => {
  stopRecordingPoll();
  void loadRecordings();
  recordingTimer = window.setInterval(() => void loadRecordings(), 4000);
};

const refreshRecordings = () => {
  if (!wb.caseId) {
    toast("请先在「案件」里选择或新建案件", "warn");
    return;
  }
  startRecordingPoll();
};

watch(tab, (next) => {
  if (next === "resources") startRecordingPoll();
  else stopRecordingPoll();
});
onMounted(() => {
  if (tab.value === "resources") startRecordingPoll();
});

let cleanup: (() => void) | null = null;

function startResize(event: PointerEvent) {
  if (event.button !== 0) return;
  event.preventDefault();
  const startY = event.clientY;
  const startHeight = ui.dockHeight;
  document.body.style.cursor = "ns-resize";
  document.body.style.userSelect = "none";

  const onMove = (moveEvent: PointerEvent) => {
    const next = startHeight - (moveEvent.clientY - startY);
    const max = Math.round(window.innerHeight * 0.62);
    ui.dockHeight = Math.round(Math.min(Math.max(next, 208), max));
  };
  const onUp = () => {
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    cleanup = null;
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  cleanup = onUp;
}

onBeforeUnmount(() => {
  cleanup?.();
  stopRecordingPoll();
});
</script>

<template>
  <section class="dock" :class="{ 'is-collapsed': ui.dockCollapsed }" :style="{ height: ui.dockCollapsed ? '32px' : `${ui.dockHeight}px` }">
    <div
      class="dock-grip"
      role="separator"
      aria-orientation="horizontal"
      :aria-label="`任务操作台高度 ${ui.dockHeight} 像素，可拖动调整`"
      @pointerdown="startResize"
    >
      <span class="grip-bar" />
      <span v-if="ui.dockCollapsed" class="grip-label">任务控制</span>
      <span v-if="ui.dockCollapsed" class="grip-summary">{{ collapsedSummary }}</span>
      <button
        class="icon-btn grip-toggle"
        :aria-label="ui.dockCollapsed ? '展开任务操作台' : '收起任务操作台'"
        :title="ui.dockCollapsed ? '展开' : '收起'"
        @pointerdown.stop
        @click="ui.dockCollapsed = !ui.dockCollapsed"
      >
        <AppIcon :name="ui.dockCollapsed ? 'chevron-up' : 'chevron-down'" :size="14" />
      </button>
    </div>

    <div v-show="!ui.dockCollapsed" class="dock-body">
      <nav class="dock-tabs" aria-label="任务控制分区">
        <button
          v-for="item in tabs"
          :key="item.id"
          class="dock-tab"
          :class="{ 'is-on': tab === item.id }"
          :aria-pressed="tab === item.id"
          @click="tab = item.id"
        >
          <AppIcon :name="item.icon" :size="15" />
          <span>{{ item.label }}</span>
          <span v-if="item.id === 'control' && jobStats.review" class="dock-tab-badge num">{{ jobStats.review }}</span>
        </button>
      </nav>

      <div class="dock-panel">
        <!-- 任务控制：左控制 / 右分析卡片 -->
        <div v-if="tab === 'control'" class="split split--control">
          <aside class="col col--left">
            <div class="round-line">
              <span class="round-chip num">{{ roundInfo.id }}</span>
              <span class="round-desc truncate">{{ roundInfo.anchor }} · {{ roundInfo.directionLabel }}</span>
              <span class="round-window num">{{ wb.from }}–{{ wb.to }}</span>
            </div>

            <!-- 启动后在做什么：只写文字，不给百分比（研判本身不确定） -->
            <div class="activity" :data-tone="activityView.tone">
              <span class="act-dot" />
              <div class="act-main">
                <span class="act-label">{{ activityView.label }}</span>
                <span class="act-detail">{{ activityView.detail }}</span>
              </div>
            </div>

            <div class="ctl-actions">
              <button class="btn btn--primary" :disabled="wb.emptyMode" @click="actions.resumeRound()">
                <AppIcon name="play" :size="13" />继续分析
              </button>
              <button class="btn btn--outline" :disabled="wb.emptyMode" @click="actions.pauseRound()">
                <AppIcon name="pause" :size="13" />暂停
              </button>
              <button class="btn btn--danger" :disabled="wb.emptyMode" @click="actions.stopRound()">
                <AppIcon name="stop" :size="13" />终止
              </button>
            </div>

            <div class="ctl-facts num">
              <span>要素 <b>{{ visibleElements.length }}</b></span>
              <span>作业 <b>{{ jobStats.total }}</b></span>
              <span>分析中 <b>{{ jobStats.running }}</b></span>
              <span :class="{ warn: jobStats.review }">待审核 <b>{{ jobStats.review }}</b></span>
            </div>

            <div class="idx-line">
              <AppIcon name="folder" :size="12" />
              <span class="truncate">
                录像索引 <b class="num">{{ indexView.files.toLocaleString() }}</b> 文件 · 有时间
                <b class="num">{{ indexView.parsed.toLocaleString() }}</b> · 匹配 <b class="num">{{ indexView.mapped }}</b>
              </span>
              <button class="btn btn--sm btn--ghost" @click="openRecording"><AppIcon name="plus" :size="12" />添加</button>
            </div>
          </aside>

          <div class="col col--right">
            <header class="col-head">
              <span class="col-title">正在分析</span>
              <span class="col-sub">
                共 <b class="num">{{ jobCards.length }}</b> 段
                <template v-if="jobStats.review"> · <b class="num warn">{{ jobStats.review }}</b> 段待人审</template>
              </span>
              <div class="thumb-switch" role="group" aria-label="卡片缩略显示方式">
                <button
                  class="thumb-btn"
                  :class="{ 'is-on': ui.dockThumbMode === 'image' }"
                  :aria-pressed="ui.dockThumbMode === 'image'"
                  @click="actions.setDockThumbMode('image')"
                >
                  <AppIcon name="camera" :size="11" />缩略图
                </button>
                <button
                  class="thumb-btn"
                  :class="{ 'is-on': ui.dockThumbMode === 'video' }"
                  :aria-pressed="ui.dockThumbMode === 'video'"
                  @click="actions.setDockThumbMode('video')"
                >
                  <AppIcon name="video" :size="11" />缩略视频
                </button>
              </div>
            </header>

            <div v-if="jobCards.length" class="job-grid scroll-y">
              <JobCard
                v-for="card in jobCards"
                :key="card.job.id"
                :card="card"
                :mode="ui.dockThumbMode"
                :active="wb.selectedJobId === card.job.id"
                @open="actions.openReviewClip(card.job.id)"
              />
            </div>
            <div v-else class="panel-empty panel-empty--inline">
              <AppIcon name="camera" :size="17" />
              <strong>还没有正在分析的视频</strong>
              <span>
                {{ wb.emptyMode ? "先添加目标要素并启动研判；分析开始后，每段录像会以卡片出现在这里。" : "去「录像资源」添加视频或挂载目录，再点「开启分析」。" }}
              </span>
            </div>
          </div>
        </div>

        <!-- 录像资源：左操作 / 右文件信息 -->
        <div v-else-if="tab === 'resources'" class="split split--resources">
          <aside class="col col--left">
            <div class="res-actions">
              <button class="btn btn--primary" :disabled="!wb.caseId || recordingBusy" @click="actions.openRecordingPicker('video')">
                <AppIcon name="video" :size="13" />添加视频
              </button>
              <button class="btn btn--outline" :disabled="!wb.caseId || recordingBusy" @click="actions.openRecordingPicker('dir')">
                <AppIcon name="folder" :size="13" />挂载目录
              </button>
              <button class="btn btn--outline" :disabled="!recordingSources.length || recordingBusy" @click="actions.startAllScans()">
                <AppIcon name="play" :size="13" />开启分析
              </button>
              <button
                class="btn"
                :class="allAutoScan ? 'btn--ok' : 'btn--outline'"
                :aria-pressed="allAutoScan"
                :disabled="!recordingSources.length || recordingBusy"
                @click="actions.setAllAutoScan(!allAutoScan)"
              >
                <AppIcon name="scan" :size="13" />自动分析{{ allAutoScan ? " · 开" : "" }}
              </button>
            </div>

            <div class="res-meta">
              <span class="truncate">案件 <b>{{ currentCaseName || "未选择" }}</b></span>
              <span class="num">目录 {{ recordingSources.length }} · 文件 {{ (recordingSummary?.file_count ?? 0).toLocaleString() }} · 未匹配 {{ unmatchedCount }}</span>
            </div>

            <div class="res-list scroll-y">
              <div v-for="source in recordingSources" :key="source.id" class="res-source">
                <div class="res-source-top">
                  <span class="res-name mono truncate" :title="source.path">{{ source.label }}</span>
                  <span class="res-status" :data-status="source.last_status">{{ statusText(source.last_status) }}</span>
                </div>
                <div class="res-source-bottom">
                  <span class="num">{{ source.file_count }} 文件 · {{ source.matched_count }} 匹配</span>
                  <label class="res-auto" title="新录像自动解析">
                    <input
                      type="checkbox"
                      :checked="source.auto_scan"
                      :disabled="recordingBusy"
                      @change="toggleAuto(source.id, ($event.target as HTMLInputElement).checked)"
                    />
                    自动
                  </label>
                  <span class="res-row-actions">
                    <button class="icon-btn" title="重新匹配摄像头（保留人工审核）" :disabled="recordingBusy || source.scanning" @click="rematchSource(source.id)">
                      <AppIcon name="target" :size="11" />
                    </button>
                    <button class="icon-btn" title="扫描这个目录" :disabled="source.scanning" @click="rescanSource(source.id)">
                      <AppIcon name="refresh" :size="11" />
                    </button>
                    <button class="icon-btn" title="移除登记（不动录像文件）" @click="removeSource(source.id, source.label)">
                      <AppIcon name="trash" :size="11" />
                    </button>
                  </span>
                </div>
              </div>
              <p v-if="!recordingSources.length" class="empty-hint">
                {{ wb.caseId ? "还没有录像目录：点「添加视频」或「挂载目录」，选择服务器上的录像目录。" : "先打开「案件」选择或新建案件，再添加视频。" }}
              </p>
            </div>
          </aside>

          <div class="col col--right">
            <header class="col-head">
              <span class="col-title">视频文件信息</span>
              <span class="col-sub num">
                {{ recordingClips.length }} 个 · 读到开始时间 {{ recordingSummary?.time_count ?? 0 }} · 扫描中 {{ recordingSummary?.scanning ? "是" : "否" }}
              </span>
              <button class="btn btn--sm btn--ghost" :disabled="!wb.caseId" @click="refreshRecordings">
                <AppIcon name="refresh" :size="11" />刷新
              </button>
            </header>

            <div v-if="recordingError" class="res-error">录像资源读取失败：{{ recordingError }}</div>

            <div class="file-table scroll-y">
              <div class="file-head">
                <span>开始时间</span>
                <span>摄像头</span>
                <span>文件名</span>
                <span>时长</span>
                <span>匹配</span>
                <span>操作</span>
              </div>
              <div v-for="clip in visibleClips" :key="clip.id" class="file-row">
                <span class="num file-time">{{ clipTime(clip) }}</span>
                <span class="truncate">
                  {{ clip.camera_name || "未识别" }}
                  <template v-if="clip.name_source === 'filename'">
                    <span class="hint-tag" title="名称来自文件名（未由角标确认）">文件名</span>
                  </template>
                  <template v-if="clip.name_source === 'manual'">
                    <span class="hint-tag hint-tag--ok" title="人工填写，自动扫描与重新匹配都不覆盖">人工</span>
                  </template>
                </span>
                <span class="mono truncate file-name" :title="fileRowTitle(clip)">{{ clip.file_name }}</span>
                <span class="num">{{ durationText(clip.duration_seconds) }}</span>
                <span class="file-tag" :data-status="clip.match_status">{{ matchLabel(clip.match_status) }}</span>
                <span class="file-ops">
                  <a class="link" @click.prevent="openReview(clip)">审核</a>
                </span>
              </div>
              <p v-if="!visibleClips.length" class="empty-hint">{{ clipsEmptyText }}</p>
              <p v-else-if="sortedClips.length > visibleClips.length" class="file-more">
                只显示最近 {{ MAX_CLIP_ROWS }} 个，共 {{ sortedClips.length }} 个文件
              </p>
            </div>
          </div>
        </div>

        <!-- 候选结果：已分析视频的统计 -->
        <div v-else-if="tab === 'candidates'" class="cand-panel">
          <div class="stat-row">
            <div class="stat"><span>已分析视频</span><b class="num">{{ analyzedView.analyzed }}</b></div>
            <div class="stat"><span>有检出</span><b class="num ok">{{ analyzedView.detected }}</b></div>
            <div class="stat"><span>无检出</span><b class="num">{{ analyzedView.nodetect }}</b></div>
            <div class="stat"><span>待审核</span><b class="num warn">{{ analyzedView.review }}</b></div>
            <div class="stat"><span>失败</span><b class="num danger">{{ analyzedView.failed }}</b></div>
            <div class="stat"><span>候选总数</span><b class="num">{{ analyzedView.candidates }}</b></div>
          </div>

          <div class="cand-body">
            <div class="cand-cams">
              <div class="mini-head">
                <span>按摄像头统计</span>
                <span class="dim num">{{ analyzedView.cameras.length }} 台</span>
              </div>
              <div class="mini-scroll scroll-y">
                <div v-for="row in analyzedView.cameras" :key="row.cameraId" class="cam-row">
                  <span class="truncate">{{ row.cameraName }}</span>
                  <span class="num">已分析 {{ row.analyzed }}</span>
                  <span class="num ok">检出 {{ row.detected }}</span>
                  <span class="num" :class="{ warn: row.review }">待审 {{ row.review }}</span>
                </div>
                <p v-if="!analyzedView.cameras.length" class="empty-hint">还没有分析完成的视频。</p>
              </div>
            </div>

            <div class="cand-list">
              <div class="mini-head">
                <span>候选条目</span>
                <span class="dim num">{{ candidateRows.length }} 条</span>
              </div>
              <div class="mini-scroll scroll-y">
                <button v-for="item in candidateRows" :key="item.id" class="cand-row" @click="actions.selectObservation(item.id)">
                  <span class="num cand-time">{{ item.timeLabel }}</span>
                  <span class="truncate">{{ item.cameraId ?? "—" }} {{ item.title }}</span>
                  <span class="cand-status" :data-tone="item.reliability">
                    {{ item.reliability === "verified" ? "已检验" : item.reliability === "invalid" ? "已排除" : item.reliability === "review" ? "待核查" : "检测候选" }}
                  </span>
                </button>
                <p v-if="!candidateRows.length" class="empty-hint">还没有候选条目；分析完成且人工确认后才会进入这里。</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 历史记录 -->
        <div v-else class="panel-empty">
          <AppIcon name="clock" :size="18" />
          <strong>暂无历史轮次</strong>
          <span>每轮研判结束后，轮次、窗口与审核结果会记录在这里。</span>
        </div>
      </div>
    </div>
  </section>

<RecordingClipNameModal
  :open="Boolean(editingClip)"
  :clip="editingClip"
  @close="closeReview"
  @save="saveReview"
/>
</template>

<style scoped>
.dock {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #080c12;
  border-top: 1px solid var(--line);
  flex: none;
}

/* ------------------------------ 拖拽条 ------------------------------ */

.dock-grip {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  height: 8px;
  cursor: ns-resize;
  flex: none;
}

.grip-bar {
  width: 42px;
  height: 3px;
  border-radius: 2px;
  background: var(--line-3);
}

.grip-label {
  font-size: 12px;
  color: var(--text-3);
}

.grip-summary {
  font-size: 11.5px;
  color: var(--text-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dock.is-collapsed .dock-grip {
  height: 30px;
  justify-content: flex-start;
  padding: 0 12px;
}

.grip-toggle {
  position: absolute;
  right: 8px;
  top: 1px;
  width: 24px;
  height: 18px;
}

/* ------------------------------ 骨架 ------------------------------ */

.dock-body {
  display: grid;
  grid-template-columns: 130px minmax(0, 1fr);
  flex: 1;
  min-height: 0;
  border-top: 1px solid var(--line);
}

.dock-tabs {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 6px 6px 8px;
  border-right: 1px solid var(--line);
  background: #070b11;
  min-height: 0;
  overflow-y: auto;
}

.dock-tab {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 9px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-2);
  font-size: 12.5px;
  white-space: nowrap;
  text-align: left;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease), border-color var(--dur) var(--ease);
}

.dock-tab:hover {
  background: var(--bg-2);
  color: var(--text-1);
}

.dock-tab.is-on {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--text-0);
}

.dock-tab-badge {
  margin-left: auto;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--warn-soft);
  color: var(--warn);
  font-size: 10.5px;
  line-height: 16px;
}

.dock-panel {
  min-width: 0;
  min-height: 0;
  padding: 7px 10px 8px;
  overflow: hidden;
}

/* ------------------------------ 两栏骨架 ------------------------------ */

.split {
  display: grid;
  gap: 10px;
  height: 100%;
  min-height: 0;
}

.split--control,
.split--resources {
  grid-template-columns: 322px minmax(0, 1fr);
}

.col {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  min-height: 0;
}

.col--left {
  padding-right: 10px;
  border-right: 1px solid var(--line);
  overflow-y: auto;
}

.col-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 24px;
  flex: none;
}

.col-title {
  font-size: var(--fs-title);
  font-weight: 600;
  color: var(--text-0);
}

.col-sub {
  font-size: var(--fs-body);
  color: var(--text-3);
}

.col-sub .warn {
  color: var(--warn);
}

/* ------------------------------ 任务控制 · 左栏 ------------------------------ */

.round-line {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  font-size: var(--fs-body);
  color: var(--text-2);
}

.round-chip {
  flex: none;
  padding: 0 6px;
  border: 1px solid var(--accent-line);
  border-radius: 4px;
  background: var(--accent-soft);
  color: var(--accent-hi);
  font-size: 12px;
  line-height: 18px;
}

.round-desc {
  color: var(--text-2);
}

.round-window {
  flex: none;
  margin-left: auto;
  color: var(--text-3);
}

/* 启动后在做什么：一段文字，不是进度条 */
.activity {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--line-2);
  border-left: 2px solid var(--line-3);
  border-radius: var(--radius-sm);
  background: var(--bg-1);
}

.act-dot {
  flex: none;
  width: 7px;
  height: 7px;
  margin-top: 6px;
  border-radius: 50%;
  background: var(--text-4);
}

.act-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.act-label {
  font-size: var(--fs-strong);
  font-weight: 600;
  color: var(--text-0);
}

.act-detail {
  font-size: var(--fs-body);
  line-height: 1.55;
  color: var(--text-2);
  text-wrap: pretty;
}

.activity[data-tone="run"] {
  border-left-color: var(--accent);
}

.activity[data-tone="run"] .act-dot {
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.activity[data-tone="review"] {
  border-left-color: var(--warn);
  background: var(--warn-soft);
}

.activity[data-tone="review"] .act-dot {
  background: var(--warn);
}

.activity[data-tone="paused"] {
  border-left-color: var(--warn);
}

.activity[data-tone="paused"] .act-dot {
  background: var(--warn);
}

.activity[data-tone="fail"] {
  border-left-color: var(--danger);
}

.activity[data-tone="fail"] .act-dot {
  background: var(--danger);
}

.activity[data-tone="done"] {
  border-left-color: var(--ok);
}

.activity[data-tone="done"] .act-dot {
  background: var(--ok);
}

.ctl-actions {
  display: flex;
  gap: 7px;
}

.ctl-facts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 3px 12px;
  font-size: var(--fs-body);
  color: var(--text-3);
}

.ctl-facts b {
  color: var(--text-0);
  font-weight: 600;
}

.ctl-facts .warn,
.ctl-facts .warn b {
  color: var(--warn);
}

.idx-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-top: auto;
  padding-top: 6px;
  border-top: 1px solid var(--line);
  font-size: var(--fs-note);
  color: var(--text-3);
}

/* 索引数可能很长：只让文字收缩，别把「添加」挤出去 */
.idx-line > .truncate {
  flex: 1;
  min-width: 0;
}

.idx-line > .btn {
  flex: none;
}

.idx-line b {
  color: var(--text-1);
}

/* ------------------------------ 任务控制 · 卡片 ------------------------------ */

.thumb-switch {
  display: flex;
  gap: 1px;
  margin-left: auto;
  padding: 1px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-xs);
  background: var(--bg-1);
}

.thumb-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-3);
  font-size: var(--fs-note);
  cursor: pointer;
}

.thumb-btn:hover {
  color: var(--text-1);
}

.thumb-btn.is-on {
  background: var(--bg-4);
  color: var(--text-0);
}

.job-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(228px, 1fr));
  grid-auto-rows: min-content;
  align-content: start;
  gap: 7px;
  min-height: 0;
  padding-right: 2px;
}

/* ------------------------------ 录像资源 ------------------------------ */

.res-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 5px;
  flex: none;
}

.res-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--fs-body);
  color: var(--text-3);
}

.res-meta b {
  color: var(--text-1);
  font-weight: 500;
}

.res-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-height: 0;
  overflow-y: auto;
}

.res-source {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 7px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-xs);
  background: var(--bg-1);
}

.res-source-top {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.res-name {
  flex: 1;
  min-width: 0;
  font-size: var(--fs-body);
  color: var(--text-1);
}

.res-status {
  flex: none;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--bg-4);
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 17px;
}

.res-status[data-status="running"],
.res-status[data-status="queued"] {
  background: var(--accent-soft);
  color: var(--accent-hi);
}

.res-status[data-status="failed"] {
  background: var(--danger-soft);
  color: var(--danger);
}

.res-status[data-status="succeeded"] {
  background: var(--ok-soft);
  color: var(--ok);
}

.res-source-bottom {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-note);
  color: var(--text-4);
}

.res-auto {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

.res-auto input {
  width: 12px;
  height: 12px;
  accent-color: var(--accent);
}

.res-row-actions {
  display: inline-flex;
  gap: 2px;
  margin-left: auto;
}

.res-row-actions .icon-btn {
  width: 20px;
  height: 20px;
}

.res-error {
  padding: 5px 7px;
  border: 1px solid rgba(242, 112, 106, 0.36);
  border-radius: var(--radius-xs);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: var(--fs-note);
  flex: none;
}

/* ------------------------------ 文件表 ------------------------------ */

.file-table {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-xs);
  background: var(--bg-1);
}

.file-head,
.file-row {
  display: grid;
  grid-template-columns: 136px minmax(96px, 0.7fr) minmax(0, 1.6fr) 52px 62px 84px;
  gap: 9px;
  align-items: center;
  padding: 5px 10px;
  font-size: var(--fs-body);
}

.file-head {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--bg-3);
  color: var(--text-3);
  border-bottom: 1px solid var(--line);
  line-height: 22px;
}

.file-row {
  border-bottom: 1px solid rgba(26, 36, 50, 0.6);
  color: var(--text-2);
  line-height: 21px;
}

.file-row:last-child {
  border-bottom: 0;
}

.file-row:hover {
  background: var(--bg-2);
}

.file-time {
  color: var(--text-1);
}

.file-name {
  color: var(--text-1);
}

.file-tag {
  justify-self: start;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--bg-4);
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 17px;
}

.file-tag[data-status="matched"] {
  background: var(--ok-soft);
  color: var(--ok);
}

.file-tag[data-status="ambiguous"] {
  background: var(--warn-soft);
  color: var(--warn);
}

.hint-tag {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 4px;
  background: var(--bg-4);
  color: var(--text-4);
  font-size: 9.5px;
  line-height: 15px;
  vertical-align: 1px;
}
.hint-tag--ok {
  background: var(--ok-soft);
  color: var(--ok);
}
.hint-tag--warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.file-ops {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.file-ops .link {
  cursor: pointer;
  color: var(--accent);
  font-size: var(--fs-note);
}
.file-ops .link:hover {
  text-decoration: underline;
}
.file-more {
  padding: 7px 10px;
  color: var(--text-4);
  font-size: var(--fs-note);
}

/* ------------------------------ 候选结果 ------------------------------ */

.cand-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 0;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 6px;
  flex: none;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-xs);
  background: var(--bg-1);
}

.stat span {
  font-size: var(--fs-note);
  color: var(--text-4);
}

.stat b {
  font-size: var(--fs-xl);
  color: var(--text-0);
  line-height: 1.15;
}

.stat b.ok {
  color: var(--ok);
}

.stat b.warn {
  color: var(--warn);
}

.stat b.danger {
  color: var(--danger);
}

.cand-body {
  display: grid;
  grid-template-columns: minmax(240px, 0.85fr) minmax(0, 1.15fr);
  gap: 10px;
  min-height: 0;
  flex: 1;
}

.cand-cams,
.cand-list {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius-xs);
  background: var(--bg-1);
  overflow: hidden;
}

.mini-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 9px;
  border-bottom: 1px solid var(--line);
  background: var(--bg-3);
  font-size: var(--fs-micro);
  color: var(--text-2);
  flex: none;
}

.mini-scroll {
  min-height: 0;
  overflow-y: auto;
  padding: 3px 0;
}

.cam-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 62px 62px 62px;
  gap: 6px;
  align-items: center;
  padding: 5px 10px;
  font-size: var(--fs-body);
  color: var(--text-2);
}

.cam-row:hover {
  background: var(--bg-2);
}

.cam-row .ok {
  color: var(--ok);
}

.cam-row .warn {
  color: var(--warn);
}

.cand-row {
  display: grid;
  grid-template-columns: 46px minmax(0, 1fr) 60px;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 5px 10px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font: inherit;
  font-size: var(--fs-body);
  text-align: left;
  cursor: pointer;
}

.cand-row:hover {
  background: var(--bg-2);
  color: var(--text-1);
}

.cand-time {
  color: var(--text-1);
}

.cand-status {
  justify-self: start;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--bg-4);
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 17px;
}

.cand-status[data-tone="review"] {
  background: var(--warn-soft);
  color: var(--warn);
}

.cand-status[data-tone="verified"] {
  background: var(--ok-soft);
  color: var(--ok);
}

.cand-status[data-tone="invalid"] {
  background: var(--bg-4);
  color: var(--text-4);
}

/* ------------------------------ 空态 ------------------------------ */

.panel-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: 100%;
  padding: 16px;
  color: var(--text-3);
  text-align: center;
}

.panel-empty strong {
  font-size: var(--fs-title);
  color: var(--text-1);
}

.panel-empty span {
  max-width: 440px;
  font-size: var(--fs-body);
  line-height: 1.6;
  color: var(--text-4);
}

.panel-empty--inline {
  align-items: flex-start;
  justify-content: center;
  text-align: left;
  gap: 4px;
  padding: 12px 2px;
}

.empty-hint {
  padding: 10px;
  font-size: var(--fs-body);
  line-height: 1.6;
  color: var(--text-4);
}
</style>