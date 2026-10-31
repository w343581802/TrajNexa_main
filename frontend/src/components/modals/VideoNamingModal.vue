<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import { ui } from "../../store/workbench";
import {
  videoNamingApi,
  type VideoNamingBatch,
  type VideoNamingItem,
} from "../../services/videoNaming";

const pathInput = ref("");
const recursive = ref(true);
const renameFolders = ref(false);
const batch = ref<VideoNamingBatch | null>(null);
const error = ref<string | null>(null);
const busy = ref(false);
const fixing = ref<VideoNamingItem | null>(null);
const fixName = ref("");
const fixError = ref<string | null>(null);
const fixBusy = ref(false);
let timer: number | undefined;

const STATUS_TEXT: Record<string, string> = {
  queued: "排队中",
  running: "识别中",
  succeeded: "已完成",
  failed: "失败",
  processing: "处理中",
  partial: "部分完成",
};

const TIME_SOURCE_TEXT: Record<string, string> = {
  ocr: "画面角标",
  filename: "文件名兜底",
};

const OCR_TEXT: Record<string, string> = {
  ran: "已识别",
  disabled: "OCR 未启用",
  unavailable: "OCR 不可用",
  error: "OCR 出错",
  no_frame: "未取到画面",
  not_checked: "未检查",
};

const paths = computed(() =>
  pathInput.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean),
);

const processing = computed(() => batch.value?.status === "processing");
const progress = computed(() => {
  if (!batch.value?.total) return 0;
  const sum = batch.value.items.reduce(
    (total, item) => total + (item.status === "succeeded" || item.status === "failed" ? 100 : item.progress || 0),
    0,
  );
  return Math.round(sum / batch.value.total);
});

function stopPolling() {
  if (timer !== undefined) {
    window.clearInterval(timer);
    timer = undefined;
  }
}

let refreshing = false;

async function refresh() {
  if (!batch.value || refreshing) return;
  refreshing = true;
  try {
    batch.value = await videoNamingApi.get(batch.value.batch_id);
    error.value = null;
    if (batch.value.status !== "processing") stopPolling();
  } catch (err) {
    // 单次网络抖动不终止轮询；保持当前表格，等下一轮再试。
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    refreshing = false;
  }
}

function startPolling() {
  stopPolling();
  if (!ui.videoNamingOpen) return;
  timer = window.setInterval(() => void refresh(), 1500);
}

async function start() {
  if (!paths.value.length || busy.value) return;
  stopPolling();
  error.value = null;
  busy.value = true;
  try {
    batch.value = await videoNamingApi.start(paths.value, recursive.value, renameFolders.value);
    startPolling();
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = false;
  }
}

function reset() {
  stopPolling();
  batch.value = null;
  error.value = null;
  fixing.value = null;
  fixError.value = null;
}

function statusLabel(status: string) {
  return STATUS_TEXT[status] ?? status;
}

function timeLabel(item: VideoNamingItem) {
  if (!item.capture_time) return "未识别";
  const source = TIME_SOURCE_TEXT[item.time_source];
  return source ? `${item.capture_time.replace("T", " ")}（${source}）` : item.capture_time.replace("T", " ");
}

function statusClass(status: string) {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "bad";
  if (status === "running") return "busy";
  return "idle";
}

function dirOf(path: string) {
  const index = path.lastIndexOf("/");
  const index2 = path.lastIndexOf("\\");
  const cut = Math.max(index, index2);
  return cut > 0 ? path.slice(0, cut) : ".";
}

function needsManual(item: VideoNamingItem) {
  return item.status === "succeeded" || item.status === "failed";
}

function openFix(item: VideoNamingItem) {
  fixing.value = item;
  fixName.value = item.renamed_name || item.original_name;
  fixError.value = null;
}

function closeFix() {
  if (fixBusy.value) return;
  fixing.value = null;
  fixError.value = null;
}

async function submitFix() {
  if (!batch.value || !fixing.value || fixBusy.value) return;
  const name = fixName.value.trim();
  if (!name) {
    fixError.value = "请输入文件名";
    return;
  }
  fixBusy.value = true;
  fixError.value = null;
  try {
    const updated = await videoNamingApi.rename(batch.value.batch_id, fixing.value.media_id, name);
    const index = batch.value.items.findIndex((item) => item.media_id === updated.media_id);
    if (index >= 0) batch.value.items[index] = updated;
    fixing.value = null;
  } catch (err) {
    fixError.value = err instanceof Error ? err.message : String(err);
  } finally {
    fixBusy.value = false;
  }
}

watch(
  () => ui.videoNamingOpen,
  (open) => {
    if (!open) {
      stopPolling();
    } else if (batch.value?.status === "processing") {
      startPolling();
    }
  },
);
onBeforeUnmount(stopPolling);
</script>

<template>
  <BaseModal
    :open="ui.videoNamingOpen"
    title="视频命名"
    subtitle="后端直接读取本地录像：OCR 开头 0.5 / 2 / 4 秒画面的摄像头名称与时间，把文件改成「名称_时间」，并生成表格"
    width="xl"
    height="min(88vh, 900px)"
    @close="ui.videoNamingOpen = false"
  >
    <div class="vn">
      <section class="vn-input">
        <label class="vn-field">
          <span class="vn-label">视频文件或目录路径（每行一个）</span>
          <textarea
            v-model="pathInput"
            class="vn-paths mono"
            rows="3"
            spellcheck="false"
            placeholder="G:\测试数据\视频\正常&#10;G:\测试数据\视频\正常\YD-桂康路中仁路T口2-1_20261023150000至20261023153059"
            :disabled="busy"
          />
        </label>
        <div class="vn-row">
          <label class="vn-check">
            <input v-model="recursive" type="checkbox" :disabled="busy" />
            <span>目录包含子目录</span>
          </label>
          <label class="vn-check" title="同一子文件夹内的视频必须识别为同一摄像头，才会按「摄像头_开始至结束」重命名文件夹">
            <input v-model="renameFolders" type="checkbox" :disabled="busy" />
            <span>同时重命名子文件夹</span>
          </label>
          <span class="vn-hint">
            支持宿主机路径（如 <code>G:\测试数据\视频\正常</code>）或容器可写路径；目录会递归遍历子文件夹。识别后直接重命名原文件（不改内容、不转码）。
          </span>
          <button class="btn btn--primary" type="button" :disabled="busy || !paths.length" @click="start">
            <AppIcon name="scan" :size="13" />
            {{ busy ? "正在创建批次…" : "开始识别并重命名" }}
          </button>
        </div>
      </section>

      <p v-if="error" class="vn-error">{{ error }}</p>

      <section v-if="batch" class="vn-result">
        <div class="vn-result-head">
          <div>
            <strong>批次 {{ batch.batch_id.slice(0, 8) }}</strong>
            <span class="vn-batch-status" :data-tone="statusClass(batch.status)">{{ statusLabel(batch.status) }}</span>
            <span class="vn-count">{{ batch.completed }}/{{ batch.total }} 已处理</span>
          </div>
          <div class="vn-progress" role="progressbar" :aria-valuenow="progress" aria-valuemin="0" aria-valuemax="100">
            <i :style="{ width: `${progress}%` }" />
          </div>
        </div>

        <div class="vn-table-wrap">
          <table class="vn-table">
            <thead>
              <tr>
                <th>#</th>
                <th>原始文件</th>
                <th>新文件</th>
                <th>文件夹</th>
                <th>摄像头名称（OCR）</th>
                <th>拍摄时间</th>
                <th>状态</th>
                <th>证据</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, index) in batch.items" :key="item.media_id">
                <td class="num">{{ index + 1 }}</td>
                <td class="vn-original" :title="item.original_path || item.original_name">
                  <span class="vn-name">{{ item.original_name }}</span>
                  <span v-if="item.original_path" class="vn-dir mono">{{ dirOf(item.original_path) }}</span>
                </td>
                <td>
                  <span v-if="item.renamed_name" class="vn-renamed mono" :title="item.renamed_path || item.renamed_name">{{ item.renamed_name }}</span>
                  <span v-else class="vn-muted">—</span>
                  <span v-if="item.needs_review && item.status === 'succeeded'" class="vn-review">需核对</span>
                  <button v-if="needsManual(item)" class="link vn-fix-link" type="button" @click="openFix(item)">
                    {{ item.needs_review || item.status === "failed" ? "手动命名" : "改名" }}
                  </button>
                </td>
                <td>
                  <span v-if="item.folder_renamed_name" class="vn-folder" :title="item.folder_renamed_path">{{ item.folder_renamed_name }}</span>
                  <span v-else class="vn-muted">—</span>
                </td>
                <td>
                  <span v-if="item.camera_name">{{ item.camera_name }}</span>
                  <span v-else class="vn-muted">未识别</span>
                  <span v-if="item.ocr_status && item.ocr_status !== 'ran'" class="vn-muted vn-ocr">
                    {{ OCR_TEXT[item.ocr_status] ?? item.ocr_status }}
                  </span>
                </td>
                <td class="vn-time">{{ timeLabel(item) }}</td>
                <td>
                  <span class="vn-status" :data-tone="statusClass(item.status)">
                    {{ statusLabel(item.status) }}
                  </span>
                  <span v-if="item.error" class="vn-error-inline" :title="item.error">{{ item.error }}</span>
                </td>
                <td>
                  <a v-if="item.frame_url" class="link" :href="item.frame_url" target="_blank" rel="noopener">看截图</a>
                  <span v-else class="vn-muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p v-if="batch.status === 'partial' || batch.status === 'failed'" class="vn-warn">
          有视频处理失败；失败原因见状态列。改名失败的文件不会被删除，修正后可以重新提交路径。
        </p>
      </section>

      <div v-else class="vn-empty">
        <AppIcon name="video" :size="30" />
        <p>还没有开始。填入本地录像路径或目录后，这里会显示识别结果和命名表格。</p>
      </div>
    </div>

    <template #footer>
      <span class="vn-foot-note">
        命名口径：<code>摄像头名称_YYYYMMDD_HHMMSS.扩展名</code>；OCR 缺失时保留原名并标「OCR未识别」。
      </span>
      <div class="vn-foot-actions">
        <button v-if="batch" class="btn" type="button" :disabled="processing" @click="reset">再处理一批</button>
        <a
          v-if="batch"
          class="btn btn--primary"
          :class="{ 'is-disabled': !batch.can_download_table }"
          :href="batch.can_download_table ? batch.table_url : undefined"
          :aria-disabled="!batch.can_download_table"
        >
          <AppIcon name="download" :size="13" />下载表格 CSV
        </a>
        <button class="btn" type="button" @click="ui.videoNamingOpen = false">关闭</button>
      </div>
    </template>
  </BaseModal>

  <BaseModal
    :open="!!fixing"
    title="手动命名"
    subtitle="左侧是视频开头截图；核对后填写新文件名，确定后直接修改挂载目录里的原文件"
    width="lg"
    height="min(80vh, 760px)"
    @close="closeFix"
  >
    <div v-if="fixing" class="vn-fix">
      <div class="vn-fix-shot">
        <img v-if="fixing.frame_url" :src="fixing.frame_url" alt="视频开头截图" />
        <div v-else class="vn-fix-noshot">没有可用截图</div>
        <span class="vn-fix-cap">开头截图（OCR 原图）</span>
      </div>
      <div class="vn-fix-form">
        <label class="vn-fix-field">
          <span>原文件名</span>
          <strong class="mono">{{ fixing.original_name }}</strong>
        </label>
        <label class="vn-fix-field">
          <span>所在文件夹</span>
          <strong class="mono vn-fix-dir" :title="dirOf(fixing.renamed_path || fixing.original_path)">
            {{ dirOf(fixing.renamed_path || fixing.original_path) }}
          </strong>
        </label>
        <label class="vn-fix-field">
          <span>新文件名</span>
          <input
            v-model="fixName"
            class="vn-fix-input mono"
            spellcheck="false"
            :disabled="fixBusy"
            @keyup.enter="submitFix"
          />
        </label>
        <p class="vn-fix-hint">不写扩展名时沿用当前扩展名；写错扩展名会被拒绝，避免文件打不开。</p>
        <p v-if="fixError" class="vn-error">{{ fixError }}</p>
      </div>
    </div>

    <template #footer>
      <span class="vn-foot-note">确定后会立即重命名磁盘文件，表格和 CSV 同步更新。</span>
      <div class="vn-foot-actions">
        <button class="btn" type="button" :disabled="fixBusy" @click="closeFix">取消</button>
        <button class="btn btn--primary" type="button" :disabled="fixBusy || !fixName.trim()" @click="submitFix">
          <AppIcon name="check" :size="13" />
          {{ fixBusy ? "正在改名…" : "确定" }}
        </button>
      </div>
    </template>
  </BaseModal>
</template>

<style scoped>
.vn {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 16px 20px;
  overflow: auto;
}

.vn-input {
  flex: none;
  display: grid;
  gap: 10px;
}

.vn-field {
  display: grid;
  gap: 6px;
}

.vn-label {
  font-size: 11px;
  color: var(--text-3);
  letter-spacing: 0.04em;
}

.vn-paths {
  width: 100%;
  resize: vertical;
  min-height: 74px;
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  background: var(--bg-1);
  color: var(--text-0);
  font-size: 12px;
  line-height: 1.6;
}

.vn-paths:focus {
  outline: none;
  border-color: var(--accent-line);
  box-shadow: 0 0 0 2px var(--accent-soft);
}

.vn-row {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.vn-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-2);
  cursor: pointer;
  flex: none;
}

.vn-hint {
  flex: 1;
  min-width: 200px;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-3);
}

.vn-error {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--danger-line, #5b2a2a);
  border-radius: 6px;
  background: var(--danger-soft, rgba(220, 70, 70, 0.12));
  color: var(--danger-hi, #ff9c9c);
  font-size: 11.5px;
}

.vn-result {
  display: grid;
  gap: 10px;
  min-height: 0;
}

.vn-result-head {
  display: flex;
  align-items: center;
  gap: 12px;
  justify-content: space-between;
}

.vn-result-head > div:first-child {
  display: flex;
  align-items: center;
  gap: 8px;
}

.vn-batch-status {
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  font-size: 10px;
}

.vn-batch-status[data-tone="ok"] {
  color: var(--ok);
  border-color: var(--ok-line, rgba(47, 207, 146, 0.4));
}

.vn-batch-status[data-tone="bad"] {
  color: var(--danger-hi, #ff9c9c);
}

.vn-count {
  font-size: 10.5px;
  color: var(--text-3);
}

.vn-progress {
  width: 220px;
  height: 4px;
  border-radius: 99px;
  background: var(--bg-3);
  overflow: hidden;
}

.vn-progress i {
  display: block;
  height: 100%;
  background: var(--ok);
  transition: width 0.3s var(--ease);
}

.vn-table-wrap {
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
  max-height: 52vh;
}

.vn-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}

.vn-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  text-align: left;
  font-weight: 500;
  color: var(--text-3);
  background: var(--bg-2);
  padding: 7px 9px;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}

.vn-table td {
  padding: 7px 9px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}

.vn-table tbody tr:hover {
  background: rgba(255, 255, 255, 0.02);
}

.vn-name {
  display: block;
  color: var(--text-1);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vn-dir {
  display: block;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-4);
  font-size: 9.5px;
}

.vn-renamed {
  display: inline-block;
  color: var(--ok);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: top;
}

.vn-review {
  margin-left: 4px;
  padding: 0 4px;
  border-radius: 3px;
  border: 1px solid var(--warn-line, rgba(230, 160, 60, 0.45));
  color: var(--warn, #e6a03c);
  font-size: 9px;
}

.vn-folder {
  display: inline-block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--accent-hi);
  vertical-align: top;
}

.vn-fix-link {
  margin-left: 8px;
  padding-left: 8px;
  border-left: 1px solid var(--line);
  font-size: 10px;
  display: inline-block;
  vertical-align: top;
}

.vn-fix {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
  gap: 14px;
  height: 100%;
  min-height: 0;
}

.vn-fix-shot {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 260px;
  background: #000;
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
}

.vn-fix-shot img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.vn-fix-noshot {
  color: var(--text-4);
  font-size: 12px;
}

.vn-fix-cap {
  position: absolute;
  left: 8px;
  bottom: 6px;
  font-size: 10px;
  color: var(--text-4);
}

.vn-fix-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.vn-fix-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.vn-fix-field > span {
  font-size: 10px;
  color: var(--text-4);
}

.vn-fix-field > strong {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-1);
  word-break: break-all;
}

.vn-fix-dir {
  color: var(--text-2) !important;
  font-size: 10px !important;
}

.vn-fix-input {
  width: 100%;
  height: 32px;
  padding: 0 8px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm, 4px);
  color: var(--text-1);
  font-size: 12px;
}

.vn-fix-input:focus {
  outline: none;
  border-color: var(--accent);
}

.vn-fix-hint {
  margin: 0;
  font-size: 10px;
  color: var(--text-4);
  line-height: 1.5;
}

.vn-muted {
  color: var(--text-4);
}

.vn-ocr {
  display: block;
  font-size: 9.5px;
}

.vn-time {
  white-space: nowrap;
  color: var(--text-2);
}

.vn-status {
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--line-2);
  font-size: 10px;
  white-space: nowrap;
}

.vn-status[data-tone="ok"] {
  color: var(--ok);
  border-color: var(--ok-line, rgba(47, 207, 146, 0.4));
}

.vn-status[data-tone="bad"] {
  color: var(--danger-hi, #ff9c9c);
}

.vn-status[data-tone="busy"] {
  color: var(--accent-hi);
}

.vn-error-inline {
  display: block;
  margin-top: 3px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--danger-hi, #ff9c9c);
  font-size: 9.5px;
}

.vn-warn {
  margin: 0;
  font-size: 10.5px;
  color: var(--warn, #e6a03c);
}

.vn-empty {
  display: grid;
  place-items: center;
  gap: 8px;
  padding: 46px 0;
  color: var(--text-4);
  text-align: center;
  font-size: 11.5px;
}

.vn-foot-note {
  font-size: 10.5px;
  color: var(--text-3);
}

.vn-foot-note code {
  color: var(--text-2);
}

.vn-foot-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.link {
  color: var(--accent-hi);
  font-size: 11px;
  cursor: pointer;
}

.link:hover {
  text-decoration: underline;
}

.btn.is-disabled {
  opacity: 0.45;
  pointer-events: none;
}
</style>
