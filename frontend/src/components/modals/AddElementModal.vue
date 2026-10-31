<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import StatusChip from "../ui/StatusChip.vue";
import { actions, currentCase, elementList, pendingElementFile, toast, ui, type ElementPayload } from "../../store/workbench";
import {
  CAMERA_STATUS_TEXT,
  TIME_STATUS_TEXT,
  cameraNameCandidates,
  mainApi,
  overlayIdentity,
  runInspect,
  timeCandidates,
  type CameraCatalogEntry,
  type InspectionReport,
  type MediaItem,
  type M4Observation,
} from "../../services/mainApi";

/** 新建要素的默认日期 = 本机今天（不再写死演示日期） */
const DEFAULT_EVENT_DATE = (() => {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
})();

/* ------------------------------ 截图（添加要素只收截图） ------------------------------ */

/** 本次添加要素上传的截图；不再从媒体库挑多个素材，一张图走一次识别 */
const selectedMedia = ref<MediaItem | null>(null);
const selectedMediaId = computed(() => selectedMedia.value?.id ?? null);

/* M4a: human-selected evidence region. No automatic detector/identity claim. */
const m4Image = ref<HTMLImageElement | null>(null);
const m4Box = ref<[number, number, number, number] | null>(null);
const m4Anchor = ref<[number, number] | null>(null);
const m4Observation = ref<M4Observation | null>(null);
const m4Busy = ref(false);
const m4Error = ref<string | null>(null);
const m4Authorized = ref(false);
const m4Note = ref("");

/* The pointer surface must match the IMAGE content, not the dark letterbox. */
type M4Layout = { left: number; top: number; width: number; height: number };
const m4Layout = ref<M4Layout | null>(null);
let m4ResizeObserver: ResizeObserver | null = null;
function syncM4Layout() {
  const img = m4Image.value;
  const parent = img?.parentElement;
  if (!img || !parent || !img.naturalWidth || !img.naturalHeight) { m4Layout.value = null; return; }
  const rect = img.getBoundingClientRect();
  const frame = parent.getBoundingClientRect();
  if (!rect.width || !rect.height) { m4Layout.value = null; return; }
  const scale = Math.min(rect.width / img.naturalWidth, rect.height / img.naturalHeight);
  const width = img.naturalWidth * scale, height = img.naturalHeight * scale;
  m4Layout.value = {
    left: rect.left - frame.left + (rect.width - width) / 2,
    top: rect.top - frame.top + (rect.height - height) / 2, width, height,
  };
}
watch(m4Image, async (img) => {
  m4ResizeObserver?.disconnect(); m4ResizeObserver = null;
  m4Layout.value = null;
  if (!img) return;
  m4ResizeObserver = new ResizeObserver(syncM4Layout);
  m4ResizeObserver.observe(img);
  if (img.parentElement) m4ResizeObserver.observe(img.parentElement);
  await nextTick(); syncM4Layout();
});
onUnmounted(() => { m4ResizeObserver?.disconnect(); m4Session += 1; });
const m4OverlayStyle = computed(() => m4Layout.value ? ({
  left: m4Layout.value.left + 'px', top: m4Layout.value.top + 'px',
  width: m4Layout.value.width + 'px', height: m4Layout.value.height + 'px',
}) : {});
const m4BoxStyle = computed(() => {
  const b = m4Box.value;
  if (!b) return {};
  return { left: (b[0] * 100) + '%', top: (b[1] * 100) + '%',
    width: ((b[2] - b[0]) * 100) + '%', height: ((b[3] - b[1]) * 100) + '%' };
});
function m4Coords(event: PointerEvent, strict: boolean): [number, number] | null {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return null;
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  if (strict && (x < 0 || x > 1 || y < 0 || y > 1)) return null;
  return [Math.max(0, Math.min(1, x)), Math.max(0, Math.min(1, y))];
}
function m4PointerDown(event: PointerEvent) {
  if (event.button !== 0 || !selectedMedia.value || m4Busy.value) return;
  const point = m4Coords(event, true);
  if (!point) return;
  m4Anchor.value = point;
  m4Box.value = [point[0], point[1], point[0], point[1]];
  m4Observation.value = null;
  m4Error.value = null;
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function m4PointerMove(event: PointerEvent) {
  if (!m4Anchor.value) return;
  const end = m4Coords(event, false);
  if (!end) return;
  const [x, y] = m4Anchor.value;
  m4Box.value = [Math.min(x, end[0]), Math.min(y, end[1]),
                 Math.max(x, end[0]), Math.max(y, end[1])];
}
function m4PointerUp(event: PointerEvent) {
  if (!m4Anchor.value) return;
  m4PointerMove(event); m4Anchor.value = null;
  if (m4Box.value && (m4Box.value[2] - m4Box.value[0] < .015 ||
      m4Box.value[3] - m4Box.value[1] < .015)) m4Box.value = null;
}
function m4PointerCancel() { m4Anchor.value = null; m4Box.value = null; }
function m4MatchesStored(): boolean {
  const b = m4Box.value, record = m4Observation.value;
  const original = record?.analysis_json.bbox_normalized;
  return !!b && !!record && record.media_id === selectedMediaId.value &&
    !!original && original.length === 4 &&
    original.every((value, index) => Math.abs(value - b[index]) < 0.000001);
}
const m4History = ref<M4Observation[]>([]);
const m4HistoryBusy = ref(false);
const m4HistoryMore = ref(false);
const m4HistoryError = ref<string | null>(null);
const m4Restoring = ref(false);
const m4PageSize = 25;
let m4Session = 0;

/** The database history remains available after a browser refresh. */
async function loadM4History(reset = false) {
  const caseId = currentCase.value?.id;
  if (!caseId || (m4HistoryBusy.value && !reset)) return;
  if (reset) { m4History.value = []; m4HistoryMore.value = false; }
  const session = m4Session;
  const offset = m4History.value.length;
  m4HistoryBusy.value = true; m4HistoryError.value = null;
  try {
    const records = await mainApi.listCaseM4Observations(caseId, m4PageSize, offset);
    if (session !== m4Session || currentCase.value?.id !== caseId || !ui.addElementOpen) return;
    const known = new Set(m4History.value.map(r => r.id));
    m4History.value = [...m4History.value, ...records.filter(r => !known.has(r.id))];
    m4HistoryMore.value = records.length === m4PageSize;
  } catch (error) {
    if (session === m4Session) m4HistoryError.value = error instanceof Error ? error.message : String(error);
  } finally {
    if (session === m4Session) m4HistoryBusy.value = false;
  }
}

/** Recover the original MediaAsset and the exact saved ROI, never re-run OCR. */
async function reopenM4Record(id: string) {
  const caseId = currentCase.value?.id;
  if (!caseId) return;
  const session = m4Session;
  m4Restoring.value = true; m4Error.value = null;
  try {
    const record = await mainApi.getM4Observation(id);
    if (record.case_id !== caseId) throw new Error('目标观测不属于当前案件');
    const source = await mainApi.getMedia(record.media_id);
    if (source.id !== record.media_id || source.case_id !== caseId || source.kind !== 'image') {
      throw new Error('原始截图与观测的案件归属不一致');
    }
    if (session !== m4Session || currentCase.value?.id !== caseId || !ui.addElementOpen) return;
    const b = record.analysis_json.bbox_normalized;
    if (!Array.isArray(b) || b.length !== 4 || b.some(v => !Number.isFinite(v)) ||
        !(0 <= b[0] && b[0] < b[2] && b[2] <= 1 &&
          0 <= b[1] && b[1] < b[3] && b[3] <= 1)) throw new Error('观测的框选坐标无效');
    m4Layout.value = null;
    selectedMedia.value = source;
    m4Box.value = [b[0], b[1], b[2], b[3]];
    m4Observation.value = record;
    m4Note.value = record.review_note;
    m4Authorized.value = false;
    category.value = record.object_class;
    report.value = null;
    recognizeError.value = null;
  } catch (error) {
    if (session === m4Session) m4Error.value = error instanceof Error ? error.message : String(error);
  } finally { if (session === m4Session) m4Restoring.value = false; }
}
async function analyzeM4Selection() {
  if (!selectedMediaId.value || !m4Box.value || m4Busy.value) {
    m4Error.value = "请先在截图中拖拽框选目标区域";
    return;
  }
  if (!m4Authorized.value || m4Note.value.trim().length < 8) {
    m4Error.value = "需要确认授权并填写至少 8 个字符的选取依据";
    return;
  }
  m4Busy.value = true; m4Error.value = null;
  try {
    const mediaId = selectedMediaId.value;
    const caseId = currentCase.value?.id;
    const session = m4Session;
    const saved = await mainApi.addM4Observation(mediaId, {
      object_class: category.value, bbox_normalized: [...m4Box.value],
      review_note: m4Note.value.trim(), authorized: m4Authorized.value,
    });
    if (session === m4Session && currentCase.value?.id === caseId && selectedMediaId.value === mediaId) {
      m4Observation.value = saved;
      toast('M4a 区域观测已持久保存；不确认身份或地理位置', 'ok');
      void loadM4History(true);
    }
  } catch (error) {
    m4Error.value = error instanceof Error ? error.message : String(error);
  } finally { m4Busy.value = false; }
}

const mediaLoading = ref(false);
const mediaError = ref<string | null>(null);

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

/** 选图 → 上传到当前案件 → 自动识别画面角标时间与摄像头名称 */
async function upload(file: File) {
  if (!currentCase.value) {
    toast("请先在「案件」中新建或选择案件，截图才会归属案件", "warn");
    return;
  }
  if (!file.type.startsWith("image/")) {
    toast("添加要素只接受截图（图片）；录像请在「录像资源」里添加目录", "warn");
    return;
  }
  mediaLoading.value = true;
  mediaError.value = null;
  recognizeError.value = null;
  report.value = null;
  m4Box.value = null; m4Observation.value = null; m4Error.value = null;
  m4Authorized.value = false; m4Note.value = '';
  try {
    const media = await mainApi.uploadMedia(file, currentCase.value.id);
    selectedMedia.value = media;
    toast(`已上传 ${media.original_name}，正在识别画面角标`, "info");
    await recognize();
  } catch (error) {
    mediaError.value = error instanceof Error ? error.message : String(error);
  } finally {
    mediaLoading.value = false;
  }
}

function uploadFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (file) void upload(file);
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
/** 与录像资源扫描 / 视频命名工具同一份结论：读到什么、从哪儿读到的、还缺什么 */
const identity = computed(() => (report.value ? overlayIdentity(report.value.result_json) : null));
/** 时间来自文件名兜底时必须显式说明，不能让人以为角标确认过 */
const identityNotes = computed(() => {
  const value = identity.value;
  if (!value) return [] as string[];
  const notes: string[] = [...value.warnings];
  if (value.time_source === "filename") notes.push("拍摄时间来自文件名兜底，未由画面角标确认");
  return [...new Set(notes)];
});
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
  const normalized = overlayIdentity(result.result_json);
  const best = timeCandidates(result.result_json)[0];
  if (best) {
    timeUnknown.value = false;
    date.value = best.date ?? DEFAULT_EVENT_DATE;
    timeLabel.value = best.time ?? "";
    timeIndex.value = 0;
  } else if (normalized.capture_time) {
    // 角标读不到时间、但文件名里有时间戳：按后端同一口径兜底填入，界面标「文件名兜底」
    timeUnknown.value = false;
    date.value = normalized.capture_time.slice(0, 10);
    timeLabel.value = normalized.capture_time.slice(11, 16);
    timeIndex.value = -1;
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
const date = ref(DEFAULT_EVENT_DATE);
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

const editingElement = computed(() =>
  ui.editingElementId ? elementList.value.find((item) => item.id === ui.editingElementId) ?? null : null,
);

function resetForm() {
  timeIndex.value = -1;
  nameIndex.value = -1;
  selectedMedia.value = null;
  m4Box.value = null; m4Observation.value = null; m4Authorized.value = false;
  m4Anchor.value = null; m4Note.value = ""; m4Error.value = null;
  mediaError.value = null;
  date.value = DEFAULT_EVENT_DATE;
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
  date.value = element.time.slice(0, 10) || DEFAULT_EVENT_DATE;
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
    m4Session += 1;
    resetForm();
    fillFromElement();
    void loadM4History(true);
    const linkedObservationId = editingElement.value?.m4ObservationId;
    if (linkedObservationId) void reopenM4Record(linkedObservationId);
    void mainApi
      .listCameras()
      .then((catalogPage) => {
        catalog.value = catalogPage.cameras ?? [];
        if (cameraName.value) syncCatalogMatch();
      })
      .catch(() => {
        catalog.value = [];
      });
    // 「添加要素」已经先弹过系统选图框：拿到文件后进弹窗就直接上传+识别
    const pending = pendingElementFile.value;
    if (pending) {
      pendingElementFile.value = null;
      void upload(pending);
    }
  },
);

function save(keepOpen: boolean) {
  // A selected ROI must have a durable, matching observation record.
  // Never place the synthetic "manual-unsaved" marker on the task timeline.
  if (m4Box.value && !m4MatchesStored()) {
    toast("请先点击「保存区域观测」，再保存目标要素；未写库的框选不能进入轨迹", "warn");
    return;
  }
  if (m4Observation.value && selectedMedia.value?.case_id !== currentCase.value?.id) {
    toast("当前截图不属于所选案件，请重新打开对应案件", "danger");
    return;
  }
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
    sourceMediaId: m4MatchesStored() ? selectedMediaId.value : (editingElement.value?.sourceMediaId ?? null),
    m4ObservationId: m4MatchesStored() ? m4Observation.value?.id : (editingElement.value?.m4ObservationId ?? null),
    boxes: m4MatchesStored() && m4Box.value ? [{
      id: m4Observation.value!.id,
      x: m4Box.value[0], y: m4Box.value[1],
      w: m4Box.value[2] - m4Box.value[0],
      h: m4Box.value[3] - m4Box.value[1],
      kind: category.value === "vehicle" ? "vehicle" : "person",
      label: "人工截图框选", auto: false,
    }] : (editingElement.value?.boxes ?? []),
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
    :title="editingElement ? '编辑目标要素' : '添加目标要素'"
    subtitle="添加要素只接受截图：选图后自动识别画面角标时间与摄像头名称，人工确认后再保存；人体 / 人脸 / 车辆识别尚未接入。"
    width="xl"
    height="92vh"
    @close="ui.addElementOpen = false"
  >
    <div class="ae">
      <!-- 左：截图与识别依据 -->
      <section class="ae-left">
        <div class="ae-bar">
          <label v-if="currentCase" class="btn btn--sm btn--primary">
            <AppIcon name="plus" :size="12" />选择截图并识别
            <input type="file" accept="image/png,image/jpeg,image/webp" hidden @change="uploadFile" />
          </label>
          <button v-else class="btn btn--sm btn--primary" disabled title="请先在「案件」中新建或选择案件">
            <AppIcon name="plus" :size="12" />选择截图并识别
          </button>
          <span v-if="mediaLoading" class="ae-progress">
            <span class="ae-progress-bar"><i :style="{ width: `${jobProgress}%` }" /></span>
            <span class="num dim">{{ jobProgress }}%</span>
          </span>
          <span class="grow" />
          <span class="ae-block-hint">{{ mediaLoading ? "上传并识别中…" : "只接受截图；选图后自动识别角标，不再从媒体库挑多要素" }}</span>
        </div>

        <div class="ae-scope" :class="{ 'is-empty': !currentCase }">
          <AppIcon name="folder" :size="12" />
          <template v-if="currentCase">
            <span class="ae-scope-text">
              当前案件 <b>{{ currentCase.name }}</b><span class="mono dim"> · {{ currentCase.code }}</span>
            </span>
            <span class="grow" />
            <span class="ae-scope-note">上传的要素文件归属该案件；删除案件只清理这些文件</span>
          </template>
          <template v-else>
            <span class="ae-scope-text">未选择案件</span>
            <span class="grow" />
            <span class="ae-scope-note">未选择案件：先新建或选择案件，截图才会归属该案件</span>
            <button class="chip-btn" @click="actions.openCases()">选择案件</button>
          </template>
        </div>

        <div class="preview">
          <template v-if="selectedMedia">
            <img
              ref="m4Image"
              :src="mainApi.mediaContentUrl(selectedMedia.id)"
              @load="syncM4Layout"
              :alt="selectedMedia.original_name"
            />
            <div v-if="m4Layout" class="m4a-select-overlay" :style="m4OverlayStyle"
              title="在原始截图中拖拽框选观察对象"
              @pointerdown.prevent="m4PointerDown"
              @pointermove.prevent="m4PointerMove"
              @pointerup.prevent="m4PointerUp"
              @pointercancel="m4PointerCancel">
              <div v-if="m4Box" class="m4a-selected-box" :style="m4BoxStyle"></div>
            </div>
            <span class="preview-tag">只读原始画面 · 拖拽框选目标区域（M4a）</span>
          </template>
          <p v-else class="preview-empty">选择一张截图，自动识别画面角标时间与摄像头名称</p>
        </div>

        <div v-if="selectedMedia" class="m4a-panel">
          <div class="m4a-panel-title">M4a · 目标区域画质与外观线索（人工框选）</div>
          <p>拖动鼠标框选目标。统计结果可能混有背景，不能代表目标身份、衣物或地理坐标。</p>
          <div class="m4a-panel-form">
            <input v-model="m4Note" class="input" placeholder="选取依据（至少 8 字）" maxlength="500" />
            <label><input v-model="m4Authorized" type="checkbox" /> 本案件素材已获授权处理</label>
            <button class="btn btn--sm btn--primary" type="button"
              :disabled="m4Busy || !m4Box" @click="analyzeM4Selection">
              {{ m4Busy ? "分析中…" : "保存区域观测" }}
            </button>
          </div>
          <p v-if="m4Error" class="ae-error">{{ m4Error }}</p>
          <div v-if="m4Observation" class="m4a-result">
            <span>观测已保存 · {{ m4Observation.review_state }} · ROI
              {{ m4Observation.analysis_json.bbox_pixels.join(", ") }}</span>
            <span>亮度 {{ m4Observation.analysis_json.quality.median_luminance_0_255 }}
              · 拉普拉斯方差 {{ m4Observation.analysis_json.quality.laplacian_variance }}</span>
            <span>画质提示：{{ m4Observation.analysis_json.quality.flags.join("、") || "未触发规则告警" }}</span>
            <span>ROI 粗颜色统计：{{ m4Observation.analysis_json.appearance.roi_tones_unverified
              .map(v => v.tone + " " + Math.round(v.fraction * 100) + "%").join(" / ") }}</span>
            <span>摄像头、地理位置、身份关联：未建立；不自动填入语义属性。</span>
          </div>
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
              <div v-if="identity" class="evidence-row">
                <span class="evidence-key">归一结论</span>
                <span>
                  时间 {{ identity.capture_time ?? "未识别" }}
                  <template v-if="identity.time_source === 'filename'">（文件名兜底）</template>
                  · 名称 {{ identity.camera_name ?? "未识别" }}
                  <template v-if="identity.names.length > 1"> · 候选 {{ identity.names.length }} 个</template>
                </span>
              </div>
              <div v-if="identityNotes.length" class="evidence-row">
                <span class="evidence-key">提示</span>
                <span class="dim">{{ identityNotes.join("；") }}</span>
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
            <p v-else-if="!inspecting" class="ae-placeholder">选择截图后会自动识别；识别结果只做候选，保存前需要人工确认。</p>
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
        {{ editingElement ? "保存修改" : "保存为目标要素" }}
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

/* 案件上下文：上传的要素文件归属当前案件，决定删除案件时清理哪些文件 */
.ae-scope {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
  min-height: 24px;
  padding: 2px 9px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--bg-1);
  font-size: 10.5px;
  color: var(--text-2);
}

.ae-scope :deep(svg) {
  flex: none;
  color: var(--ok);
}

.ae-scope.is-empty {
  border-color: rgba(240, 180, 90, 0.32);
  background: var(--warn-soft);
}

.ae-scope.is-empty :deep(svg) {
  color: var(--warn);
}

.ae-scope-text {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  flex: none;
}

.ae-scope-text b {
  color: var(--text-0);
  font-weight: 600;
}

.ae-scope-note {
  font-size: 10px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ae-scope .chip-btn {
  height: 19px;
  font-size: 10px;
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


.m4a-select-overlay { position: absolute; cursor: crosshair;
  touch-action: none; z-index: 2; user-select: none; }
.m4a-selected-box { position: absolute; border: 2px solid #54cfff;
  box-shadow: 0 0 0 9999px rgba(0,0,0,.12); pointer-events: none;
  background: rgba(84,207,255,.11); }
.m4a-panel { border: 1px solid var(--line-2); border-radius: 6px; padding: 9px;
  display: grid; gap: 6px; background: var(--bg-1); font-size: 11px; }
.m4a-panel-title { font-size: 12px; font-weight: 600; color: var(--text-1); }
.m4a-panel p { margin: 0; color: var(--text-3); }
.m4a-panel-form { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.m4a-panel-form .input { flex: 1 1 190px; height: 27px; }
.m4a-panel-form label { white-space: nowrap; }
.m4a-result { display: grid; gap: 3px; padding: 7px; border-radius: 5px;
  border: 1px solid rgba(47,207,146,.3); color: var(--text-2); }
.preview-tag { z-index: 3; pointer-events: none;
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
