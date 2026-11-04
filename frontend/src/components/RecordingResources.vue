<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import RecordingClipNameModal from "./modals/RecordingClipNameModal.vue";
import type { RecordingClip } from "../services/mainApi";
import {
  actions,
  allAutoScan,
  currentCaseName,
  loadRecordings,
  recordingBusy,
  recordingClips,
  recordingError,
  recordingLoading,
  recordingSources,
  recordingSummary,
  toast,
  wb,
} from "../store/workbench";

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
  return "还没有视频文件：点「添加视频」或「挂载目录」后会按目录扫描，只读开头角标，不改动录像";
});

const fileRowTitle = (clip: { relpath: string; reviewer_note: string | null }) =>
  clip.reviewer_note ? clip.relpath + "\n依据：" + clip.reviewer_note : clip.relpath;

const toggleAuto = (sourceId: string, checked: boolean) =>
  void actions.toggleRecordingAutoScan(sourceId, checked);

const rescanSource = (sourceId: string) => void actions.scanRecording(sourceId);

const rematchSource = (sourceId: string) => void actions.rematchRecordingSource(sourceId);

const removeSource = (sourceId: string, label: string) => {
  if (!window.confirm(`移除录像目录「${label}」？只会删掉主系统的扫描记录，磁盘上的录像文件不会动。`)) return;
  void actions.removeRecordingDir(sourceId);
};

const editingClip = ref<RecordingClip | null>(null);

const openReview = (clip: RecordingClip) => {
  editingClip.value = clip;
};

const saveReview = async (name: string, note: string) => {
  const clip = editingClip.value;
  if (!clip) return;
  const saved = await actions.setRecordingClipName(clip.id, name, note || undefined);
  if (saved) editingClip.value = null;
};

let recordingTimer: number | undefined;
const stopPoll = () => {
  if (recordingTimer !== undefined) {
    window.clearInterval(recordingTimer);
    recordingTimer = undefined;
  }
};
const startPoll = () => {
  stopPoll();
  void loadRecordings();
  recordingTimer = window.setInterval(() => void loadRecordings(), 4000);
};

const refresh = () => {
  if (!wb.caseId) {
    toast("请先在「案件」里选择或新建案件", "warn");
    return;
  }
  startPoll();
};

onMounted(startPoll);
onBeforeUnmount(stopPoll);
</script>

<template>
  <section class="res-page">
    <header class="page-head">
      <div class="page-title">
        <h1>录像资源</h1>
        <p>按目录登记本地录像，只读取开头角标识别摄像头与时间，不改动、不搬移任何视频文件。</p>
      </div>
      <div class="head-actions">
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
        <button class="btn btn--outline" :disabled="!wb.caseId" @click="refresh">
          <AppIcon name="refresh" :size="13" />刷新
        </button>
      </div>
    </header>

    <div class="res-layout">
      <aside class="res-side">
        <div class="side-head">
          <span>录像目录</span>
          <span class="num">{{ recordingSources.length }} 个</span>
        </div>
        <div class="side-meta">
          <span class="truncate">案件 <b>{{ currentCaseName || "未选择" }}</b></span>
          <span class="num">
            文件 <b>{{ (recordingSummary?.file_count ?? 0).toLocaleString() }}</b>
            · 未匹配 <b :class="{ warn: unmatchedCount }">{{ unmatchedCount }}</b>
          </span>
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

      <div class="res-main">
        <header class="col-head">
          <span class="col-title">视频文件信息</span>
          <span class="col-sub num">
            {{ recordingClips.length }} 个 · 读到开始时间 {{ recordingSummary?.time_count ?? 0 }} · 扫描中 {{ recordingSummary?.scanning ? "是" : "否" }}
          </span>
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
                <span class="hint-tag hint-tag--ok" title="人工名称不会被覆盖；重新匹配只更新镜头绑定">人工</span>
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

    <RecordingClipNameModal
      :open="Boolean(editingClip)"
      :clip="editingClip"
      @close="editingClip = null"
      @save="saveReview"
    />
  </section>
</template>

<style scoped>
.res-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
  min-height: 0;
  padding: 18px 20px 20px;
  overflow: hidden;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  flex: none;
}

.page-title h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  color: var(--text-0);
  letter-spacing: 0.01em;
}

.page-title p {
  margin: 6px 0 0;
  max-width: 760px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-3);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.res-layout {
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 14px;
  flex: 1;
  min-height: 0;
}

.res-side,
.res-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  min-height: 0;
}

.res-side {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg-1);
}

.side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-1);
  flex: none;
}

.side-head .num {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-4);
}

.side-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-bottom: 9px;
  border-bottom: 1px solid var(--line);
  font-size: 11.5px;
  color: var(--text-3);
  flex: none;
}

.side-meta b {
  color: var(--text-1);
  font-weight: 500;
}

.side-meta .warn {
  color: var(--warn);
}

.res-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-height: 0;
  overflow-y: auto;
  padding-top: 2px;
}

.res-source {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-xs);
  background: var(--bg-2);
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
  font-size: 12px;
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
  font-size: 11px;
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
  width: 21px;
  height: 21px;
}

.col-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex: none;
}

.col-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-0);
}

.col-sub {
  font-size: 11.5px;
  color: var(--text-3);
}

.res-error {
  padding: 7px 9px;
  border: 1px solid rgba(242, 112, 106, 0.36);
  border-radius: var(--radius-xs);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 11.5px;
  flex: none;
}

.file-table {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg-1);
}

.file-head,
.file-row {
  display: grid;
  grid-template-columns: 190px minmax(150px, 0.8fr) minmax(0, 1.7fr) 56px 70px 60px;
  gap: 10px;
  align-items: center;
  padding: 7px 12px;
  font-size: 12px;
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
  line-height: 22px;
}

.file-row:last-child {
  border-bottom: 0;
}

.file-row:hover {
  background: var(--bg-2);
}

.file-time,
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

.file-ops {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}

.file-ops .link {
  cursor: pointer;
  color: var(--accent);
  font-size: 11.5px;
}

.file-ops .link:hover {
  text-decoration: underline;
}

.file-more,
.empty-hint {
  padding: 10px 12px;
  color: var(--text-4);
  font-size: 11.5px;
  line-height: 1.6;
}

@media (max-width: 1180px) {
  .page-head {
    flex-direction: column;
  }

  .head-actions {
    justify-content: flex-start;
  }

  .res-layout {
    grid-template-columns: 300px minmax(0, 1fr);
  }
}
</style>