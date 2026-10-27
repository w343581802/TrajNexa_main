<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import StatusChip from "../ui/StatusChip.vue";
import { actions, elementList, toast, ui, type ElementPayload } from "../../store/workbench";
import {
  CAMERA_STATUS_TEXT,
  TIME_STATUS_TEXT,
  cameraNameCandidates,
  mainApi,
  runInspect,
  timeCandidates,
  type CameraCatalogEntry,
  type InspectionReport,
  type MediaItem,
} from "../../services/mainApi";

const CEILING_EVENT_DATE = "2026-10-25";

/* ------------------------------ 媒体 ------------------------------ */

const mediaList = ref<MediaItem[]>([]);
const mediaQuery = ref("");
const mediaLoading = ref(false);
const mediaError = ref<string | null>(null);
const selectedMediaId = ref<string | null>(null);

const selectedMedia = computed(() => mediaList.value.find((item) => item.id === selectedMediaId.value) ?? null);

const filteredMedia = computed(() => {
  const keyword = mediaQuery.value.trim().toLowerCase();
  const seen = new Set<string>();
  const unique = [...mediaList.value]
    .sort((a, b) => b.ingested_at.localeCompare(a.ingested_at))
    .filter((item) => {
      if (seen.has(item.sha256)) return false;
      seen.add(item.sha256);
      return true;
    });
  if (!keyword) return unique.slice(0, 12);
  return unique.filter((item) => item.original_name.toLowerCase().includes(keyword)).slice(0, 12);
});

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

async function loadMedia() {
  mediaLoading.value = true;
  mediaError.value = null;
  try {
    mediaList.value = await mainApi.listMedia();
    if (!selectedMediaId.value && mediaList.value.length) selectedMediaId.value = mediaList.value[0].id;
  } catch (error) {
    mediaError.value = error instanceof Error ? error.message : String(error);
  } finally {
    mediaLoading.value = false;
  }
}

async function uploadFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  mediaLoading.value = true;
  mediaError.value = null;
  try {
    const media = await mainApi.uploadMedia(file);
    await loadMedia();
    selectedMediaId.value = media.id;
    toast(`已上传 ${media.original_name}，可以开始识别`, "ok");
  } catch (error) {
    mediaError.value = error instanceof Error ? error.message : String(error);
  } finally {
    mediaLoading.value = false;
    input.value = "";
  }
}

/* ------------------------------ 识别作业 ------------------------------ */

const inspecting = ref(false);
const evidenceOpen = ref(false);
const jobProgress = ref(0);
const jobStatus = ref<string | null>(null);
const report = ref<InspectionReport | null>(null);
const recognizeError = ref<string | null>(null);

const timeList = computed(() => (report.value ? timeCandidates(report.value.result_json) : []));
const nameList = computed(() => (report.value ? cameraNameCandidates(report.value.result_json) : []));
/** 报告里记录的识别引擎版本，用来核对是否真的跑在 PP-OCRv6 上 */
const engineLabel = computed(() => {
  const engine = report.value?.result_json.time.ocr_engine;
  if (!engine) return "—";
  const library = [engine.library, engine.library_version].filter(Boolean).join(" ");
  return [library, engine.ocr_version].filter(Boolean).join(" · ");
});
/** OCR 没跑起来时不能让人误以为“画面里没有角标/名称” */
const ocrProblem = computed(() => {
  const status = report.value?.result_json.time.ocr_engine_status;
  if (!status || status === "ran") return null;
  if (status === "disabled") return "OCR 未启用：当前 worker 没加载 PaddleOCR 镜像，请用 compose.ocr.yaml 启动。识别为空不代表画面没有角标。";
  if (status === "unavailable") return "OCR 引擎不可用：worker 里缺少 PaddleOCR/PaddlePaddle，识别结果为空不代表画面没有角标。";
  if (status === "error") return "OCR 执行出错：本次识别结果不可用，请重试或人工填写。";
  return `OCR 状态：${status}，识别结果可能不完整。`;
});
const timeStatus = computed(() => {
  const status = report.value?.result_json.time.time_status ?? "";
  return TIME_STATUS_TEXT[status] ?? { label: status || "未识别", tone: "muted" as const };
});

async function recognize() {
  if (!selectedMediaId.value || inspecting.value) return;
  inspecting.value = true;
  jobProgress.value = 0;
  jobStatus.value = "queued";
  recognizeError.value = null;
  try {
    const result = await runInspect(selectedMediaId.value, {
      onProgress: (job) => {
        jobProgress.value = job.progress;
        jobStatus.value = job.status;
      },
    });
    report.value = result;
    jobProgress.value = 100;
    jobStatus.value = "succeeded";
    applyRecognition(result);
    toast("识别完成：已填入时间与摄像头名称，请人工确认", "ok");
  } catch (error) {
    recognizeError.value = error instanceof Error ? error.message : String(error);
    toast(recognizeError.value, "danger");
  } finally {
    inspecting.value = false;
  }
}

/** 把识别结果写进表单：只填不替，人工仍可改 */
function applyRecognition(result: InspectionReport) {
  const best = timeCandidates(result.result_json)[0];
  if (best) {
    timeUnknown.value = false;
    date.value = best.date ?? CEILING_EVENT_DATE;
    timeLabel.value = best.time ?? "";
    timeIndex.value = 0;
  } else {
    timeIndex.value = -1;
  }
  const name = cameraNameCandidates(result.result_json)[0];
  if (name) {
    cameraName.value = name.name;
    nameSource.value = name.source;
    nameIndex.value = 0;
    syncCatalogMatch();
  } else {
    nameIndex.value = -1;
  }
}

/* ------------------------------ 表单 ------------------------------ */

const timeIndex = ref(-1);
const nameIndex = ref(-1);
const date = ref(CEILING_EVENT_DATE);
const timeLabel = ref("");
const timeUnknown = ref(false);
const cameraName = ref<string | null>(null);
const confirmedCamera = ref<string | null>(null);
const catalog = ref<CameraCatalogEntry[]>([]);
const nameSource = ref<string>("");

const catalogMatches = computed(() => {
  const keyword = (cameraName.value ?? "").trim();
  if (!keyword) return [];
  return catalog.value.filter(
    (camera) =>
      camera.display_name === keyword || camera.aliases?.some((alias) => alias === keyword),
  );
});

function pickTime(index: number) {
  const item = timeList.value[index];
  if (!item) return;
  timeIndex.value = index;
  timeUnknown.value = false;
  if (item.date) date.value = item.date;
  if (item.time) timeLabel.value = item.time;
}

/** 后端按目录名称算出的相似候选：只填名称，不改绑定状态 */
const nameSuggestions = computed(() => report.value?.result_json.camera.name_suggestions ?? []);

function applySuggestion(item: { display_name: string; score: number }) {
  cameraName.value = item.display_name;
  nameIndex.value = -1;
  nameSource.value = `相似建议（${Math.round(item.score * 100)}%）`;
  confirmedCamera.value = null;
}

/** 与后端 camera_name_match.MIN_SCORE 对齐，只用于文案 */
const SIMILARITY_THRESHOLD = 0.78;

/** 名称比对的说明文案：区分「目录读不到」「差得远」「目录里没有」三种情况 */
const matchHint = computed(() => {
  const camera = report.value?.result_json.camera;
  const name = cameraName.value;
  if (!camera || !name || catalogMatches.value.length) return null;
  const status = camera.directory_status ?? "";
  if (status.startsWith("map_db_") || status.startsWith("catalog_")) {
    return "镜头目录不可用：本次没读到 Map 台账，因此没有做名称比对（不代表名称写错）。";
  }
  const nearest = camera.directory_nearest;
  if (nearest) {
    const percent = Math.round(nearest.score * 100);
    return `台账里没有相近名称：最接近「${nearest.display_name}」${percent}%，低于 ${Math.round(SIMILARITY_THRESHOLD * 100)}% 阈值，未作为建议。`;
  }
  return `未在镜头目录中匹配到「${name}」（来源：${nameSource.value || "人工填写"}），保存后按候选来源处理。`;
});

function pickName(index: number) {
  const item = nameList.value[index];
  if (!item) return;
  nameIndex.value = index;
  cameraName.value = item.name;
  nameSource.value = item.source;
  syncCatalogMatch();
}

function syncCatalogMatch() {
  confirmedCamera.value = catalogMatches.value[0]?.camera_uuid ?? null;
}

function markUnlocated() {
  cameraName.value = null;
  nameIndex.value = -1;
  confirmedCamera.value = null;
  nameSource.value = "";
}

function markTimeUnknown() {
  timeUnknown.value = true;
  timeIndex.value = -1;
  timeLabel.value = "";
}

const category = ref<"person" | "vehicle" | "other">("person");

const attributes = reactive([
  { key: "上衣", value: "", unknown: false },
  { key: "下装", value: "", unknown: false },
  { key: "鞋", value: "", unknown: false },
  { key: "随身物", value: "", unknown: false },
]);

const note = ref("");

const statusOfCamera = computed(() => {
  if (confirmedCamera.value) return { id: "confirmed" as const, label: "来源已确认" };
  if (cameraName.value) return { id: "candidate" as const, label: "候选待核验" };
  return { id: "unlocated" as const, label: "仅确定区域" };
});

/** 状态只描述「当前确认到的名称」与目录的关系，不照抄后端 match_status 的结论 */
const cameraStatus = computed(() => {
  if (confirmedCamera.value) return { label: "已匹配镜头目录", tone: "ok" as const };
  if (cameraName.value) {
    const mined = nameSource.value.includes("OCR");
    const suggested = nameSource.value.includes("相似建议");
    return {
      label: suggested
        ? "已采用相似名称，未绑定镜头目录"
        : mined
          ? "OCR 文本疑似名称，未结构化"
          : "已识别名称，未绑定镜头目录",
      tone: "warn" as const,
    };
  }
  const status = report.value?.result_json.camera.match_status ?? "";
  return CAMERA_STATUS_TEXT[status] ?? { label: status ? `未识别（${status}）` : "尚未识别", tone: "muted" as const };
});

/** 名称输入框始终可用：识别结果只做初值，人工可改写或清空 */
const cameraNameInput = computed({
  get: () => cameraName.value ?? "",
  set: (value: string) => {
    const text = value.trim();
    cameraName.value = text || null;
    nameIndex.value = -1;
    nameSource.value = text ? "人工填写" : "";
    confirmedCamera.value = null;
    if (text) syncCatalogMatch();
  },
});

const nextElementId = computed(() => `E${String(ui.nextElementSeq).padStart(2, "0")}`);
const editingElement = computed(() =>
  ui.editingElementId ? elementList.value.find((item) => item.id === ui.editingElementId) ?? null : null,
);

function resetForm() {
  timeIndex.value = -1;
  nameIndex.value = -1;
  selectedMediaId.value = mediaList.value[0]?.id ?? null;
  date.value = CEILING_EVENT_DATE;
  timeLabel.value = "";
  timeUnknown.value = false;
  cameraName.value = null;
  confirmedCamera.value = null;
  nameSource.value = "";
  category.value = "person";
  attributes.forEach((attr) => {
    attr.value = "";
    attr.unknown = false;
  });
  note.value = "";
  report.value = null;
  recognizeError.value = null;
  jobProgress.value = 0;
  jobStatus.value = null;
  evidenceOpen.value = false;
}

function fillFromElement() {
  const element = editingElement.value;
  if (!element) return;
  date.value = element.time.slice(0, 10) || CEILING_EVENT_DATE;
  timeUnknown.value = Boolean(element.timeUnknown);
  timeLabel.value = element.timeUnknown ? "" : element.timeLabel;
  cameraName.value = element.cameraId ? element.cameraLabel : null;
  confirmedCamera.value = element.cameraId;
  category.value = element.category ?? "person";
  note.value = element.note ?? "";
  element.attributes?.forEach((saved) => {
    const target = attributes.find((attr) => attr.key === saved.key);
    if (target) {
      target.value = saved.value;
      target.unknown = saved.unknown;
    }
  });
}

watch(
  () => ui.addElementOpen,
  (open) => {
    if (!open) return;
    resetForm();
    fillFromElement();
    void loadMedia();
    void mainApi
      .listCameras()
      .then((catalogPage) => {
        catalog.value = catalogPage.cameras ?? [];
        if (cameraName.value) syncCatalogMatch();
      })
      .catch(() => {
        catalog.value = [];
      });
  },
);

function save(keepOpen: boolean) {
  const payload: ElementPayload = {
    date: date.value,
    timeLabel: timeLabel.value,
    timeUnknown: timeUnknown.value,
    cameraId: confirmedCamera.value,
    cameraLabel: cameraName.value
      ? `${cameraName.value}${confirmedCamera.value ? "" : "（未绑定目录）"}`
      : "区域：仅确定大致区域",
    status: statusOfCamera.value.id,
    statusLabel: statusOfCamera.value.label,
    note: note.value,
    tone: confirmedCamera.value ? "color" : "gray",
    quality: "good",
    category: category.value,
    attributes: attributes.map((attr) => ({ key: attr.key, value: attr.value, unknown: attr.unknown })),
    boxes: [],
  };

  const id = editingElement.value
    ? actions.updateElement(editingElement.value.id, payload)
    : actions.saveElement(payload);
  if (!id) return;
  if (keepOpen) {
    resetForm();
    return;
  }
  ui.addElementOpen = false;
}
</script>

<template>
  <BaseModal
    :open="ui.addElementOpen"
    :title="editingElement ? `编辑目标要素 · ${editingElement.id}` : `添加目标要素 · ${nextElementId}`"
    subtitle="先识别画面角标时间与摄像头名称，人工确认后再保存；人体 / 人脸 / 车辆识别尚未接入，不做自动检测。"
    width="xl"
    height="92vh"
    @close="ui.addElementOpen = false"
  >
    <div class="ae">
      <!-- 左：媒体与识别依据 -->
      <section class="ae-left">
        <div class="ae-bar">
          <div class="ae-search">
            <AppIcon name="search" :size="13" />
            <input v-model="mediaQuery" type="text" placeholder="搜索已入库媒体" aria-label="搜索媒体" />
          </div>
          <select v-model="selectedMediaId" class="ae-select" aria-label="选择画面">
            <option v-if="!filteredMedia.length" value="">没有匹配的媒体</option>
            <option v-for="media in filteredMedia" :key="media.id" :value="media.id">
              {{ media.kind === "video" ? "视频" : "图片" }} · {{ media.original_name }}
            </option>
          </select>
          <button class="btn btn--sm btn--ghost" :disabled="mediaLoading" @click="loadMedia">
            <AppIcon name="refresh" :size="12" />刷新
          </button>
          <label class="btn btn--sm btn--outline">
            <AppIcon name="plus" :size="12" />上传
            <input type="file" accept="image/*,video/*" hidden @change="uploadFile" />
          </label>
          <span class="grow" />
          <button class="btn btn--sm btn--primary ae-run" :disabled="!selectedMediaId || inspecting" @click="recognize">
            <AppIcon name="scan" :size="12" />{{ inspecting ? "识别中…" : report ? "重新识别" : "开始识别" }}
          </button>
        </div>

        <div class="preview">
          <template v-if="selectedMedia">
            <img
              v-if="selectedMedia.kind === 'image'"
              :src="mainApi.mediaContentUrl(selectedMedia.id)"
              :alt="selectedMedia.original_name"
            />
            <video v-else :src="mainApi.mediaContentUrl(selectedMedia.id)" controls preload="metadata" />
            <span class="preview-tag">只读原始画面 · 未做任何改写</span>
          </template>
          <p v-else class="preview-empty">从上方选择或上传一张画面，识别角标时间与摄像头名称</p>
        </div>

        <p v-if="mediaError" class="ae-error">{{ mediaError }}</p>

        <div class="ae-evidence" :class="{ 'is-open': evidenceOpen }">
          <div class="ae-evidence-head">
            <span class="ae-block-title">识别依据</span>
            <span v-if="selectedMedia" class="ae-block-hint truncate">
              {{ selectedMedia.mime_detected }} · {{ formatBytes(selectedMedia.size_bytes) }} · sha256 {{ selectedMedia.sha256.slice(0, 10) }}…
            </span>
            <span v-if="inspecting" class="ae-progress">
              <span class="ae-progress-bar"><i :style="{ width: `${jobProgress}%` }" /></span>
              <span class="num dim">{{ jobProgress }}%</span>
            </span>
            <button class="chip-btn ae-evidence-toggle" @click="evidenceOpen = !evidenceOpen">
              {{ evidenceOpen ? "收起" : "展开" }}
            </button>
          </div>

          <p v-if="report && !evidenceOpen" class="ae-evidence-summary">
            {{ report.result_json.media.technical_metadata?.width ?? "—" }} ×
            {{ report.result_json.media.technical_metadata?.height ?? "—" }} ·
            {{ report.result_json.quality?.overall_status ?? "—" }} ·
            OCR {{ (report.result_json.time.overlay_time_samples[0]?.ocr_rows ?? []).length }} 行
          </p>

          <p v-if="ocrProblem" class="ae-error">{{ ocrProblem }}</p>

          <p v-if="recognizeError" class="ae-error">{{ recognizeError }}</p>

          <template v-if="evidenceOpen">
            <div v-if="report" class="evidence">
              <div class="evidence-row">
                <span class="evidence-key">画面尺寸</span>
                <span class="num">
                  {{ report.result_json.media.technical_metadata?.width ?? "—" }} ×
                  {{ report.result_json.media.technical_metadata?.height ?? "—" }}
                  <template v-if="report.result_json.media.technical_metadata?.duration_seconds">
                    · {{ report.result_json.media.technical_metadata.duration_seconds }}s
                  </template>
                </span>
              </div>
              <div class="evidence-row">
                <span class="evidence-key">质量</span>
                <span>{{ report.result_json.quality?.overall_status ?? "—" }}<template v-if="report.result_json.quality?.issues?.length"> · {{ report.result_json.quality.issues.join("、") }}</template></span>
              </div>
              <div class="evidence-row">
                <span class="evidence-key">识别引擎</span>
                <span class="mono">{{ engineLabel }}</span>
              </div>
              <div class="evidence-rows">
                <span class="evidence-key">OCR 原始行</span>
                <div class="ocr-rows">
                  <span v-for="(row, index) in report.result_json.time.overlay_time_samples[0]?.ocr_rows ?? []" :key="`${row.text}-${index}`" class="ocr-row">
                    <span class="ocr-region">{{ row.region ?? "—" }}</span>
                    <span class="ocr-text mono">{{ row.text }}</span>
                    <span class="num ocr-conf">{{ (row.confidence * 100).toFixed(1) }}%</span>
                  </span>
                  <span v-if="!(report.result_json.time.overlay_time_samples[0]?.ocr_rows ?? []).length" class="dim">没有可用的 OCR 行</span>
                </div>
              </div>
            </div>
            <p v-else-if="!inspecting" class="ae-placeholder">选择媒体后点「开始识别」，识别结果只做候选，保存前需要人工确认。</p>
          </template>
        </div>
      </section>

      <!-- 右：确认表单 -->
      <aside class="ae-right scroll-y">
        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">画面角标时间</span>
            <StatusChip :label="timeUnknown ? '已标记未知' : timeStatus.label" :tone="timeUnknown ? 'muted' : timeStatus.tone" />
          </div>

          <div v-if="timeList.length && !timeUnknown" class="candidates">
            <button
              v-for="(item, index) in timeList"
              :key="`t-${index}`"
              class="candidate-opt"
              :class="{ 'is-on': timeIndex === index }"
              @click="pickTime(index)"
            >
              <span class="mono">{{ item.text }}</span>
              <span class="num dim">{{ (item.confidence * 100).toFixed(1) }}%</span>
              <span v-if="item.date && item.time" class="dim">→ {{ item.date }} {{ item.time }}</span>
            </button>
          </div>
          <p v-else-if="report && !timeUnknown" class="field-hint warn">未识别到可用时间，请手动填写或标记未知。</p>

          <div class="time-input">
            <input v-model="date" class="input num" type="date" :disabled="timeUnknown" aria-label="日期" />
            <input v-model="timeLabel" class="input num" type="text" placeholder="HH:MM:SS" :disabled="timeUnknown" aria-label="时分秒" />
            <button class="chip-btn" :class="{ 'is-on': timeUnknown }" @click="timeUnknown ? (timeUnknown = false) : markTimeUnknown()">
              {{ timeUnknown ? "已标未知" : "标记未知" }}
            </button>
          </div>
          <p class="field-hint">OCR 原值保留在识别依据里；人工修改只影响本要素的入库时间。</p>
        </div>

        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">摄像头名称</span>
            <StatusChip :label="cameraStatus.label" :tone="cameraStatus.tone" />
          </div>

          <div v-if="nameList.length" class="candidates">
            <button
              v-for="(item, index) in nameList"
              :key="`n-${index}`"
              class="candidate-opt"
              :class="{ 'is-on': nameIndex === index }"
              @click="pickName(index)"
            >
              <span class="truncate">{{ item.name }}</span>
              <span class="dim">{{ item.source }}</span>
            </button>
          </div>
          <p v-else-if="report" class="field-hint warn">
            结构化解析没有得到名称，可在下方手动填写，或对照「识别依据」里的 OCR 行挑选。
          </p>

          <div class="name-input">
            <input
              v-model="cameraNameInput"
              class="input"
              type="text"
              placeholder="角标名称（可手动填写或修改）"
              aria-label="摄像头名称"
            />
            <button class="chip-btn" :class="{ 'is-on': !cameraName }" @click="markUnlocated">仅确定区域</button>
          </div>

          <div v-if="nameSuggestions.length" class="catalog-hits">
            <span class="field-hint">相似建议：</span>
            <button
              v-for="item in nameSuggestions"
              :key="item.camera_uuid"
              class="tag suggest"
              :title="`OCR：${item.ocr_text} · 相似度 ${Math.round(item.score * 100)}%${item.reviewed === false ? ' · Map 台账未审核（名称只是台账写法）' : ''}`"
              @click="applySuggestion(item)"
            >
              {{ item.display_name }}<span class="dim"> {{ Math.round(item.score * 100) }}%</span>
              <span v-if="item.reviewed === false" class="dim">·台账未审核</span>
            </button>
          </div>
          <p v-if="nameSuggestions.length" class="field-hint">
            相似建议只填名称、不建立绑定；确认真实镜头请人工核对后再绑定。
          </p>

          <div v-if="catalogMatches.length" class="catalog-hits">
            <span class="field-hint">镜头目录匹配：</span>
            <span v-for="camera in catalogMatches" :key="camera.camera_uuid" class="tag">
              {{ camera.display_name }}
            </span>
          </div>
          <p v-else-if="matchHint" class="field-hint">{{ matchHint }}</p>

          <div class="source-actions">
            <span class="grow" />
            <span class="dim field-hint">名称匹配度 ≠ 同一目标的身份结论</span>
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
            <span class="field-label">可观察属性（选填）</span>
            <span class="field-hint">保存后进入待核查</span>
          </div>
          <div v-for="attr in attributes" :key="attr.key" class="attr-row">
            <span class="attr-key">{{ attr.key }}</span>
            <input v-model="attr.value" class="input" :disabled="attr.unknown" :placeholder="attr.unknown ? '未知' : '可观察描述'" />
            <button class="chip-btn" :class="{ 'is-on': attr.unknown }" @click="attr.unknown = !attr.unknown">{{ attr.unknown ? "未知" : "标未知" }}</button>
          </div>
          <p class="field-hint">属性需人工观察填写，系统不做自动检测与推测。</p>
        </div>

        <div class="ae-section">
          <span class="field-label">备注 / 来源说明</span>
          <textarea v-model="note" class="textarea" rows="2" placeholder="例如：角标时间与文件名交叉核对；画面低照度" />
        </div>
      </aside>
    </div>

    <template #footer>
      <p class="ae-foot-note">保存后不会自动启动或扩大研判；未核验属性仅作为特征候选，不会覆盖已核验特征。</p>
      <span class="grow" />
      <button class="btn btn--ghost" @click="ui.addElementOpen = false">取消</button>
      <button v-if="!editingElement" class="btn btn--outline" @click="save(true)">保存并继续添加</button>
      <button class="btn btn--primary" @click="save(false)">
        {{ editingElement ? "保存修改" : `保存为目标要素 ${nextElementId}` }}
      </button>
    </template>
  </BaseModal>
</template>

<style scoped>
.ae {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  min-height: 0;
  flex: 1;
}

.ae-left {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  min-height: 0;
  padding: 10px 12px 12px;
  border-right: 1px solid var(--line);
  overflow: hidden;
}

.ae-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.ae-block-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-0);
}

.ae-block-hint {
  font-size: 11px;
  color: var(--text-3);
}

.ae-run {
  margin-left: auto;
  flex: none;
}

.ae-search {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 1 200px;
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: var(--bg-2);
  color: var(--text-3);
}

.ae-search input {
  width: 100%;
  border: 0;
  background: none;
  color: var(--text-1);
  font-size: 11.5px;
  outline: none;
}

.ae-select {
  flex: 1 1 auto;
  min-width: 0;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: var(--bg-2);
  color: var(--text-1);
  font-size: 11.5px;
}

.ae-evidence {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: none;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--bg-1);
  overflow: hidden;
}

.ae-evidence.is-open {
  max-height: 210px;
  overflow: auto;
}

.ae-evidence-summary {
  font-size: 11px;
  color: var(--text-3);
}

.ae-evidence-toggle {
  margin-left: auto;
  flex: none;
}

.ae-evidence-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.preview {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1 1 auto;
  width: 100%;
  min-height: 150px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #05070a;
  overflow: hidden;
}

.preview img,
.preview video {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.preview-empty {
  font-size: 11.5px;
  color: var(--text-3);
}

.preview-tag {
  position: absolute;
  left: 6px;
  top: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.12);
  font-size: 9.5px;
  color: #a9b6c6;
}

.ae-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ae-progress-bar {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}

.ae-progress-bar i {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 0.3s var(--ease);
}

.evidence {
  display: grid;
  gap: 6px;
  font-size: 11px;
  color: var(--text-2);
}

.evidence-row {
  display: flex;
  gap: 8px;
}

.evidence-key {
  flex: none;
  width: 64px;
  color: var(--text-3);
}

.evidence-rows {
  display: flex;
  gap: 8px;
}

.ocr-rows {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.ocr-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ocr-region {
  flex: none;
  width: 54px;
  font-size: 10px;
  color: var(--text-4);
}

.ocr-text {
  color: var(--text-1);
}

.ocr-conf {
  color: var(--text-3);
  font-size: 10px;
}

.ae-error {
  font-size: 11.5px;
  color: #ffb0ab;
  line-height: 1.5;
}

.ae-placeholder {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-3);
}

/* -------- 右侧表单 -------- */

.ae-right {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  min-height: 0;
}

.ae-section {
  display: grid;
  gap: 7px;
}

.ae-field-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.field-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-0);
}

.field-hint {
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-3);
}

.field-hint.warn {
  color: var(--warn);
}

.candidates {
  display: grid;
  gap: 4px;
}

.candidate-opt {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg-2);
  font-size: 11.5px;
  color: var(--text-1);
  text-align: left;
}

.candidate-opt:hover {
  border-color: var(--line-3);
}

.candidate-opt.is-on {
  border-color: var(--accent-line);
  background: var(--accent-soft);
}

.candidate-opt .dim {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--text-3);
}

.time-input {
  display: flex;
  align-items: center;
  gap: 6px;
}

.time-input .input {
  height: 30px;
  font-size: 12px;
}

.time-input .input[type="date"] {
  flex: none;
  width: 132px;
}

.time-input .input[type="text"] {
  flex: 1;
  min-width: 0;
}

.chip-btn {
  flex: none;
  height: 26px;
  padding: 0 9px;
  border: 1px solid var(--line-2);
  border-radius: 5px;
  font-size: 11px;
  color: var(--text-2);
}

.chip-btn.is-on {
  border-color: var(--warn);
  background: rgba(240, 180, 90, 0.14);
  color: #f6c98a;
}

.name-input {
  display: flex;
  align-items: center;
  gap: 6px;
}

.name-input .input {
  flex: 1;
  min-width: 0;
  height: 30px;
  font-size: 12px;
}

.catalog-hits {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.tag {
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid rgba(47, 207, 146, 0.4);
  background: rgba(47, 207, 146, 0.12);
  color: #7fe3bb;
  font-size: 10.5px;
}

/* 相似建议是可点的，和只读的目录匹配标签区分开 */
.tag.suggest {
  cursor: pointer;
  font-family: inherit;
  border-color: rgba(120, 170, 255, 0.42);
  background: rgba(90, 140, 255, 0.14);
  color: #a9c8ff;
}
.tag.suggest:hover {
  background: rgba(90, 140, 255, 0.24);
}
.tag.suggest .dim {
  color: #7f9fc9;
}

.source-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.source-actions .btn.is-on {
  border-color: var(--accent-line);
  color: var(--accent-hi);
}

.seg {
  display: flex;
  gap: 4px;
  padding: 3px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--bg-2);
}

.seg button {
  flex: 1;
  height: 26px;
  border-radius: 4px;
  font-size: 11.5px;
  color: var(--text-2);
}

.seg button.is-on {
  background: var(--accent-soft);
  color: var(--accent-hi);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.attr-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.attr-key {
  flex: none;
  width: 48px;
  font-size: 11.5px;
  color: var(--text-2);
}

.attr-row .input {
  flex: 1;
  height: 28px;
  min-width: 0;
  font-size: 11.5px;
}

.textarea {
  width: 100%;
  min-height: 54px;
  font-size: 11.5px;
  line-height: 1.5;
}

.grow {
  flex: 1;
}

.ae-foot-note {
  font-size: 10.5px;
  color: var(--text-3);
  line-height: 1.5;
}
</style>
