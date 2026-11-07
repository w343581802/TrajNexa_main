<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import StatusChip from "../ui/StatusChip.vue";
import { actions, currentCase, elementGroupIds, elementList, pendingElementFile, toast, ui, type ElementPayload } from "../../store/workbench";
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
  type M4DetectCandidate,
  type M4DetectEngine,
  type M4RoiFeatures,
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
/* B8-B0：同一帧的关联对象（如骑行者旁边的两轮车），独立 ROI，不合并主框 */
const assocProposal = ref<M4DetectCandidate | null>(null);
const assocBox = ref<[number, number, number, number] | null>(null);
const assocObservation = ref<M4Observation | null>(null);
const assocCategory = ref<"person" | "vehicle" | "other">("vehicle");
const assocMode = ref(false);
const assocRelation = ref("riding");
const assocBoxStyle = computed(() => {
  const b = assocBox.value;
  return b ? m4CandidateStyle(b) : {};
});
const assocOptions = computed(() => m4Proposals.value.filter((p) => p.bbox_normalized));
const assocFeatures = ref<M4RoiFeatures | null>(null);
const assocFeaturesBusy = ref(false);
async function loadAssocFeatures() {
  const mediaId = selectedMediaId.value;
  const box = assocBox.value;
  if (!mediaId || !box) return;
  assocFeaturesBusy.value = true; assocFeatures.value = null;
  try {
    assocFeatures.value = await mainApi.analyzeM4Roi(mediaId, [...box], m4Engine, assocCategory.value);
    autoFillFromFeatures(assocFeatures.value, assocAttrs.value);
  } catch { assocFeatures.value = null; }
  finally { assocFeaturesBusy.value = false; }
}

function pickAssocProposal(proposal: M4DetectCandidate | null) {
  assocProposal.value = proposal;
  assocBox.value = proposal ? [...proposal.bbox_normalized] : null;
  assocObservation.value = null;
  if (proposal) void loadAssocFeatures();
  assocCategory.value = proposal && /车|vehicle|car|truck|bus|motor/i.test(proposal.detector_label)
    ? "vehicle" : "person";
}
function swapPrimaryAndAssoc() {
  if (!assocBox.value) return;
  const box = m4Box.value, obs = m4Observation.value, cat = category.value;
  m4Box.value = assocBox.value; m4Observation.value = assocObservation.value; category.value = assocCategory.value;
  assocBox.value = box; assocObservation.value = obs; assocCategory.value = cat;
  assocProposal.value = null;
}
function clearAssoc() { pickAssocProposal(null); assocMode.value = false; }
const m4Busy = ref(false);
const m4Error = ref<string | null>(null);
/** Business mode always invokes the SERVER-side default, never lab choices. */
const m4Engine: M4DetectEngine = "auto";
/** 选中框之后的关键特征（画质/主色 + 视觉模型外观短语） */
const m4Features = ref<M4RoiFeatures | null>(null);
const m4FeaturesBusy = ref(false);
const m4FeaturesError = ref<string | null>(null);
let m4FeaturesRequest = 0;
const m4Note = ref("");

/* The pointer surface must match the IMAGE content, not the dark letterbox. */
type M4Layout = { left: number; top: number; width: number; height: number };
const m4Layout = ref<M4Layout | null>(null);
let m4ResizeObserver: ResizeObserver | null = null;
function syncM4Layout() {
  const img = m4Image.value;
  const parent = img?.parentElement;
  if (!img || !parent || !img.naturalWidth || !img.naturalHeight) { m4Layout.value = null; return; }
  const frameWidth = parent.clientWidth, frameHeight = parent.clientHeight;
  if (!frameWidth || !frameHeight) { m4Layout.value = null; return; }
  const scale = Math.min(frameWidth / img.naturalWidth, frameHeight / img.naturalHeight);
  const width = img.naturalWidth * scale, height = img.naturalHeight * scale;
  m4Layout.value = { left: (frameWidth - width) / 2, top: (frameHeight - height) / 2, width, height };
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
onUnmounted(() => { m4ResizeObserver?.disconnect(); m4Session += 1; window.removeEventListener("keydown", onEscape); });
onMounted(() => window.addEventListener("keydown", onEscape));
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
/** 主色统计的展示文案：模板里不写带类型注解的箭头函数（Vue 模板表达式不支持） */
function m4ToneText(tones: { tone: string; fraction: number }[]): string {
  return tones.map(v => v.tone + " " + Math.round(v.fraction * 100) + "%").join(" / ") || "无法判定";
}
function m4CandidateStyle(box: readonly number[]): Record<string, string> {  const x1 = Math.min(box[0], box[2]), y1 = Math.min(box[1], box[3]);
  const x2 = Math.max(box[0], box[2]), y2 = Math.max(box[1], box[3]);
  return {
    left: (x1 * 100) + '%', top: (y1 * 100) + '%',
    width: ((x2 - x1) * 100) + '%', height: ((y2 - y1) * 100) + '%',
  };
}
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
      m4Box.value[3] - m4Box.value[1] < .015)) { m4Box.value = null; return; }
  // 手画框也要立刻出关键特征，和点选模型候选框是同一套反馈
  if (m4Box.value) { void loadM4Features().then(() => autoFillFromFeatures(m4Features.value, primaryAttrs.value)); }
}
function m4PointerCancel() { m4Anchor.value = null; m4Box.value = null; }
function m4MatchesStored(): boolean {
  const b = m4Box.value, record = m4Observation.value;
  const original = record?.analysis_json.bbox_normalized;
  return !!b && !!record && record.media_id === selectedMediaId.value &&
    record.object_class === category.value &&
    !!original && original.length === 4 &&
    original.every((value, index) => Math.abs(value - b[index]) < 0.0001);
}

/** 地面投影使用目标框底部中心，不能使用框中心（框中心通常落在人体/车辆内部）。 */
function groundContactGeometry(): {
  pixel: [number, number] | null;
  frameSize: [number, number] | null;
} {
  const box = m4Box.value;
  const image = m4Image.value;
  if (!box || !image?.naturalWidth || !image.naturalHeight) {
    return { pixel: null, frameSize: null };
  }
  return {
    pixel: [((box[0] + box[2]) / 2) * image.naturalWidth, box[3] * image.naturalHeight],
    frameSize: [image.naturalWidth, image.naturalHeight],
  };
}
const m4History = ref<M4Observation[]>([]);
const m4HistoryBusy = ref(false);
const m4HistoryMore = ref(false);
const m4HistoryError = ref<string | null>(null);
const m4Restoring = ref(false);
const m4PageSize = 25;
let m4Session = 0;
let m4HistoryEpoch = 0;

/** The database history remains available after a browser refresh. */
async function loadM4History(reset = false) {
  const caseId = currentCase.value?.id;
  if (!caseId || (m4HistoryBusy.value && !reset)) return;
  if (reset) { m4HistoryEpoch += 1; m4History.value = []; m4HistoryMore.value = false; }
  const epoch = m4HistoryEpoch;
  const session = m4Session;
  const offset = m4History.value.length;
  m4HistoryBusy.value = true; m4HistoryError.value = null;
  try {
    const records = await mainApi.listCaseM4Observations(caseId, m4PageSize, offset);
    if (session !== m4Session || epoch !== m4HistoryEpoch || currentCase.value?.id !== caseId || !ui.addElementOpen) return;
    const known = new Set(m4History.value.map(r => r.id));
    m4History.value = [...m4History.value, ...records.filter(r => !known.has(r.id))];
    m4HistoryMore.value = records.length === m4PageSize;
  } catch (error) {
    if (session === m4Session && epoch === m4HistoryEpoch) m4HistoryError.value = error instanceof Error ? error.message : String(error);
  } finally {
    if (session === m4Session && epoch === m4HistoryEpoch) m4HistoryBusy.value = false;
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
    m4DetectRequest += 1; m4Proposals.value = [];
    selectedMedia.value = source;
    m4Box.value = [b[0], b[1], b[2], b[3]];
    m4Observation.value = record;
    await nextTick(); syncM4Layout();
    m4Note.value = record.review_note;
    m4Features.value = null;
    category.value = record.object_class;
    report.value = null;
    recognizeError.value = null;
  } catch (error) {
    if (session === m4Session) m4Error.value = error instanceof Error ? error.message : String(error);
  } finally { if (session === m4Session) m4Restoring.value = false; }
}

// M4d: 模型候选框是可丢弃的建议，选中并保存后才成为观测。
const m4Proposals = ref<M4DetectCandidate[]>([]);
const m4DetectBusy = ref(false);
const m4DetectError = ref<string | null>(null);
const m4DetectModel = ref<string | null>(null);
const m4DetectElapsed = ref<number | null>(null);
let m4DetectRequest = 0;
// 在途识别计数：换图/换引擎会让旧请求作废，但「识别中」必须能收回来
let m4DetectInflight = 0;
const m4DetectElapsedLive = ref(0);
let m4ElapsedTimer: ReturnType<typeof setInterval> | null = null;
function startM4Elapsed() {
  m4DetectElapsedLive.value = 0;
  if (m4ElapsedTimer) clearInterval(m4ElapsedTimer);
  m4ElapsedTimer = setInterval(() => { m4DetectElapsedLive.value += 1; }, 1000);
}
function stopM4Elapsed() {
  if (m4ElapsedTimer) { clearInterval(m4ElapsedTimer); m4ElapsedTimer = null; }
}

const M4_ENGINE_TEXT: Record<M4DetectEngine, string> = {
  dfine: "D-FINE",
  qwen3vl: "Qwen3-VL 2B",
  qwen3vl4b: "Qwen3-VL 4B",
  local: "本机检测器",
  vlm: "视觉模型",
  auto: "默认检测",
};

/** 选完图、换引擎都会自动跑一次：框先出来，人再决定用哪一个 */
async function runDetection() {
  const source = selectedMedia.value;
  const caseId = currentCase.value?.id;
  if (!source || !caseId) return;
  const ticket = ++m4DetectRequest;
  const session = m4Session;
  const engine = m4Engine;
  m4DetectInflight += 1;
  m4DetectBusy.value = true; startM4Elapsed();
  m4DetectError.value = null; m4Proposals.value = []; m4DetectModel.value = null;
  m4DetectElapsed.value = null;
  try {
    const response = await mainApi.detectM4(source.id, engine, .4);
    if (ticket !== m4DetectRequest || session !== m4Session ||
        selectedMediaId.value !== source.id || currentCase.value?.id !== caseId ||
        !ui.addElementOpen) return;
    if (response.media_id !== source.id || response.case_id !== caseId ||
        response.source_sha256 !== source.sha256 ||
        response.status !== "candidates_unverified") {
      throw new Error("识别结果来源不一致，已拒绝显示");
    }
    m4Proposals.value = response.candidates;
    m4DetectElapsed.value = response.elapsed_seconds;
    const failedRuns = response.model_runs?.filter(run => run.status === "inference_error") ?? [];
    if (failedRuns.length) {
      m4DetectError.value = `部分 Rebucca 模型没有成功返回结果：${failedRuns.map(run => run.model_id).join("、")}（失败不等于无目标）`;
    }
    m4DetectModel.value = response.model_family +
      (response.model_id ? ` · ${response.model_id}` : "") +
      (response.tiling && response.tiling > 1 ? ` · 切片 ${response.tiling}×${response.tiling}` : "");
    if (!response.candidates.length) {
      if (!m4DetectError.value) m4DetectError.value = "默认检测没有给出候选框，可手动拖拽框选目标。";
    } else if (!m4Box.value && response.candidates.length) {
      // 框一出来就选中第一个：让「系统已经看到目标」这件事立刻可见
      void selectM4Proposal(response.candidates[0]);
    }
  } catch (error) {
    if (ticket === m4DetectRequest && session === m4Session) {
      const message = error instanceof Error ? error.message : String(error);
      m4DetectError.value = message + "（默认检测失败不等于无目标，可手动框选）";
    }
  } finally {
    m4DetectInflight -= 1;
    if (m4DetectInflight <= 0) { m4DetectInflight = 0; m4DetectBusy.value = false; stopM4Elapsed(); }
  }
}

async function selectM4Proposal(proposal: M4DetectCandidate) {
  if (attrTarget.value === "assoc") {
    pickAssocProposal(proposal);
    return;
  }
  const b = proposal.bbox_normalized;
  if (!selectedMedia.value || b.length !== 4 || b.some(v => !Number.isFinite(v)) ||
      !(0 <= b[0] && b[0] < b[2] && b[2] <= 1 &&
        0 <= b[1] && b[1] < b[3] && b[3] <= 1)) {
    m4DetectError.value = "模型候选坐标异常，已拒绝";
    return;
  }
  m4Box.value = [b[0], b[1], b[2], b[3]];
  m4Observation.value = null;
  m4Features.value = null;
  category.value = proposal.object_class === "other" ? "other" : proposal.object_class;
  // 检测器名称/类别只是 provenance，不写进人工备注，更不能当外观特征值。
  m4Error.value = null;
  await loadM4Features();
  autoFillFromFeatures(m4Features.value, primaryAttrs.value);
}

function autoFillFromFeatures(features: M4RoiFeatures | null, list: AttrRow[]) {
  const feats = (features?.appearance.semantic_attributes?.features_unverified ?? [])
    .map((f) => String(f).trim()).filter(Boolean);
  if (!feats.length) return;
  const rules: Record<string, RegExp> = {
    "garment.upper.color": /白|黑|红|蓝|灰|绿|黄|橙|紫|米|棕|粉/,
    "garment.upper.type": /外套|夹克|短袖|长袖|衬衫|t恤|卫衣|制服|背心|马甲/i,
    "garment.lower.color": /白|黑|红|蓝|灰|绿|黄|橙|紫|米|棕|粉/,
    "garment.lower.type": /裤|裙/i,
    "head.appearance": /头盔|帽|发|头部/,
    "footwear": /鞋|靴/,
    "carried.item": /包|伞|手提|携带|袋/,
    "vehicle.class": /两轮|三轮|摩托|电动|自行|轿车|卡车|客车/,
    "vehicle.color": /白|黑|红|蓝|灰|绿|黄|橙|紫|银|深色|浅色/,
    "vehicle.form": /踏板|跨骑|弯梁/,
    "vehicle.attachment": /尾箱|货箱|箱|后视镜|货架|挡风/,
    "vehicle.distinctive": /大灯|图案|贴|标识|灯/,
  };
  const used = new Set<string>();
  for (const row of list) {
    if (row.custom || row.unknown || row.value.trim()) continue;
    const rule = rules[row.attributeKey];
    const hit = rule ? feats.find((f) => !used.has(f) && rule.test(f)) : undefined;
    if (!hit) continue;
    row.value = hit.slice(0, 60);
    row.auto = true;
    used.add(hit);
  }
}
function addCustomAttribute() {
  activeAttrs.value.push({ key: "", attributeKey: "custom", value: "", unknown: false, auto: false, custom: true });
}

/** 选中框后自动出来的关键特征：画质/主色（确定性）+ 外观短语（模型，可降级） */
async function loadM4Features() {
  const mediaId = selectedMediaId.value;
  const box = m4Box.value;
  if (!mediaId || !box) return;
  const session = m4Session;
  const request = ++m4FeaturesRequest;
  m4FeaturesBusy.value = true;
  m4FeaturesError.value = null;
  m4Features.value = null;
  try {
    const result = await mainApi.analyzeM4Roi(
      mediaId, [...box], m4Engine, category.value);
    if (request !== m4FeaturesRequest || session !== m4Session ||
        selectedMediaId.value !== mediaId || !ui.addElementOpen) return;
    m4Features.value = result;
  } catch (error) {
    if (request === m4FeaturesRequest && session === m4Session) {
      m4FeaturesError.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    if (request === m4FeaturesRequest && session === m4Session) m4FeaturesBusy.value = false;
  }
}

// 选完图立刻识别一次；换引擎也重跑——这就是「两个模型看效果」的对比方式
watch(selectedMediaId, (id) => {
  if (id && ui.addElementOpen) void runDetection();
});


async function analyzeM4Selection() {
  if (!selectedMediaId.value || !m4Box.value || m4Busy.value) {
    m4Error.value = "请先在截图中点击候选框或拖拽框选目标区域";
    return;
  }
  m4Busy.value = true; m4Error.value = null;
  try {
    const mediaId = selectedMediaId.value;
    const caseId = currentCase.value?.id;
    const session = m4Session;
    const saved = await mainApi.addM4Observation(mediaId, {
      object_class: category.value === "other" ? "other" : category.value,
      bbox_normalized: [...m4Box.value],
      review_note: m4Note.value.trim(),
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
  m4Session += 1; // Invalidate pending recovery/history for the previous image.
  if (ui.addElementOpen) void loadM4History(true);
  m4DetectRequest += 1; m4Proposals.value = [];
  m4DetectModel.value = null; m4DetectError.value = null;
  m4Box.value = null; m4Observation.value = null; m4Error.value = null;
  m4FeaturesRequest += 1;
  m4Features.value = null; m4Note.value = '';
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
    date.value = best.date ?? (normalized.capture_time ? normalized.capture_time.slice(0, 10) : DEFAULT_EVENT_DATE);
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
  // 兜底：OCR 已给出角标原名但候选过滤没命中时，仍把它填进输入框（低信任，需人工确认）。
  const rawName = result.result_json.camera?.overlay_name_raw;
  if (!cameraName.value && typeof rawName === "string" && rawName.trim()) {
    cameraName.value = rawName.trim();
    nameSource.value = "画面角标（OCR 原文）";
    syncCatalogMatch();
  }
}

/* ------------------------------ 表单 ------------------------------ */

const timeIndex = ref(-1);
const nameIndex = ref(-1);
const date = ref(DEFAULT_EVENT_DATE);
const timeLabel = ref("");
const timeUnknown = ref(false);
/** 年份明显不合理的 OCR 时间（如把 2026 读成 2020）只提示，不静默改写。 */
const dateSuspect = computed(() => {
  const match = /^(\d{4})-\d{2}-\d{2}$/.exec(date.value);
  if (!match) return true;
  const year = Number(match[1]);
  return year < 2015 || year > 2100;
});
/** 画面 OCR 里出现的年份；与填写日期不一致时要求人工确认。 */
const ocrYears = computed(() => {
  const raw = JSON.stringify(report.value?.result_json?.time ?? {});
  return [...new Set([...raw.matchAll(/20\d{2}/g)].map((m) => m[0]))];
});
const dateAcknowledged = ref(false);
const dateConflict = computed(() => {
  if (timeUnknown.value || !ocrYears.value.length) return false;
  const match = /^(\d{4})-/.exec(date.value);
  if (!match) return false;
  if (ocrYears.value.length > 1) return true;
  return !ocrYears.value.includes(match[1]);
});
watch(date, () => { dateAcknowledged.value = false; });
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

type AttrRow = { key: string; attributeKey: string; value: string; unknown: boolean; auto: boolean; rejected?: boolean; custom?: boolean };
function attrRow(key: string, attributeKey: string): AttrRow {
  return { key, attributeKey, value: "", unknown: false, auto: false };
}
const attributes = reactive<AttrRow[]>([
  attrRow("上衣颜色", "garment.upper.color"),
  attrRow("上衣类型", "garment.upper.type"),
  attrRow("下装颜色", "garment.lower.color"),
  attrRow("下装类型", "garment.lower.type"),
  attrRow("头部特征", "head.appearance"),
  attrRow("鞋", "footwear"),
  attrRow("随身物", "carried.item"),
]);
const vehicleAttributes = reactive<AttrRow[]>([
  attrRow("车辆大类", "vehicle.class"),
  attrRow("车身颜色", "vehicle.color"),
  attrRow("外观类型", "vehicle.form"),
  attrRow("明显附属物", "vehicle.attachment"),
  attrRow("特殊外观", "vehicle.distinctive"),
]);
const assocAttributes = reactive<AttrRow[]>(vehicleAttributes.map((row) => ({ ...row })));
const assocPersonAttributes = reactive<AttrRow[]>(attributes.map((row) => ({ ...row })));
const primaryAttrs = computed(() => (category.value === "vehicle" ? vehicleAttributes : attributes));
const assocAttrs = computed(() => (assocCategory.value === "vehicle" ? assocAttributes : assocPersonAttributes));
const groupRevisions = ref<{ id: string; name: string; old_value: string | null; new_value: string | null; old_review_state: string | null; new_review_state: string; changed_at: string }[]>([]);
const editingGroup = ref<Record<string, unknown> | null>(null);
const editingPrimaryId = ref<string | null>(null);
const editingAssocId = ref<string | null>(null);
const attrTarget = ref<"primary" | "assoc">("primary");
const activeAttrs = computed(() => (attrTarget.value === "assoc" ? assocAttrs.value : primaryAttrs.value));

/** 只把“明确观察到的值”或“明确未知”送到后端；空字段不产生假属性。 */
function attrRows(list: AttrRow[], subject: string, bbox: number[] | null = null) {
  return list
    .filter((attr) => attr.unknown || attr.value.trim())
    .map((attr, index) => ({
      subject_id: subject, name: attr.key.trim() || `特征${index + 1}`,
      attribute_key: attr.custom ? `custom.${(attr.key.trim() || "feature")}` : attr.attributeKey,
      value: attr.unknown ? null : attr.value.trim() || null,
      observation_status: attr.unknown ? "unknown" : "known",
      // 手动添加要素窗口里的人工录入不再走核验流程：人在弹窗里看过并保存，即视为已核验。
      // 模型预填的值同样按已核验落库，但 provenance 保留模型来源，方便回溯它原本是模型给的。
      review_state: attr.unknown ? "unknown" : attr.rejected ? "human_rejected" : "human_verified",
      source: attr.auto && !attr.rejected ? "model" : "human",
      provenance: attr.auto && !attr.rejected ? { model: displayFeatures.value?.appearance.semantic_attributes?.semantic_model ?? null, engine: m4Engine, feature_text: attr.value.trim(), bbox_norm_xyxy: bbox } : {},
    }));
}

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

function hasUnsavedChanges(): boolean {
  return Boolean(m4Observation.value || assocBox.value || note.value.trim()
    || cameraName.value
    || attributes.some((a) => a.unknown || a.value.trim())
    || assocAttributes.some((a) => a.unknown || a.value.trim()));
}
function requestClose() {
  if (hasUnsavedChanges() && !window.confirm("有未保存的修改，确定关闭？")) return;
  ui.addElementOpen = false;
}
function onEscape(event: KeyboardEvent) {
  if (event.key !== "Escape" || !ui.addElementOpen) return;
  if (assocMode.value) { assocMode.value = false; return; }
  if (m4Anchor.value) { m4PointerCancel(); return; }
}

function resetAttrList(list: AttrRow[]) {
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i].custom) list.splice(i, 1);
    else { list[i].value = ""; list[i].unknown = false; list[i].auto = false; list[i].rejected = false; }
  }
}
async function loadGroupForEdit(groupId: string) {
  const caseId = currentCase.value?.id;
  if (!caseId) return;
  try {
    const detail = (await mainApi.getTargetGroup(caseId, groupId)) as Record<string, any>;
    const media = (await mainApi.getMedia(String(detail.media_id))) as unknown as Record<string, any>;
    if (!ui.addElementOpen || ui.editingGroupId !== groupId) return;
    editingGroup.value = detail;
    editingPrimaryId.value = null; editingAssocId.value = null;
    selectedMedia.value = media as any;
    date.value = String(media.capture_time ?? "").slice(0, 10) || DEFAULT_EVENT_DATE;
    const objects = (detail.objects ?? []) as Record<string, any>[];
    const primary = objects.find((o) => o.role === "primary");
    const assoc = objects.find((o) => o.role === "associated");
    if (primary) {
      editingPrimaryId.value = String(primary.id);
      m4Box.value = [...primary.bbox_norm_xyxy] as [number, number, number, number];
      category.value = String(primary.object_class) as "person" | "vehicle" | "other";
      m4Observation.value = primary.m4_observation_id ? ({ id: primary.m4_observation_id } as M4Observation) : null;
    }
    if (assoc) {
      editingAssocId.value = String(assoc.id);
      assocBox.value = [...assoc.bbox_norm_xyxy] as [number, number, number, number];
      assocCategory.value = String(assoc.object_class) as "person" | "vehicle" | "other";
      assocObservation.value = assoc.m4_observation_id ? ({ id: assoc.m4_observation_id } as M4Observation) : null;
    }
    resetAttrList(attributes); resetAttrList(vehicleAttributes);
    resetAttrList(assocAttributes); resetAttrList(assocPersonAttributes);
    for (const at of (detail.attributes ?? []) as Record<string, any>[]) {
      const list = at.subject_id === editingPrimaryId.value ? primaryAttrs.value : assocAttrs.value;
      const fixed = list.find((row) => !row.custom && row.attributeKey === String(at.attribute_key ?? ""));
      const auto = at.review_state === "model_proposed";
      if (fixed) { fixed.value = at.value ?? ""; fixed.unknown = at.observation_status === "unknown"; fixed.auto = auto; fixed.rejected = at.review_state === "human_rejected"; }
      else list.push({ key: String(at.name ?? "特征"), attributeKey: String(at.attribute_key ?? "custom"), value: at.value ?? "", unknown: at.observation_status === "unknown", auto, rejected: at.review_state === "human_rejected", custom: true });
    }
    ignoredAiFeatures.value = Array.isArray((detail.source as Record<string, any> | undefined)?.ai_ignored) ? [...(detail.source as Record<string, any>).ai_ignored] : [];
    rejectedAiFeatures.value = Array.isArray((detail.source as Record<string, any> | undefined)?.ai_rejected) ? [...(detail.source as Record<string, any>).ai_rejected] : [];
    groupRevisions.value = await mainApi.listGroupRevisions(caseId, groupId);
    toast("已载入目标组，可修改特征后保存", "ok");
    void recognize();
  } catch (error) {
    toast("目标组载入失败：" + (error instanceof Error ? error.message : String(error)), "danger");
  }
}
function resetForm() {
  timeIndex.value = -1;
  nameIndex.value = -1;
  selectedMedia.value = null;
  m4Box.value = null; m4Observation.value = null; m4Features.value = null;
  m4FeaturesRequest += 1;
  m4DetectRequest += 1; m4Proposals.value = []; m4DetectModel.value = null;
  m4DetectError.value = null;
  m4Anchor.value = null; m4Note.value = ""; m4Error.value = null;
  m4Restoring.value = false; m4Busy.value = false; m4HistoryBusy.value = false;
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
    if (ui.editingGroupId) { resetForm(); void loadGroupForEdit(ui.editingGroupId); return; }
    if (!open) { m4Session += 1; m4HistoryEpoch += 1; return; }
    m4Session += 1;
    resetForm();
    fillFromElement();
    if (!pendingElementFile.value) void loadM4History(true);
    const linkedObservationId = editingElement.value?.m4ObservationId;
    if (linkedObservationId) void reopenM4Record(linkedObservationId);
    const catalogCaseId = currentCase.value?.id;
    void mainApi
      .listCameras()
      .then((catalogPage) => {
        if (catalogCaseId !== currentCase.value?.id || !ui.addElementOpen) return;
        catalog.value = catalogPage.cameras ?? [];
        if (cameraName.value) syncCatalogMatch();
      })
      .catch(() => {
        if (catalogCaseId !== currentCase.value?.id || !ui.addElementOpen) return;
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

watch(() => currentCase.value?.id, (now, old) => {
  if (!ui.addElementOpen || now === old) return;
  m4Session += 1;
  m4HistoryEpoch += 1;
  resetForm();
  void loadM4History(true);
});
/** 固定字段与动态特征之间的重复/冲突检查，避免同一特征两处取值。 */
function featureConflict(): string | null {
  for (const list of [primaryAttrs.value, assocAttrs.value]) {
    const seen = new Set<string>();
    for (const row of list) {
      const value = row.value.trim();
      if (!value) continue;
      const label = row.key.trim() || "特征";
      const signature = `${label}::${value}`;
      if (seen.has(signature)) return `存在重复特征「${label}：${value}」，请合并后再保存`;
      seen.add(signature);
      if (row.custom) {
        const fixed = list.find((item) => !item.custom && item.key === label && item.value.trim() && item.value.trim() !== value);
        if (fixed) return `「${label}」已由固定字段记录为「${fixed.value.trim()}」，请直接修改该字段或换个描述`;
      }
    }
  }
  return null;
}

async function save(keepOpen: boolean) {
  if (dateConflict.value && !dateAcknowledged.value) {
    toast("画面年份与填写日期冲突：请核对角标并勾选「已人工核对日期」后再保存", "danger");
    return;
  }
  // A selected ROI must have a durable, matching observation record.
  // Never place the synthetic "manual-unsaved" marker on the task timeline.
  if (m4Box.value && !ui.editingGroupId && !m4MatchesStored()) {
    if (!selectedMediaId.value) { toast("请先上传截图并保存区域观测", "warn"); return; }
    await analyzeM4Selection();
    if (!m4MatchesStored()) { toast("主目标观测保存失败，未写入目标组", "danger"); return; }
  }
  if (m4Observation.value && selectedMedia.value?.case_id !== currentCase.value?.id) {
    toast("当前截图不属于所选案件，请重新打开对应案件", "danger");
    return;
  }
  if (!editingElement.value && ui.editingGroupId && currentCase.value && m4Box.value && editingPrimaryId.value) {
    const caseId = currentCase.value.id;
    const groupId = ui.editingGroupId;
    const detail = editingGroup.value as Record<string, any> | null;
    try {
      await mainApi.updateTargetGroup(caseId, groupId, {
        revision: detail?.revision ?? 1,
        primary_object_id: editingPrimaryId.value,
        objects: [
          { id: editingPrimaryId.value, object_class: category.value, role: "primary", bbox_norm_xyxy: [...m4Box.value], m4_observation_id: m4Observation.value?.id ?? null },
          ...(assocBox.value ? [{ id: editingAssocId.value ?? "A1", object_class: assocCategory.value, role: "associated", bbox_norm_xyxy: [...assocBox.value], m4_observation_id: assocObservation.value?.id ?? null }] : []),
        ],
        attributes: [...attrRows(primaryAttrs.value, editingPrimaryId.value, m4Box.value), ...(assocBox.value && editingAssocId.value ? attrRows(assocAttrs.value, editingAssocId.value, assocBox.value) : [])],
        source: { ...((editingGroup.value as Record<string, any> | null)?.source ?? {}), summary: note.value.trim(), ai_ignored: [...ignoredAiFeatures.value], ai_rejected: [...rejectedAiFeatures.value] },
      });
      groupRevisions.value = await mainApi.listGroupRevisions(caseId, groupId);
      toast("目标组已更新", "ok");
      ui.addElementOpen = false;
      return;
    } catch (error) {
      toast("目标组更新失败：" + (error instanceof Error ? error.message : String(error)), "danger");
      return;
    }
  }
  // 编辑已有要素时沿用原目标组；新建时用下面 createTargetGroup 的返回值。
  let groupId: string | null = editingElement.value ? elementGroupIds.value[editingElement.value.id] ?? null : null;
  // 定位只认「完整截图」：角标时间 + 能对上镜头；局部截图（只有目标块）只写特征。
  const base: "full" | "crop" = !timeUnknown.value && (!!confirmedCamera.value || !!cameraName.value?.trim()) ? "full" : "crop";
  const payload: ElementPayload = {
    date: date.value,
    timeLabel: timeLabel.value,
    timeUnknown: timeUnknown.value,
    cameraId: confirmedCamera.value,
    base,
    targetGroupId: groupId,
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
    assocBox: assocBox.value ? [...assocBox.value] as [number, number, number, number] : null,
    groundContactPixel: groundContactGeometry().pixel,
    frameSize: groundContactGeometry().frameSize,
  };

  // B8-B0：主目标 + 关联对象一次写入目标组；关联对象只作证据，不成为独立追踪任务
  if (!editingElement.value && currentCase.value && selectedMediaId.value && m4Observation.value && m4Box.value) {
    try {
      let assocObsId = assocBox.value ? (assocObservation.value?.id ?? null) : null;
      if (assocBox.value && !assocObsId) {
        const saved = await mainApi.addM4Observation(selectedMediaId.value, {
          object_class: assocCategory.value === "other" ? "other" : assocCategory.value,
          bbox_normalized: [...assocBox.value],
          review_note: m4Note.value.trim(),
        });
        assocObservation.value = saved;
        assocObsId = saved.id;
      }
      const created = await mainApi.createTargetGroup(currentCase.value.id, {
        media_id: selectedMediaId.value,
        primary_object_id: "P1",
        objects: [
          { id: "P1", object_class: category.value === "other" ? "other" : category.value,
            role: "primary", bbox_norm_xyxy: [...m4Box.value],
            m4_observation_id: m4Observation.value.id },
          ...(assocBox.value ? [{ id: "A1", object_class: assocCategory.value, role: "associated",
            bbox_norm_xyxy: [...assocBox.value], m4_observation_id: assocObsId }] : []),
        ],
        relations: assocBox.value ? [{ subject_id: "P1", predicate: assocRelation.value, object_id: "A1" }] : [],
        attributes: [...attrRows(primaryAttrs.value, "P1", m4Box.value), ...(assocBox.value ? attrRows(assocAttrs.value, "A1", assocBox.value) : [])],
        source: { camera_name: cameraName.value || null, time_status: timeUnknown.value ? "unknown" : "unverified", summary: note.value.trim(), ai_ignored: [...ignoredAiFeatures.value], ai_rejected: [...rejectedAiFeatures.value],
          // 截图时间/镜头/类型一并存进目标组：刷新后即使锚点没建成功，也能重建时间卡并补定位
          capture_local: timeUnknown.value ? null : `${date.value}T${timeLabel.value.trim()}`,
          capture_source: "osd", camera_uuid: confirmedCamera.value, base },
      });
      groupId = created.id;
      // payload 在组创建之前就构造好了，这里必须回填，否则保存时拿不到目标组，
      // 定位点与时间轴卡片都会静默消失。
      payload.targetGroupId = groupId;
    } catch (error) {
      toast("目标组保存失败：" + (error instanceof Error ? error.message : String(error)), "danger");
      return;
    }
  }
  const conflict = featureConflict();
  if (conflict) { toast(conflict, "warn"); return; }

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

const m4Thumb = ref("");
const assocThumb = ref("");

/** 裁剪当前框，作为「当前选中目标」的小图；不重新识别、不请求后端。 */
function cropThumb(box: [number, number, number, number] | null): string {
  const img = m4Image.value;
  if (!box || !img || !img.naturalWidth) return "";
  const [x1, y1, x2, y2] = box;
  const canvas = document.createElement("canvas");
  const sw = Math.max(1, (x2 - x1) * img.naturalWidth);
  const sh = Math.max(1, (y2 - y1) * img.naturalHeight);
  canvas.width = 160;
  canvas.height = Math.max(1, Math.round(160 * sh / sw));
  const context = canvas.getContext("2d");
  if (!context) return "";
  context.drawImage(img, x1 * img.naturalWidth, y1 * img.naturalHeight, sw, sh, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/png");
}

watch([m4Box, assocBox], () => {
  m4Thumb.value = cropThumb(m4Box.value);
  assocThumb.value = cropThumb(assocBox.value);
}, { deep: true });

function pickProposalIndex(index: number) {
  const proposal = m4Proposals.value[index];
  if (proposal) void selectM4Proposal(proposal);
}

function bindCatalogCamera(cameraUuid: string) {
  confirmedCamera.value = cameraUuid || null;
}

function focusTarget(role: "primary" | "assoc") {
  attrTarget.value = role;
  if (role === "assoc" && !assocBox.value) assocMode.value = true;
}
const zoom = ref(1);
const panX = ref(0);
const panY = ref(0);
const stageStyle = computed(() => ({ transform: `translate(${panX.value}px, ${panY.value}px) scale(${zoom.value})` }));
function zoomBy(delta: number) {
  zoom.value = Math.min(4, Math.max(1, Math.round((zoom.value + delta) * 100) / 100));
  if (zoom.value === 1) { panX.value = 0; panY.value = 0; }
}
function resetZoom() { zoom.value = 1; panX.value = 0; panY.value = 0; }
function onWheel(event: WheelEvent) { zoomBy(event.deltaY < 0 ? 0.25 : -0.25); }
let panStart: { x: number; y: number; px: number; py: number } | null = null;
function onPanStart(event: PointerEvent) {
  if (event.button !== 1) return;
  panStart = { x: event.clientX, y: event.clientY, px: panX.value, py: panY.value };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}
function onPanMove(event: PointerEvent) {
  if (!panStart) return;
  panX.value = panStart.px + (event.clientX - panStart.x);
  panY.value = panStart.py + (event.clientY - panStart.y);
}
function onPanEnd() { panStart = null; }

const displayFeatures = computed(() => (attrTarget.value === "assoc" ? assocFeatures.value : m4Features.value));
const displayFeaturesBusy = computed(() => (attrTarget.value === "assoc" ? assocFeaturesBusy.value : m4FeaturesBusy.value));
const modelFeatureList = computed(() => (displayFeatures.value?.appearance.semantic_attributes?.features_unverified ?? []).map((f) => String(f)).filter(Boolean));

const rejectedAiFeatures = ref<string[]>([]);
const ignoredAiFeatures = ref<string[]>([]);
const aiSuggestions = computed(() => {
  const used = new Set(activeAttrs.value.map((a) => a.value.trim()).filter(Boolean));
  return modelFeatureList.value.filter((f) => !used.has(f) && !ignoredAiFeatures.value.includes(f) && !rejectedAiFeatures.value.includes(f));
});
function acceptAiFeature(feature: string) {
  activeAttrs.value.push({ key: "AI线索", attributeKey: "custom.ai", value: feature, unknown: false, auto: true, custom: true });
  ignoredAiFeatures.value.push(feature);
}
function ignoreAiFeature(feature: string) { ignoredAiFeatures.value.push(feature); }
function rejectAiFeature(feature: string) { if (!rejectedAiFeatures.value.includes(feature)) rejectedAiFeatures.value.push(feature); }
function undoReject(feature: string) { rejectedAiFeatures.value = rejectedAiFeatures.value.filter((f) => f !== feature); }

</script>

<template>
  <BaseModal
    :open="ui.addElementOpen"
    :title="editingElement ? '编辑目标要素' : '添加目标要素'"
    subtitle="按已配置的业务模型自动发现截图候选；点框或手动框选后复核外观。模型调试请使用左侧「模型测试」。"
    width="xl"
    height="92vh"
    @close="requestClose"
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
          <div class="preview-stage" :style="stageStyle" @wheel.prevent="onWheel" @pointerdown="onPanStart" @pointermove="onPanMove" @pointerup="onPanEnd" @pointercancel="onPanEnd">
            <img
              ref="m4Image"
              :src="mainApi.mediaContentUrl(selectedMedia.id)"
              @load="syncM4Layout"
              :alt="selectedMedia.original_name"
            />
            <div v-if="m4Layout" class="m4a-select-overlay" :style="m4OverlayStyle"
              title="点击候选框选中目标；也可以直接拖拽框选"
              @pointerdown.prevent="m4PointerDown"
              @pointermove.prevent="m4PointerMove"
              @pointerup.prevent="m4PointerUp"
              @pointercancel="m4PointerCancel">
              <div v-for="proposal in m4Proposals" :key="'cand-' + (proposal.model_id || m4Engine) + '-' + proposal.proposal_index"
                class="m4a-candidate-box" :style="m4CandidateStyle(proposal.bbox_normalized)"
                :title="(proposal.model_name ? proposal.model_name + ' · ' : '') + proposal.detector_label"
                @pointerdown.stop.prevent="selectM4Proposal(proposal)" />
              <div v-if="m4Box" class="m4a-selected-box" :style="m4BoxStyle"></div>
              <div v-if="assocBox" class="m4a-assoc-box" :style="assocBoxStyle"
                :title="'关联对象（不参与主目标身份）'"></div>
            </div>
          </div>
          <div class="preview-zoom">
            <button type="button" @click="zoomBy(-0.25)">−</button>
            <span>{{ Math.round(zoom * 100) }}%</span>
            <button type="button" @click="zoomBy(0.25)">＋</button>
            <button type="button" @click="resetZoom">重置</button>
          </div>
            <span class="preview-tag">只读原始画面 · 选图后自动框出候选，点框或拖拽选中</span>
          </template>
          <p v-else class="preview-empty">选择一张截图，自动识别画面角标时间与摄像头名称</p>
        </div>

        
        <p v-if="mediaError" class="ae-error">{{ mediaError }}</p>

      </section>

      <!-- 右：确认表单 -->
      <aside class="ae-right scroll-y">
        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">画面角标时间</span>
            <StatusChip :label="timeUnknown ? '已标记未知' : timeStatus.label" :tone="timeUnknown ? 'muted' : timeStatus.tone" />
          </div>


          <div class="time-input">
            <input v-model="date" class="input num" type="date" :disabled="timeUnknown" aria-label="日期" />
            <input v-model="timeLabel" class="input num" type="text" placeholder="HH:MM:SS" :disabled="timeUnknown" aria-label="时分秒" />
            <button class="chip-btn" :class="{ 'is-on': timeUnknown }" @click="timeUnknown ? (timeUnknown = false) : markTimeUnknown()">
              {{ timeUnknown ? "已标未知" : "标记未知" }}
            </button>
          </div>
          <p v-if="dateSuspect" class="field-hint warn">
            日期年份异常（OCR 可能把年份读成 2020 等），请核对画面角标后手动修正；系统不会静默改日期。
          </p>
          <template v-if="dateConflict">
            <p class="field-hint warn">画面 OCR 年份为 {{ ocrYears.join(" / ") }}，与填写日期不一致；请核对角标，确认后才能保存。</p>
            <label class="field-hint"><input v-model="dateAcknowledged" type="checkbox" /> 已人工核对日期</label>
          </template>
          <p class="field-hint">OCR 原值只用于内部核对；人工修改只影响本要素的入库时间。</p>
        </div>

        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">摄像头名称</span>
            <StatusChip :label="cameraStatus.label" :tone="cameraStatus.tone" />
          </div>
          <div class="camera-line">
            <input v-model="cameraNameInput" class="input" type="text" placeholder="OCR 识别名称，可直接修改" aria-label="摄像头名称" />
            <button class="btn btn--sm" type="button" @click="syncCatalogMatch">重新匹配</button>
            <button class="chip-btn" :class="{ 'is-on': !cameraName }" @click="markUnlocated">仅确定区域</button>
          </div>
          <label v-if="catalogMatches.length" class="field">
            <span class="field-label">匹配到的镜头</span>
            <select class="input" :value="confirmedCamera ?? ''" @change="bindCatalogCamera(($event.target as HTMLSelectElement).value)">
              <option value="">不绑定目录</option>
              <option v-for="camera in catalogMatches" :key="camera.camera_uuid" :value="camera.camera_uuid">{{ camera.display_name }}</option>
            </select>
          </label>
          <p v-else class="field-hint">未匹配到镜头目录，仅保留名称；可到「录像资源」核对镜头。</p>
        </div>

        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">当前选中目标</span>
            <span class="field-hint">主目标 + 关联对象</span>
          </div>
          <div class="target-cards">
            <button type="button" class="target-card" :class="{ 'is-on': attrTarget === 'primary' }" @click="focusTarget('primary')">
              <img v-if="m4Thumb" :src="m4Thumb" alt="主目标" />
              <span class="target-role">主目标要素</span>
              <span class="field-hint"><select v-model="category" class="input" @click.stop="attrTarget = 'primary'" @change="attrTarget = 'primary'"><option value="person">人员</option><option value="vehicle">车辆</option><option value="other">其他</option></select></span>
            </button>
            <button type="button" class="target-card" :class="{ 'is-on': attrTarget === 'assoc' }" @click="focusTarget('assoc')">
              <img v-if="assocThumb" :src="assocThumb" alt="关联对象" />
              <span class="target-role">关联要素</span>
              <span class="field-hint"><select v-model="assocCategory" class="input" @click.stop="attrTarget = 'assoc'" @change="attrTarget = 'assoc'"><option value="person">人员</option><option value="vehicle">车辆</option><option value="other">其他</option></select></span>
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
            <span class="field-label">可见特征（选填）</span>
            <span class="field-hint">按对象归属 · 未知≠不匹配</span>
          </div>
          <div v-for="(attr, index) in activeAttrs" :key="index" class="attr-row">
            <input v-if="attr.custom" v-model="attr.key" class="input attr-custom-key" placeholder="特征部位" />
            <span v-else class="attr-key">{{ attr.key }}</span>
            <input v-model="attr.value" @input="attr.auto = false" class="input" :disabled="attr.unknown" :placeholder="attr.unknown ? '未知' : '可观察描述'" />
            <button v-if="attr.custom" class="chip-btn" @click="activeAttrs.splice(index, 1)">删</button>
            <button class="chip-btn" :class="{ 'is-on': attr.rejected }" @click="attr.rejected = !attr.rejected">驳回</button>
            <button class="chip-btn" :class="{ 'is-on': attr.unknown }" @click="attr.unknown = !attr.unknown">{{ attr.unknown ? "未知" : "标未知" }}</button>
          </div>
          <button class="chip-btn" @click="addCustomAttribute">＋ 其他可辨识特征</button>
          <p class="field-hint">人工填写记为 human_verified；标未知只表示画面看不清，不表示没有或不匹配。</p>
        </div>

        <div class="ae-section">
        <div v-if="groupRevisions.length" class="ae-section">
          <details>
            <summary class="field-label">修订历史（{{ groupRevisions.length }}）</summary>
            <div v-for="rev in groupRevisions" :key="rev.id" class="rev-row">
              <span class="rev-name">{{ rev.name }}</span>
              <span class="rev-diff">{{ rev.old_value ?? "（无）" }} → {{ rev.new_value ?? "（无）" }}</span>
              <span class="rev-state">{{ rev.old_review_state ?? "" }} → {{ rev.new_review_state }}</span>
              <span class="rev-time">{{ rev.changed_at.slice(0, 19).replace("T", " ") }}</span>
            </div>
          </details>
        </div>
          <span class="field-label">备注 / 来源说明</span>
          <textarea v-model="note" class="textarea" rows="2" placeholder="例如：角标时间与文件名交叉核对；画面低照度" />
        </div>
        <div class="ae-section">
          <div class="ae-field-head">
            <span class="field-label">大模型外观判断</span>
            <span class="field-hint">参考 · 待人工核实</span>
          </div>
          <p v-if="displayFeaturesBusy" class="field-hint">正在分析当前框…</p>
          <template v-else-if="displayFeatures">
            <ul v-if="modelFeatureList.length" class="model-features">
              <li v-for="(feature, index) in modelFeatureList" :key="index">{{ feature }}</li>
            </ul>
            <p v-else class="field-hint">模型没有给出可用外观描述。</p>
            <p class="field-hint">主色 {{ m4ToneText(displayFeatures.appearance.roi_tones_unverified) }} · 画质 {{ displayFeatures.quality.flags.join("、") || "未见异常" }}</p>
            <p class="field-hint">来源 {{ displayFeatures.appearance.semantic_attributes?.semantic_model || "视觉模型" }}；只作线索，不自动确认身份。</p>
            <div v-if="aiSuggestions.length" class="ai-suggest">
              <p class="field-hint">AI 新发现（{{ aiSuggestions.length }}）</p>
              <div v-for="(feature, index) in aiSuggestions" :key="index" class="ai-suggest-row">
                <span class="truncate">{{ feature }}</span>
                <button class="chip-btn" @click="acceptAiFeature(feature)">加入其他特征</button>
                <button class="chip-btn" @click="ignoreAiFeature(feature)">忽略</button>
                <button class="chip-btn" @click="rejectAiFeature(feature)">否决</button>
              </div>
            </div>
          </template>
          <p v-else class="field-hint">在左侧选择或框选目标后显示。</p>
            <div v-if="rejectedAiFeatures.length" class="ai-suggest">
              <p class="field-hint">已否决 {{ rejectedAiFeatures.length }} 条（不会进入属性）</p>
              <div v-for="(feature, index) in rejectedAiFeatures" :key="index" class="ai-suggest-row">
                <span class="truncate">{{ feature }}</span>
                <button class="chip-btn" @click="undoReject(feature)">撤销否决</button>
              </div>
            </div>
        </div>
      </aside>
    </div>

    <template #footer>
      <p class="ae-foot-note">保存后不会自动启动或扩大研判；这里的特征由人工在弹窗内确认，保存即记为「已核验」，不需要再走核验流程。</p>
      <span class="grow" />
      <button class="btn btn--ghost" @click="requestClose">取消</button>
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
  grid-template-columns: minmax(0, 65fr) minmax(0, 35fr);
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
/* 模型候选框：默认可点，点中即成为选中框并触发关键特征 */
.m4a-candidate-box { position: absolute; border: 1.5px dashed rgba(84,207,255,.85);
  background: rgba(84,207,255,.05); border-radius: 2px;
  cursor: pointer; pointer-events: auto; }
.m4a-candidate-box:hover { background: rgba(84,207,255,.18); border-style: solid; }
.m4-engine-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.m4-engine-row .chip-btn small { color: var(--text-3); font-size: 10px; margin-left: 4px; }
.m4-engine-row .chip-btn.is-active { border-color: #54cfff; color: #54cfff; }
.m5-model-picks { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.m5-model-picks .ae-error { flex-basis: 100%; }
.m5-model-pill { display: inline-flex; align-items: center; gap: 4px;
  border: 1px solid var(--line-2); padding: 3px 7px; border-radius: 5px;
  color: var(--text-2); cursor: pointer; }
.m5-model-pill small { color: var(--text-4); }
.m5-prompt-editor { border: 1px dashed var(--line-2); border-radius: 5px; padding: 6px; }
.m5-prompt-editor summary { cursor: pointer; color: var(--text-2); }
.m5-prompt-editor textarea { display: block; width: 100%; resize: vertical;
  box-sizing: border-box; border: 1px solid var(--line-2); border-radius: 5px;
  background: var(--bg-2); color: var(--text-1); font: inherit; padding: 7px;
  margin: 6px 0; }
.detect-proposal-list { display: flex; gap: 6px; flex-wrap: wrap; }
.detect-panel { display: grid; gap: 7px; padding: 9px;
  border: 1px dashed var(--line-2); border-radius: 6px; font-size: 11px; }
.detect-panel p { margin: 0; color: var(--text-3); }
.detect-proposal { display: flex; justify-content: space-between; flex-wrap: wrap;
  align-items: center; gap: 8px; border-top: 1px solid var(--line-2); padding-top: 5px; }
.m4a-panel { border: 1px solid var(--line-2); border-radius: 6px; padding: 9px;
  display: grid; gap: 6px; background: var(--bg-1); font-size: 11px; }
.m4a-panel-title { font-size: 12px; font-weight: 600; color: var(--text-1); }
.m4a-panel p { margin: 0; color: var(--text-3); }
.m4a-panel-form { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.m4a-panel-form .input { flex: 1 1 190px; height: 27px; }
.m4a-panel-form label { white-space: nowrap; }
.m4a-result { display: grid; gap: 3px; padding: 7px; border-radius: 5px;
  border: 1px solid rgba(47,207,146,.3); color: var(--text-2); }
.m4a-history { flex: none; display: grid; gap: 6px; max-height: 165px; overflow-y: auto;
  padding: 8px; border: 1px solid var(--line-2); border-radius: 6px; font-size: 11px; }
.m4a-history-head { display: flex; align-items: center; justify-content: space-between; }
.m4a-history-item { display: flex; gap: 8px; align-items: center; min-width: 0; }
.m4a-history-item span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.m4a-history-hint { color: var(--text-3); font-size: 10px; margin: 0; }
.m4a-history-more { display: flex; justify-content: flex-end; }
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
.m4a-assoc-box {
  position: absolute;
  border: 2px solid #f0a020;
  border-radius: 2px;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, .5);
  pointer-events: none;
}
.assoc-row { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
.assoc-actions { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }

.camera-line { display: flex; gap: 6px; align-items: center; }
.camera-line .input { flex: 1 1 auto; min-width: 0; }
.target-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.target-card { border: 1px dashed var(--line-2); border-radius: 6px; padding: 6px; display: flex; flex-direction: column; gap: 4px; align-items: center; justify-content: center; min-height: 108px; background: transparent; color: inherit; font: inherit; cursor: pointer; }
.target-card.is-on { border-color: var(--accent); }
.target-card img { width: 100%; height: auto; max-height: 96px; object-fit: contain; border-radius: 4px; background: #000; }
.target-role { font-size: 11px; color: var(--text-2); }
.ae-fold { border-top: 1px dashed var(--line-2); padding-top: 6px; }
.ae-fold > summary { cursor: pointer; font-size: 11px; color: var(--text-3); }
.input--compact { height: 26px; max-width: 190px; }
.detect-panel--compact { display: grid; gap: 6px; }

.preview-stage { position: relative; width: 100%; height: 100%; transform-origin: center center; }
.preview-zoom { position: absolute; right: 8px; bottom: 8px; z-index: 4; display: flex; gap: 4px; align-items: center; background: rgba(0,0,0,.55); padding: 3px 6px; border-radius: 6px; font-size: 11px; }
.preview-zoom button { background: none; border: none; color: #cfd8e3; cursor: pointer; padding: 0 4px; font-size: 12px; }

.model-features { margin: 4px 0; padding-left: 16px; font-size: 11.5px; color: var(--text-2); display: grid; gap: 2px; }

.ai-suggest { display: grid; gap: 4px; margin-top: 6px; }
.ai-suggest-row { display: flex; gap: 6px; align-items: center; }
.ai-suggest-row .truncate { flex: 1; font-size: 11.5px; }

.rev-row { display: grid; grid-template-columns: 80px 1fr auto auto; gap: 8px; font-size: 11.5px; padding: 3px 0; border-bottom: 1px dashed var(--line-weak); }
.rev-name { color: var(--fg); }
.rev-diff { color: var(--fg-dim); }
.rev-state, .rev-time { color: var(--fg-mute); font-size: 11px; }
</style>
