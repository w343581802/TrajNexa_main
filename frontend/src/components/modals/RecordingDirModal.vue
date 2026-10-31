<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import { mainApi, type RecordingBrowse } from "../../services/mainApi";
import { actions, ui, wb } from "../../store/workbench";

const data = ref<RecordingBrowse | null>(null);
/** 手填路径：支持宿主机路径（G:\测试数据\视频\正常）、容器绝对路径、相对录像根目录的路径 */
const manualPath = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
/** 视频模式下被点中的那个文件：只用来提示「所在的目录会被登记」，不改扫描范围 */
const pickedFile = ref<string | null>(null);

const isVideoMode = computed(() => ui.recordingDirMode === "video");
const modalTitle = computed(() => (isVideoMode.value ? "添加视频" : "挂载录像目录"));
const modalSubtitle = computed(() =>
  isVideoMode.value
    ? "像文件对话框一样点开目录、点中视频文件；确认后登记这个视频所在的目录，按只读方式读取开头几秒的角标。也可以直接把路径贴到下面的输入框。"
    : "像文件对话框一样逐级点开目录，选中哪一级就挂载哪一级；登记后按目录扫描，只读开头几秒的角标。也可以直接把路径贴到下面的输入框。",
);

const dirs = computed(() => data.value?.dirs ?? []);
const videos = computed(() => data.value?.videos ?? []);
const rootLabel = computed(() => data.value?.root ?? "录像根目录");
const parentPath = computed(() => data.value?.parent ?? "");

const crumbs = computed(() => {
  const path = data.value?.path ?? "";
  const trail = [{ label: "录像根目录", path: "" }];
  path.split("/").filter(Boolean).forEach((part, index, parts) => {
    trail.push({ label: part, path: parts.slice(0, index + 1).join("/") });
  });
  return trail;
});

const currentName = computed(() => {
  const path = data.value?.path ?? "";
  return path ? path.split("/").pop() || path : rootLabel.value;
});

const canGoUp = computed(() => !!data.value && data.value.parent !== data.value.path);
const hasVideos = computed(() => videos.value.length > 0);
const canConfirm = computed(() => {
  if (loading.value || !wb.caseId) return false;
  return isVideoMode.value ? hasVideos.value : !!data.value;
});

const confirmLabel = computed(() => {
  if (!data.value) return "读取中…";
  if (isVideoMode.value) {
    return hasVideos.value
      ? `添加「${currentName.value}」的视频（${videos.value.length} 个）`
      : "这个目录里没有视频";
  }
  return `挂载「${currentName.value}」`;
});

const pickedHint = computed(() => {
  if (!isVideoMode.value || !pickedFile.value || !data.value) return "";
  return `已选中「${pickedFile.value}」；确认后登记它所在的目录「${currentName.value}」，这个目录里的 ${videos.value.length} 个视频会一起按只读方式扫描。`;
});

/** 接口不通时不要甩英文 Not Found 给用户 */
function humanError(err: unknown) {
  const text = err instanceof Error ? err.message : String(err);
  if (/404|not found/i.test(text)) {
    return "录像接口不可用（HTTP 404）：后端还没有包含 /api/v1/recordings，重建 api 容器后再打开这个窗口。";
  }
  if (/failed to fetch|networkerror|load failed/i.test(text)) {
    return "连不上主系统后端：确认 api 容器在运行，再点「重试」。";
  }
  return text;
}

async function load(path?: string) {
  if (loading.value) return;
  loading.value = true;
  error.value = null;
  pickedFile.value = null;
  try {
    data.value = await mainApi.browseRecordingDirs(path ?? data.value?.path ?? "");
  } catch (err) {
    error.value = humanError(err);
  } finally {
    loading.value = false;
  }
}

watch(
  () => ui.recordingDirOpen,
  (open) => {
    if (open) void load("");
  },
);

function close() {
  ui.recordingDirOpen = false;
}

/** 登记目录（视频模式是当前目录，目录模式也是当前目录）；成功后关窗 */
async function confirm() {
  if (!data.value || !canConfirm.value) return;
  const source = await actions.addRecordingDir(data.value.path);
  if (source) close();
}

/** 直接把路径交给后端解析（与视频命名工具同一套口径），按返回的目录继续浏览 */
async function goManual() {
  const text = manualPath.value.trim();
  if (!text || loading.value) return;
  await load(text);
  if (!error.value) manualPath.value = "";
}

function pickFile(path: string) {
  pickedFile.value = pickedFile.value === path ? null : path;
}
</script>

<template>
  <BaseModal
    :open="ui.recordingDirOpen"
    :title="modalTitle"
    :subtitle="modalSubtitle"
    width="lg"
    @close="close"
  >
    <div class="rd-body">
      <div class="rd-bar">
        <button class="rd-icon-btn" :disabled="loading" title="回到录像根目录" @click="load('')">
          <AppIcon name="folder" :size="13" />
        </button>
        <button class="rd-icon-btn" :disabled="loading || !canGoUp" title="上一级" @click="load(parentPath)">
          <AppIcon name="chevron-up" :size="13" />
        </button>
        <div class="rd-crumbs">
          <template v-for="(crumb, index) in crumbs" :key="crumb.path || 'root'">
            <button
              class="rd-crumb"
              :class="{ 'is-last': index === crumbs.length - 1 }"
              :disabled="index === crumbs.length - 1 || loading"
              @click="load(crumb.path)"
            >
              {{ crumb.label }}
            </button>
            <AppIcon v-if="index < crumbs.length - 1" class="rd-crumb-sep" name="chevron-right" :size="10" />
          </template>
        </div>
        <button class="rd-icon-btn" :disabled="loading" title="刷新" @click="load()">
          <AppIcon name="refresh" :size="13" />
        </button>
      </div>

      <p class="rd-root mono">
        {{ rootLabel }} <span class="rd-root-note">只读挂载</span>
      </p>

      <div class="rd-path">
        <input
          v-model="manualPath"
          class="rd-path-input mono"
          type="text"
          spellcheck="false"
          placeholder="也可以直接填路径：G:\测试数据\视频\正常 或 正常/某摄像头目录"
          aria-label="录像目录路径"
          @keyup.enter="goManual"
        />
        <button class="btn btn--outline" :disabled="loading || !manualPath.trim()" @click="goManual">
          打开
        </button>
      </div>

      <div v-if="error" class="rd-error">
        <AppIcon name="warning" :size="13" />
        <span class="rd-error-text">{{ error }}</span>
        <button class="btn btn--outline" :disabled="loading" @click="load()">重试</button>
      </div>

      <div class="rd-list scroll-y">
        <button v-if="canGoUp" class="rd-row rd-row--up" :disabled="loading" @click="load(parentPath)">
          <AppIcon name="chevron-up" :size="13" />
          <span>返回上一级</span>
        </button>

        <button
          v-for="dir in dirs"
          :key="dir.path"
          class="rd-row"
          :disabled="loading"
          @click="load(dir.path)"
        >
          <AppIcon name="folder" :size="14" />
          <span class="rd-name">{{ dir.name }}</span>
          <span class="rd-meta num">{{ dir.video_count }} 个视频</span>
          <AppIcon name="chevron-right" :size="12" />
        </button>

        <button
          v-for="file in videos"
          :key="file.path"
          class="rd-row rd-row--file"
          :class="{ 'is-picked': pickedFile === file.name }"
          :disabled="loading || !isVideoMode"
          @click="pickFile(file.name)"
        >
          <span v-if="isVideoMode" class="rd-check" :class="{ 'is-on': pickedFile === file.name }">
            <AppIcon v-if="pickedFile === file.name" name="check" :size="10" />
          </span>
          <AppIcon v-else name="video" :size="13" />
          <span class="rd-name truncate">{{ file.name }}</span>
          <span class="rd-meta num">{{ (file.size_bytes / 1024 / 1024).toFixed(1) }} MB</span>
        </button>

        <p v-if="!loading && !dirs.length && !videos.length" class="rd-hint">
          这个目录下没有子目录或视频文件；回到上一级再找找。
        </p>
        <p v-else-if="loading" class="rd-hint">读取目录…</p>
      </div>

      <p v-if="pickedHint" class="rd-picked">{{ pickedHint }}</p>
    </div>

    <template #footer>
      <span class="rd-current mono" :title="data?.path || rootLabel">{{ data?.path || rootLabel }}</span>
      <span class="grow" />
      <button class="btn btn--outline" @click="close">取消</button>
      <button class="btn btn--primary" :disabled="!canConfirm" @click="confirm">{{ confirmLabel }}</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.rd-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px 4px;
  min-height: 0;
  overflow: hidden;
}

.rd-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.rd-path {
  display: flex;
  align-items: center;
  gap: 6px;
}

.rd-path-input {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: #0b1017;
  color: var(--text-0);
  font-size: 12px;
}

.rd-path-input:focus {
  outline: none;
  border-color: var(--line-2);
}

.rd-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 24px;
  height: 24px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: #0b1017;
  color: var(--text-2);
  cursor: pointer;
}

.rd-icon-btn:hover:not(:disabled) {
  border-color: var(--line-2);
  color: var(--text-0);
}

.rd-icon-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.rd-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px;
  min-width: 0;
  font-size: 12px;
}

.rd-crumb {
  padding: 2px 6px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
}

.rd-crumb:hover:not(:disabled) {
  border-color: var(--line);
  color: var(--text-0);
}

.rd-crumb.is-last {
  color: var(--text-0);
  cursor: default;
}

.rd-crumb-sep {
  color: var(--text-4);
}

.rd-root {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-3);
}

.rd-root-note {
  padding: 1px 6px;
  border-radius: 999px;
  background: #17202c;
  font-size: 10px;
  color: var(--text-3);
}

.rd-list {
  display: grid;
  gap: 2px;
  align-content: start;
  min-height: 220px;
  max-height: 46vh;
  padding: 4px 0;
  border-top: 1px solid var(--line);
}

.rd-row {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr) auto 14px;
  gap: 8px;
  align-items: center;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-1);
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.rd-row:hover:not(:disabled) {
  border-color: var(--line);
  background: #0b1017;
}

.rd-row:disabled {
  cursor: default;
}

.rd-row--up {
  grid-template-columns: 16px minmax(0, 1fr);
  color: var(--text-2);
}

/* 文件行：左边是选择框（视频模式点得动，目录模式只是展示） */
.rd-row--file {
  grid-template-columns: 16px minmax(0, 1fr) auto;
  color: var(--text-2);
}

.rd-row--file.is-picked {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--text-0);
}

.rd-check {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  border: 1px solid var(--line-2);
  border-radius: 3px;
  color: #0b1017;
}

.rd-check.is-on {
  border-color: var(--accent);
  background: var(--accent);
}

.rd-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rd-meta {
  font-size: 11px;
  color: var(--text-3);
}

.rd-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 9px;
  border: 1px solid rgba(255, 110, 110, 0.35);
  border-radius: 6px;
  font-size: 11.5px;
  color: var(--danger);
}

.rd-error-text {
  flex: 1;
  min-width: 0;
}

.rd-hint {
  padding: 10px 2px;
  font-size: 11.5px;
  color: var(--text-3);
}

.rd-picked {
  padding: 2px 0 6px;
  font-size: 11px;
  line-height: 1.55;
  color: var(--text-2);
}

.rd-current {
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-3);
}

.grow {
  flex: 1;
}
</style>
