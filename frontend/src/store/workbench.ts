import { computed, reactive, ref, shallowRef } from "vue";
import {
  roundInfo,
  timeWindowPresets,
  type Job,
  type JobStatus,
  type Observation,
  type TargetElement,
  type TargetFeature,
  type TimeWindow,
} from "../data/mock";
import {
  mainApi,
  type CaseDeleteResult,
  type CaseItem,
  type RecordingClip,
  type RecordingSource,
  type RecordingSummary,
} from "../services/mainApi";

export type RoundStatus = "draft" | "ready" | "running" | "paused" | "review" | "done";
export type Direction = "backward" | "forward";
export type TimelineFilter = "all" | "anchor" | "review" | "approved" | "excluded";
export type ToastTone = "info" | "ok" | "warn" | "danger";
/** 底部「正在分析」卡片的缩略显示方式：统一用缩略图还是缩略视频 */
export type DockThumbMode = "image" | "video";
/** 录像目录选择器的用途：添加视频（选视频所在目录）/ 挂载目录 */
export type RecordingDirMode = "video" | "dir";

export type Toast = { id: number; text: string; tone: ToastTone };

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

/* ------------------------------ 工作台状态 ------------------------------ */

export const wb = reactive({
  /** 没有要素就是空任务态：所有案件都从空任务开始，不再有演示数据 */
  emptyMode: true,
  /** 当前案件；空表示还没有选择案件 */
  caseId: null as string | null,

  selectedCameraId: null as string | null,
  selectedElementId: null as string | null,
  selectedAnchorId: null as string | null,
  selectedFeatureId: null as string | null,
  selectedObservationId: null as string | null,
  selectedJobId: null as string | null,
  selectedIntervalId: null as string | null,

  direction: "backward" as Direction,
  from: "18:00",
  to: "20:12",

  roundStatus: "draft" as RoundStatus,
  roundProgress: 0,

  previewOn: false,
  previewJobId: "job-1042" as string | null,

  newCandidates: 0,
  timelineFilter: "all" as TimelineFilter,
  timelineRound: "all",
  timelineCamera: "all",
});

export const layers = reactive({
  fov: true,
  anchors: true,
});

export const pendingElementFile = shallowRef<File | null>(null);

let pickingImage = false;

/**
 * 点「添加要素」时立刻弹系统文件对话框（必须同步在点击手势里调用）。
 * 取消时返回 null；不残留隐藏 input。
 */
function pickImageFile(): Promise<File | null> {
  if (pickingImage) return Promise.resolve(null);
  pickingImage = true;
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/webp,image/*";
    input.style.position = "fixed";
    input.style.left = "-9999px";
    input.style.opacity = "0";
    document.body.appendChild(input);

    let settled = false;
    const finish = (file: File | null) => {
      if (settled) return;
      settled = true;
      pickingImage = false;
      input.remove();
      resolve(file);
    };

    input.addEventListener("change", () => finish(input.files?.[0] ?? null), { once: true });
    input.addEventListener("cancel", () => finish(null), { once: true });
    // 兜底：部分浏览器取消时不发 cancel，窗口回焦后按「没选文件」收尾
    window.addEventListener(
      "focus",
      () =>
        window.setTimeout(() => {
          if (!input.files?.length) finish(null);
        }, 800),
      { once: true },
    );

    input.click();
  });
}

export const timeWindow = reactive<TimeWindow & { preset: string }>({
  start: 1080,
  end: 1440,
  preset: "night",
});

export const featureBar = reactive({ collapsed: false });

export const ui = reactive({
  coverageOpen: false,
  coverageCameraId: null as string | null,
  caseManageOpen: false,
  recordingDirOpen: false,
  videoNamingOpen: false,
  /** 录像目录选择器这次是「添加视频」还是「挂载目录」 */
  recordingDirMode: "video" as RecordingDirMode,
  addElementOpen: false,
  editingElementId: null as string | null,
  elementsOpen: false,
  featureDetailOpen: false,
  correctionOpen: false,
  dockCollapsed: false,
  dockHeight: 264,
  /** 任务控制卡片统一显示缩略图还是缩略视频 */
  dockThumbMode: storedThumbMode(),
  /** 人工审核弹窗：正在审哪一段 */
  reviewClipOpen: false,
  reviewClipJobId: null as string | null,
  toasts: [] as Toast[],
  nextElementSeq: 1,
});

export const elementList = ref<TargetElement[]>([]);
export const featureList = ref<TargetFeature[]>([]);
export const observationList = ref<Observation[]>([]);
export const jobList = ref<Job[]>([]);
export const featureStats = reactive({ pending: 0, conflicts: 0, refs: 0 });

/* ------------------------------ 录像资源 ------------------------------ */

/**
 * 案件已登记录像目录的扫描结果（真实后端数据，按案件隔离）。
 * camera_uuids 来自「扫描到名称并唯一命中台账」的录像段：只有这些镜头才取消灰色。
 */
export const recordingSources = ref<RecordingSource[]>([]);
export const recordingSummary = ref<RecordingSummary | null>(null);
export const recordingClips = ref<RecordingClip[]>([]);
export const recordingLoading = ref(false);
export const recordingBusy = ref(false);
export const recordingError = ref<string | null>(null);

/**
 * 地图上取消灰色的镜头 UUID：只来自「扫描到名称并唯一命中台账」的录像段。
 * 没有选择案件、或案件还没扫描过 → 空集合，镜头全灰（添加要素不会点亮任何镜头）。
 */
export const recordingCameraUuids = computed<Set<string>>(
  () => new Set(recordingSummary.value?.camera_uuids ?? []),
);

/* ------------------------------ 案件 ------------------------------ */

export const caseList = ref<CaseItem[]>([]);
export const caseLoading = ref(false);
export const caseWorking = ref(false);
export const caseError = ref<string | null>(null);

export const currentCase = computed(() => caseList.value.find((item) => item.id === wb.caseId) ?? null);
export const currentCaseName = computed(() => currentCase.value?.name ?? "未选择案件");

/** 目标要素是前端内存数据：按案件各存一份，切换案件不会互相覆盖 */
type WorkspaceSnapshot = {
  elements: TargetElement[];
  features: TargetFeature[];
  observations: Observation[];
  jobs: Job[];
  nextElementSeq: number;
  stats: { pending: number; conflicts: number; refs: number };
};

const caseWorkspaces = new Map<string, WorkspaceSnapshot>();

function captureWorkspace(): WorkspaceSnapshot {
  return {
    elements: clone(elementList.value),
    features: clone(featureList.value),
    observations: clone(observationList.value),
    jobs: clone(jobList.value),
    nextElementSeq: ui.nextElementSeq,
    stats: { ...featureStats },
  };
}

function emptyWorkspace(): WorkspaceSnapshot {
  return {
    elements: [],
    features: [],
    observations: [],
    jobs: [],
    nextElementSeq: 1,
    stats: { pending: 0, conflicts: 0, refs: 0 },
  };
}

function applyWorkspace(work: WorkspaceSnapshot) {
  elementList.value = work.elements;
  featureList.value = work.features;
  observationList.value = work.observations;
  jobList.value = work.jobs;
  ui.nextElementSeq = work.nextElementSeq;
  featureStats.pending = work.stats.pending;
  featureStats.conflicts = work.stats.conflicts;
  featureStats.refs = work.stats.refs;
  wb.selectedElementId = work.elements[0]?.id ?? null;
  wb.selectedAnchorId = work.elements[0]?.id ?? null;
  wb.selectedCameraId = work.elements[0]?.cameraId ?? null;
  wb.selectedFeatureId = work.features[0]?.id ?? null;
  wb.selectedObservationId = work.observations[0]?.id ?? null;
  wb.newCandidates = work.stats.pending;
  const hasElements = work.elements.length > 0;
  wb.emptyMode = !hasElements;
  wb.roundStatus = hasElements ? "running" : "draft";
  wb.roundProgress = 0;
}

/* ------------------------------ 派生视图 ------------------------------ */

export const visibleElements = computed(() => (wb.emptyMode ? [] : elementList.value));
export const visibleObservations = computed(() => (wb.emptyMode ? [] : observationList.value));
export const visibleJobs = computed(() => (wb.emptyMode ? [] : jobList.value));

/* --------------------------- 底部任务控制：分析卡片 --------------------------- */

/** 录像段按文件名索引：作业只记文件名，靠它对齐扫描结果里的摄像头名、开始时间与缩略帧 */
const clipsByFile = computed(() => {
  const map = new Map<string, RecordingClip>();
  for (const clip of recordingClips.value) {
    if (!map.has(clip.file_name)) map.set(clip.file_name, clip);
  }
  return map;
});

/** 作业对应的录像段；没有扫描结果也照样返回 null，卡片退回只显示作业自身信息 */
export function jobClip(job: Job): RecordingClip | null {
  if (job.clipId) {
    const byId = recordingClips.value.find((clip) => clip.id === job.clipId);
    if (byId) return byId;
  }
  return clipsByFile.value.get(job.file) ?? null;
}

export const JOB_STATUS_TEXT: Record<JobStatus, string> = {
  queued: "排队中",
  reading: "读取中",
  detecting: "模型分析",
  assembling: "结果整理",
  review: "待人工审核",
  done: "已完成",
  nodetect: "无检出",
  failed: "失败",
  canceled: "已取消",
};

/** 需要人工审核：作业自己标了，或状态就是 review */
export const jobNeedsReview = (job: Job) => job.needsReview === true || job.status === "review";

/** 状态对应的「系统在做什么」——用文字说明，不给百分比：视频研判本来就是不确定的 */
export const JOB_STATUS_ACTION: Record<JobStatus, string> = {
  queued: "在队列里等前面的片段读完",
  reading: "正在逐帧读取这一段录像",
  detecting: "正在这段里比对目标的外观特征",
  assembling: "正在把这段里的可疑片段归档成候选",
  review: "读完了，等人工确认这一段",
  done: "读完了，已归档进候选结果",
  nodetect: "读完了，没有找到与目标相符的片段",
  failed: "读取中断，可重新排队",
  canceled: "已取消，不再继续读",
};

const RUNNING_ORDER: JobStatus[] = ["reading", "detecting", "assembling"];

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export type JobCardView = {
  job: Job;
  clip: RecordingClip | null;
  review: boolean;
  statusText: string;
  cameraName: string;
  timeText: string;
  duration: string;
  thumbUrl: string | null;
  clipUrl: string | null;
};

/**
 * 任务控制右侧卡片：待审核的一律排到最前（并在卡片上高亮），
 * 其余按 分析中 → 排队 → 已结束 排序，同组按开始时间。
 */
export const jobCards = computed<JobCardView[]>(() => {
  const rank = (job: Job): number => {
    if (jobNeedsReview(job)) return 0;
    if (RUNNING_ORDER.includes(job.status)) return 1;
    if (job.status === "queued") return 2;
    return 3;
  };
  const list = [...visibleJobs.value].sort((a, b) => {
    const diff = rank(a) - rank(b);
    return diff !== 0 ? diff : a.from.localeCompare(b.from) || a.id.localeCompare(b.id);
  });
  return list.map((job) => {
    const clip = jobClip(job);
    return {
      job,
      clip,
      review: jobNeedsReview(job),
      statusText: job.statusLabel || JOB_STATUS_TEXT[job.status],
      cameraName: job.cameraName || clip?.camera_name || job.cameraId || "未登记镜头",
      timeText: `${job.from}–${job.to}`,
      duration: formatDuration(clip?.duration_seconds),
      thumbUrl: job.thumbUrl ?? null,
      clipUrl: job.clipUrl ?? null,
    };
  });
});

/** 任务控制左栏的基础信息：只回答「几段在跑、几段要人看」 */
export const jobStats = computed(() => {
  const list = visibleJobs.value;
  const review = list.filter(jobNeedsReview);
  const running = list.filter((job) => RUNNING_ORDER.includes(job.status));
  const queued = list.filter((job) => job.status === "queued");
  const finished = list.filter((job) => ["done", "nodetect", "failed", "canceled"].includes(job.status));
  return { total: list.length, review: review.length, running: running.length, queued: queued.length, finished: finished.length };
});

export type ActivityTone = "idle" | "run" | "wait" | "review" | "paused" | "done" | "fail";
export type ActivityView = { tone: ActivityTone; label: string; detail: string };

/**
 * 「启动之后在做什么」——一律用文字说明，不给进度百分比。
 * 视频研判本身不确定：读完了可能是无检出，也可能要人工确认，进度条只会假装确定。
 */
export const activityView = computed<ActivityView>(() => {
  if (wb.emptyMode) {
    return { tone: "idle", label: "尚未启动", detail: "添加目标要素、设定时间范围后，才会开始读录像。" };
  }
  if (wb.roundStatus === "paused") {
    return { tone: "paused", label: "已暂停", detail: "已读完的片段与证据都保留；恢复后从下一段录像继续。" };
  }
  if (wb.roundStatus === "done") {
    return { tone: "done", label: "本轮已结束", detail: "结论与审核记录留在历史里；要再查一轮，请新建轮次。" };
  }

  const list = visibleJobs.value;
  const name = (job: Job) => job.cameraName || jobClip(job)?.camera_name || job.cameraId || "未登记镜头";
  const review = list.filter(jobNeedsReview);
  const running = list.filter((job) => RUNNING_ORDER.includes(job.status));
  const failed = list.filter((job) => job.status === "failed");
  const queued = list.filter((job) => job.status === "queued");

  if (review.length) {
    const job = review[0]!;
    return {
      tone: "review",
      label: `${review.length} 段等待人工审核`,
      detail: `先看「${name(job)}」${job.from}–${job.to} 这一段：确认后进候选，排除后不再计入。`,
    };
  }
  if (wb.roundStatus === "review") {
    return { tone: "review", label: "本轮进入人工审核", detail: "模型已经跑完，剩下的都是要人看的片段；逐段确认或排除后再结束本轮。" };
  }
  if (running.length) {
    const job = running[0]!;
    const others = running.length - 1;
    return {
      tone: "run",
      label: `正在分析「${name(job)}」`,
      detail: `${job.from}–${job.to} · ${JOB_STATUS_ACTION[job.status]}${others > 0 ? `；另有 ${others} 段同时在读` : ""}。`,
    };
  }
  if (failed.length) {
    return { tone: "fail", label: `${failed.length} 段读取中断`, detail: "录像可能损坏或被占用；可在卡片里重新排队。" };
  }
  if (queued.length) {
    return { tone: "wait", label: `${queued.length} 段排队中`, detail: "前一段读完后自动开始，不需要手动推进。" };
  }
  return { tone: "idle", label: "已启动，等待录像", detail: "去「录像资源」添加视频或挂载目录，扫描到的片段会按时间顺序进入分析队列。" };
});

/**
 * 候选结果 = 已经分析过的视频统计（不是原始录像清单）：
 * 只统计跑完的作业（已确认/无检出/待审核/失败），按摄像头汇总。
 */
export const analyzedView = computed(() => {
  const list = visibleJobs.value;
  const analyzed = list.filter((job) => job.status !== "queued" && job.status !== "reading");
  const detected = analyzed.filter((job) => job.status === "done");
  const nodetect = analyzed.filter((job) => job.status === "nodetect");
  const review = analyzed.filter(jobNeedsReview);
  const failed = analyzed.filter((job) => job.status === "failed");
  const candidates = analyzed.reduce((sum, job) => sum + job.candidates, 0);

  const rows = new Map<string, { cameraId: string; cameraName: string; analyzed: number; detected: number; review: number; candidates: number }>();
  for (const job of analyzed) {
    const key = job.cameraId || "unknown";
    const row = rows.get(key) ?? {
      cameraId: key,
      cameraName: job.cameraName || jobClip(job)?.camera_name || key || "未登记镜头",
      analyzed: 0,
      detected: 0,
      review: 0,
      candidates: 0,
    };
    row.analyzed += 1;
    if (job.status === "done") row.detected += 1;
    if (jobNeedsReview(job)) row.review += 1;
    row.candidates += job.candidates;
    rows.set(key, row);
  }

  return {
    analyzed: analyzed.length,
    detected: detected.length,
    nodetect: nodetect.length,
    review: review.length,
    failed: failed.length,
    candidates,
    cameras: [...rows.values()].sort((a, b) => b.analyzed - a.analyzed || a.cameraName.localeCompare(b.cameraName)),
  };
});

/** 所有已登记目录是否都已开启自动扫描（左栏「自动分析」开关的状态） */
export const allAutoScan = computed(
  () => recordingSources.value.length > 0 && recordingSources.value.every((source) => source.auto_scan),
);

/** 人工审核弹窗当前要展示的那张卡片；弹窗关掉后返回 null */
export const reviewClipCard = computed<JobCardView | null>(() => {
  const id = ui.reviewClipJobId;
  if (!id) return null;
  return jobCards.value.find((card) => card.job.id === id) ?? null;
});

export const selectedFeature = computed(() => featureList.value.find((f) => f.id === wb.selectedFeatureId) ?? null);

export const indexView = computed(() => {
  const summary = recordingSummary.value;
  const sources = recordingSources.value;
  const files = summary?.file_count ?? 0;
  const parsed = summary?.time_count ?? 0;
  const mapped = summary?.matched_count ?? 0;
  const scanning = summary?.scanning ?? false;
  return {
    dir: sources.length ? sources.map((source) => source.label).join("、") : null,
    status: !wb.caseId
      ? "未选择案件"
      : scanning
        ? "扫描中"
        : sources.length
          ? summary?.last_scan_at
            ? "已扫描"
            : "待扫描"
          : "未添加录像目录",
    progress: !wb.caseId ? 0 : files ? Math.round((parsed / files) * 100) : 0,
    lastRun: summary?.last_scan_at
      ? new Date(summary.last_scan_at).toLocaleString("zh-CN", { hour12: false })
      : "—",
    files,
    parsed,
    mapped,
    unmapped: Math.max(0, files - mapped),
    errors: 0,
    stages: ["文件枚举", "开头画面 OCR", "名称匹配"],
  };
});

export const roundView = computed(() =>
  wb.emptyMode
    ? { id: "—", directionLabel: "—", anchor: "—", stage: "未启动", progress: 0, queued: 0, done: 0, failed: 0, review: 0 }
    : {
        ...roundInfo,
        progress: wb.roundProgress,
      },
);

export const filteredObservations = computed(() => {
  let list = visibleObservations.value;
  if (wb.timelineRound !== "all") list = list.filter((o) => o.round === wb.timelineRound);
  if (wb.timelineCamera !== "all") list = list.filter((o) => o.cameraId === wb.timelineCamera);
  switch (wb.timelineFilter) {
    case "anchor":
      return list.filter((o) => o.kind === "anchor");
    case "review":
      return list.filter((o) => o.relation === "pending" || o.reliability === "review" || o.relation === "indeterminate");
    case "approved":
      return list.filter((o) => o.reliability === "verified" || o.relation === "confirmed");
    case "excluded":
      return list.filter((o) => o.relation === "excluded");
    default:
      return list;
  }
});

export const timelineCounts = computed(() => {
  const list = visibleObservations.value;
  return {
    all: list.length,
    anchor: list.filter((o) => o.kind === "anchor").length,
    review: list.filter((o) => o.relation === "pending" || o.reliability === "review" || o.relation === "indeterminate").length,
    approved: list.filter((o) => o.reliability === "verified" || o.relation === "confirmed").length,
    excluded: list.filter((o) => o.relation === "excluded").length,
  };
});

const toMinutes = (label: string) => {
  const [h, m] = label.split(":").map((n) => Number.parseInt(n, 10));
  return (h || 0) * 60 + (m || 0);
};

export const startConditions = computed(() => {
  const from = toMinutes(wb.from);
  const to = toMinutes(wb.to);
  const inScope = from < to && from >= 1080 && to <= 1380;
  return [
    { id: "source", label: "服务端录像目录可访问", ok: !wb.emptyMode },
    { id: "anchor", label: "已选有效起始锚点", ok: Boolean(wb.selectedAnchorId) && !wb.emptyMode },
    { id: "direction", label: "已选择研判方向", ok: Boolean(wb.direction) },
    { id: "scope", label: "时间范围在授权范围内", ok: inScope },
  ];
});

/* ------------------------------ 弹窗 / 提示 ------------------------------ */

let toastSeq = 1;

export function toast(text: string, tone: ToastTone = "info") {
  toastSeq += 1;
  const item: Toast = { id: toastSeq, text, tone };
  ui.toasts.push(item);
  window.setTimeout(() => {
    ui.toasts = ui.toasts.filter((t) => t.id !== item.id);
  }, 3600);
}

/* ------------------------------ 案件：读取 / 新建 / 删除 ------------------------------ */

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

const CASE_STORAGE_KEY = "trajnexa.currentCaseId";

function storedCaseId(): string | null {
  try {
    return window.localStorage.getItem(CASE_STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeCaseId(caseId: string | null) {
  try {
    if (caseId) window.localStorage.setItem(CASE_STORAGE_KEY, caseId);
    else window.localStorage.removeItem(CASE_STORAGE_KEY);
  } catch {
    /* 隐私模式下忽略 */
  }
}

const THUMB_STORAGE_KEY = "trajnexa.dock.thumbMode";

/** 卡片缩略显示方式跨会话记住：习惯看视频的不用每次重新点 */
function storedThumbMode(): DockThumbMode {
  try {
    return window.localStorage.getItem(THUMB_STORAGE_KEY) === "video" ? "video" : "image";
  } catch {
    return "image";
  }
}

function storeThumbMode(mode: DockThumbMode) {
  try {
    window.localStorage.setItem(THUMB_STORAGE_KEY, mode);
  } catch {
    /* 隐私模式下忽略 */
  }
}

/** 拉取案件列表；selectIfNone 只在首次进入页面时自动选中上次/最近的案件 */
export async function loadCases(selectIfNone = false) {
  caseLoading.value = true;
  caseError.value = null;
  try {
    caseList.value = await mainApi.listCases();
    if (wb.caseId && !caseList.value.some((item) => item.id === wb.caseId)) {
      // 当前案件已在别处被删除，回到未选择状态
      wb.caseId = null;
      storeCaseId(null);
    }
    if (selectIfNone && !wb.caseId && caseList.value.length) {
      const preferred = caseList.value.find((item) => item.id === storedCaseId()) ?? caseList.value[0];
      actions.selectCase(preferred.id, false);
    }
    if (!caseList.value.length) storeCaseId(null);
  } catch (error) {
    caseError.value = errorText(error);
  } finally {
    caseLoading.value = false;
  }
}

export async function loadRecordings(caseId: string | null = wb.caseId) {
  if (!caseId) {
    recordingSources.value = [];
    recordingSummary.value = null;
    recordingClips.value = [];
    return;
  }
  recordingLoading.value = true;
  recordingError.value = null;
  try {
    const [sources, summary, clips] = await Promise.all([
      mainApi.listRecordingSources(caseId),
      mainApi.recordingSummary(caseId),
      mainApi.listRecordingClips(caseId),
    ]);
    if (wb.caseId !== caseId) return; // 已切到别的案件，丢弃旧案件的结果
    recordingSources.value = sources;
    recordingSummary.value = summary;
    recordingClips.value = clips;
  } catch (error) {
    recordingError.value = errorText(error);
  } finally {
    recordingLoading.value = false;
  }
}

export async function createCase(name: string, description = ""): Promise<CaseItem | null> {
  if (caseWorking.value) return null;
  caseWorking.value = true;
  caseError.value = null;
  try {
    const created = await mainApi.createCase(name.trim(), description.trim());
    // 新建案件一律从空任务态开始：没有要素、没有录像目录，镜头默认全灰。
    caseWorkspaces.set(created.id, emptyWorkspace());
    caseList.value = [created, ...caseList.value];
    actions.selectCase(created.id, false);
    toast(`案件「${created.name}」已创建；上传的要素文件将归属该案件`, "ok");
    return created;
  } catch (error) {
    caseError.value = errorText(error);
    toast(caseError.value, "danger");
    return null;
  } finally {
    caseWorking.value = false;
  }
}

/** 删除案件：后端只清理该案件上传的要素文件，录像资源保持不动 */
export async function deleteCase(caseId: string): Promise<CaseDeleteResult | null> {
  if (caseWorking.value) return null;
  const target = caseList.value.find((item) => item.id === caseId);
  if (!target) return null;
  caseWorking.value = true;
  caseError.value = null;
  try {
    const result = await mainApi.deleteCase(caseId);
    const wasCurrent = wb.caseId === caseId;
    caseList.value = caseList.value.filter((item) => item.id !== caseId);
    caseWorkspaces.delete(caseId);
    if (wasCurrent) {
      wb.caseId = null;
      storeCaseId(null);
      recordingSources.value = [];
      recordingSummary.value = null;
      recordingClips.value = [];
      const next = caseList.value[0];
      if (next) actions.selectCase(next.id, false);
      // 没有案件了就回到空任务态
      else applyWorkspace(emptyWorkspace());
    }
    const cleaned = result.media_files_removed
      ? `，已清理 ${result.media_files_removed} 个要素文件`
      : "，该案件没有上传的要素文件";
    toast(`案件「${result.name}」已删除${cleaned}；录像资源未做任何改动`, "ok");
    return result;
  } catch (error) {
    caseError.value = errorText(error);
    toast(caseError.value, "danger");
    return null;
  } finally {
    caseWorking.value = false;
  }
}

/* ------------------------------ 添加 / 编辑要素 ------------------------------ */

export type ElementAttributeInput = { key: string; value: string; unknown: boolean };

export type ElementPayload = {
  /** 画面角标日期，默认案发日；OCR 解析出日期时用识别值 */
  date: string;
  timeLabel: string;
  timeUnknown: boolean;
  cameraId: string | null;
  cameraLabel: string;
  /** 添加要素只收截图：不再有视频来源，也不会因为添加要素而点亮地图镜头。 */
  status: TargetElement["status"];
  statusLabel: string;
  note: string;
  tone: TargetElement["tone"];
  quality: TargetElement["quality"];
  category: NonNullable<TargetElement["category"]>;
  attributes: ElementAttributeInput[];
  boxes: NonNullable<TargetElement["boxes"]>;
  sourceMediaId?: string | null;
  m4ObservationId?: string | null;
};

/** 表单级校验：任一条不满足就不写库，错误文案直接回给弹窗 */
export function validateElementPayload(payload: ElementPayload): string | null {
  if (!payload.timeUnknown && !/^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(payload.timeLabel.trim())) {
    return "画面角标时间格式应为 HH:MM:SS，或标记为「时间未知」";
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(payload.date)) return "日期格式应为 YYYY-MM-DD";
  if (payload.status === "confirmed" && !payload.cameraId) {
    return "「来源已确认」需要绑定镜头目录；否则请保存为候选来源或仅确定区域";
  }
  if (payload.note.length > 200) return "备注不超过 200 字";
  return null;
}

function buildElement(id: string, payload: ElementPayload): TargetElement {
  return {
    id,
    timeLabel: payload.timeUnknown ? "时间未知" : payload.timeLabel.trim(),
    time: payload.timeUnknown ? `${payload.date} 时间未知` : `${payload.date} ${payload.timeLabel.trim()}`,
    cameraId: payload.cameraId,
    cameraLabel: payload.cameraLabel,
    status: payload.status,
    statusLabel: payload.statusLabel,
    note: payload.note.trim(),
    tone: payload.tone,
    quality: payload.quality,
    category: payload.category,
    timeUnknown: payload.timeUnknown,
    origin: "user",
    attributes: payload.attributes.map((attr) => ({
      key: attr.key,
      value: attr.unknown ? "" : attr.value.trim(),
      unknown: attr.unknown,
    })),
    boxes: payload.boxes.map((box) => ({ ...box })),
    sourceMediaId: payload.sourceMediaId ?? null,
    m4ObservationId: payload.m4ObservationId ?? null,
  };
}

/** 锚点观测：时间轴以「时间 + 摄像头名称」为主，内部 id 不进入展示文案。 */
function buildAnchorObservation(element: TargetElement): Observation {
  return {
    id: `O-${element.id}`,
    timeLabel: element.timeLabel,
    kind: "anchor",
    cameraId: element.cameraId,
    title: "起始锚点 · 用户添加要素",
    sub: `${element.cameraLabel} · ${element.statusLabel}`,
    reliability: null,
    relation: null,
    round: roundInfo.id,
    elementId: element.id,
  };
}

/** 把要素录入的属性重算为特征候选：不确定值不进特征，已核验值不被覆盖 */
function applyAttributes(elementId: string, payload: ElementPayload) {
  featureList.value = featureList.value.filter((feature) => feature.elementId !== elementId);
  const created: TargetFeature[] = [];
  payload.attributes.forEach((attr, index) => {
    const value = attr.value.trim();
    if (!value && !attr.unknown) return;
    const unknown = attr.unknown || !value;
    created.push({
      id: `F-${elementId}-${index + 1}`,
      elementId,
      attr: attr.key,
      value: unknown ? "未知" : value,
      status: unknown ? "unknown" : "proposed",
      statusLabel: unknown ? "未知（人工标注）" : "人工录入 · 待核验",
      meta: "人工录入",
      sources: 1,
      window: payload.timeUnknown ? "—" : payload.timeLabel,
    });
  });
  featureList.value.push(...created);
  featureStats.pending = featureList.value.filter(
    (feature) => feature.status === "review" || feature.status === "proposed",
  ).length;
  featureStats.conflicts = featureList.value.filter((feature) => feature.conflict).length;
}

export const actions = {
  openCases() {
    ui.caseManageOpen = true;
    void loadCases();
  },
  /** 切换当前案件：每个案件保留自己的工作区；没打开过的案件一律是空任务态 */
  selectCase(caseId: string, announce = true) {
    const target = caseList.value.find((item) => item.id === caseId);
    if (!target || wb.caseId === caseId) return;
    if (wb.caseId) caseWorkspaces.set(wb.caseId, captureWorkspace());
    wb.caseId = caseId;
    storeCaseId(caseId);
    applyWorkspace(caseWorkspaces.get(caseId) ?? emptyWorkspace());
    void loadRecordings(caseId);
    if (announce) toast(`已切换到案件「${target.name}」`, "info");
  },
  selectCamera(id: string | null) {
    wb.selectedCameraId = id;
  },
  selectAnchor(id: string | null) {
    wb.selectedAnchorId = id;
    wb.selectedElementId = id;
    const element = elementList.value.find((e) => e.id === id);
    if (element?.cameraId) wb.selectedCameraId = element.cameraId;
  },
  selectObservation(id: string) {
    const obs = observationList.value.find((o) => o.id === id);
    if (!obs) return;
    wb.selectedObservationId = id;
    if (obs.cameraId) wb.selectedCameraId = obs.cameraId;
    if (obs.kind === "anchor" && obs.elementId) {
      wb.selectedAnchorId = obs.elementId;
      wb.selectedElementId = obs.elementId;
    }
  },
  selectJob(id: string) {
    wb.selectedJobId = id;
    const job = jobList.value.find((j) => j.id === id);
    if (job) wb.selectedCameraId = job.cameraId;
  },
  setDirection(direction: Direction) {
    wb.direction = direction;
  },
  applyPreset(id: string) {
    const preset = timeWindowPresets.find((p) => p.id === id);
    if (!preset) return;
    timeWindow.start = preset.start;
    timeWindow.end = preset.end;
    timeWindow.preset = preset.id;
  },
  setWindow(start: number, end: number) {
    timeWindow.start = start;
    timeWindow.end = end;
    timeWindow.preset = "custom";
  },
  setRange(from: string, to: string) {
    wb.from = from;
    wb.to = to;
  },
  startRound() {
    if (!startConditions.value.every((c) => c.ok)) {
      toast("启动条件未满足：请检查要素、方向与授权范围", "warn");
      return;
    }
    wb.roundStatus = "running";
    wb.roundProgress = Math.max(4, wb.roundProgress);
    toast("新一轮研判已登记；分析执行尚未接线，进度不会自增", "info");
  },
  pauseRound() {
    if (wb.roundStatus !== "running") return;
    wb.roundStatus = "paused";
    toast("已暂停本轮研判；后台已完成的证据保留", "warn");
  },
  resumeRound() {
    if (wb.roundStatus !== "paused") return;
    wb.roundStatus = "running";
    toast("已恢复本轮研判", "ok");
  },
  stopRound() {
    if (wb.roundStatus === "review" || wb.roundStatus === "done") return;
    wb.roundStatus = "done";
    toast("本轮已人工终止；历史证据与审核记录保留", "warn");
  },
  openCoverage(cameraId?: string) {
    ui.coverageOpen = true;
    ui.coverageCameraId = cameraId ?? wb.selectedCameraId;
    if (cameraId) wb.selectedCameraId = cameraId;
  },
  /**
   * 「添加要素」= 先弹系统选图框，选中后立即打开「添加目标要素」并自动识别。
   * 编辑已有要素（传 elementId）只是打开弹窗，不再选图。
   */
  async openAddElement(elementId?: string) {
    ui.editingElementId = elementId ?? null;
    if (elementId) {
      ui.addElementOpen = true;
      return;
    }
    if (!wb.caseId) {
      toast("请先新建或选择案件，再添加目标要素", "warn");
      ui.addElementOpen = true;
      return;
    }
    // 必须在点击手势内同步调用，否则浏览器会拦截文件对话框
    const file = await pickImageFile();
    if (!file) return;
    pendingElementFile.value = file;
    ui.addElementOpen = true;
  },
  openElements() {
    ui.elementsOpen = true;
  },
  openFeature(id?: string) {
    if (id) wb.selectedFeatureId = id;
    if (!wb.selectedFeatureId) return;
    ui.featureDetailOpen = true;
  },
  openCorrection() {
    ui.correctionOpen = true;
  },
  dismissNewCandidates() {
    wb.newCandidates = 0;
  },
  resolveFeature(action: "confirm" | "unknown" | "reject" | "revoke") {
    const feature = featureList.value.find((f) => f.id === wb.selectedFeatureId);
    if (!feature) return;
    if (action === "confirm") {
      feature.status = "verified";
      feature.statusLabel = "人工已核验";
      feature.meta = "人工核验 · 刚刚";
      toast("已确认该特征，来源证据保持不变", "ok");
    } else if (action === "unknown") {
      feature.status = "unknown";
      feature.statusLabel = "未知";
      feature.meta = "人工标为未知";
      toast("已标为未知；不会自动猜测属性", "warn");
    } else if (action === "reject") {
      feature.status = "unknown";
      feature.statusLabel = "已驳回";
      feature.meta = "驳回机器提议 · 保留原始提取";
      toast("已驳回机器提议，原始提取结果仍可追溯", "warn");
    } else {
      feature.status = "proposed";
      feature.statusLabel = "机器提议";
      feature.meta = "已撤销人工核验";
      toast("已撤销人工核验，恢复为待复核提议", "info");
    }
  },
  saveElement(payload: ElementPayload) {
    const error = validateElementPayload(payload);
    if (error) {
      toast(error, "warn");
      return null;
    }
    const id = `E${String(ui.nextElementSeq).padStart(2, "0")}`;
    ui.nextElementSeq += 1;
    // 空任务里添加第一个要素后，工作台不再停留在空态，时间线/特征栏随即出现该要素
    if (wb.emptyMode) wb.emptyMode = false;
    const element = buildElement(id, payload);
    elementList.value.push(element);
    observationList.value.push(buildAnchorObservation(element));
    observationList.value.sort((a, b) => a.timeLabel.localeCompare(b.timeLabel));
    applyAttributes(id, payload);
    wb.selectedElementId = id;
    wb.selectedAnchorId = id;
    wb.selectedObservationId = `O-${id}`;
    if (payload.cameraId) wb.selectedCameraId = payload.cameraId;
    featureStats.refs += 1;
    const proposals = payload.attributes.filter((attr) => !attr.unknown && attr.value.trim()).length;
    const unknowns = payload.attributes.filter((attr) => attr.unknown).length;
    toast(
      proposals + unknowns > 0
        ? `要素已保存：${proposals} 项特征候选进入待核查${unknowns ? `，${unknowns} 项标为未知` : ""}`
        : "要素已保存，未录入可观察属性",
      "ok",
    );
    return id;
  },
  updateElement(elementId: string, payload: ElementPayload) {
    const error = validateElementPayload(payload);
    if (error) {
      toast(error, "warn");
      return null;
    }
    const index = elementList.value.findIndex((item) => item.id === elementId);
    if (index < 0) {
      toast("该要素不存在，可能已被删除", "danger");
      return null;
    }
    const element = buildElement(elementId, payload);
    elementList.value[index] = element;

    const anchor = observationList.value.find((item) => item.elementId === elementId && item.kind === "anchor");
    if (anchor) {
      anchor.timeLabel = element.timeLabel;
      anchor.cameraId = element.cameraId;
      anchor.sub = `${element.cameraLabel} · ${element.statusLabel}`;
    }
    observationList.value.sort((a, b) => a.timeLabel.localeCompare(b.timeLabel));
    applyAttributes(elementId, payload);
    wb.selectedElementId = elementId;
    toast("要素已更新；特征候选按最新录入重算", "ok");
    return elementId;
  },
  togglePreview(on?: boolean) {
    wb.previewOn = on ?? !wb.previewOn;
  },
  switchScene(empty: boolean) {
    // 演示数据已移除：无论哪种入口，工作台都从空任务态开始。
    void empty;
    applyWorkspace(emptyWorkspace());
  },
  /* ------------------------------ 录像资源 ------------------------------ */

  async addRecordingDir(path: string) {
    if (!wb.caseId) {
      toast("请先在「案件」中选择或新建案件，再添加录像目录", "warn");
      return null;
    }
    recordingBusy.value = true;
    try {
      const source = await mainApi.addRecordingSource(wb.caseId, path);
      await loadRecordings(wb.caseId);
      toast(`已添加录像目录「${source.label}」，正在读开头画面识别名称与开始时间`, "ok");
      await actions.scanRecording(source.id);
      return source;
    } catch (error) {
      toast(errorText(error), "danger");
      return null;
    } finally {
      recordingBusy.value = false;
    }
  },
  async toggleRecordingAutoScan(sourceId: string, autoScan: boolean) {
    recordingBusy.value = true;
    try {
      await mainApi.setRecordingAutoScan(sourceId, autoScan);
      await loadRecordings(wb.caseId);
      toast(autoScan ? "已开启自动扫描：新增录像会按间隔自动识别" : "已关闭自动扫描", "info");
    } catch (error) {
      toast(errorText(error), "danger");
    } finally {
      recordingBusy.value = false;
    }
  },
  async scanRecording(sourceId: string) {
    try {
      const result = await mainApi.scanRecordingSource(sourceId);
      await loadRecordings(wb.caseId);
      toast(result.already_running ? "该目录正在扫描，稍后刷新即可" : "已开始扫描目录开头画面", "info");
      return result;
    } catch (error) {
      toast(errorText(error), "danger");
      return null;
    }
  },
  async removeRecordingDir(sourceId: string) {
    recordingBusy.value = true;
    try {
      const result = await mainApi.removeRecordingSource(sourceId);
      await loadRecordings(wb.caseId);
      toast(result.message, "ok");
    } catch (error) {
      toast(errorText(error), "danger");
    } finally {
      recordingBusy.value = false;
    }
  },

  /** 打开录像目录选择器：视频 / 目录两种用途只影响弹窗文案，登记的都是目录 */
  openRecordingPicker(mode: RecordingDirMode = "video") {
    if (!wb.caseId) {
      toast("请先在「案件」里选择或新建案件，再添加录像", "warn");
      return;
    }
    ui.recordingDirMode = mode;
    ui.recordingDirOpen = true;
  },

  /** 左栏「开启分析」：对还没在扫描的目录逐个发起扫描/解析 */
  async startAllScans() {
    if (!wb.caseId) {
      toast("请先选择案件，再开启分析", "warn");
      return;
    }
    const pending = recordingSources.value.filter((source) => !source.scanning);
    if (!pending.length) {
      toast(recordingSources.value.length ? "所有目录都在扫描中，稍后刷新即可" : "还没有录像目录，先添加视频或挂载目录", "warn");
      return;
    }
    for (const source of pending) {
      try {
        await mainApi.scanRecordingSource(source.id);
      } catch (error) {
        toast(errorText(error), "danger");
        return;
      }
    }
    await loadRecordings(wb.caseId);
    toast(`已对 ${pending.length} 个目录开启解析；模型分析服务尚未接入，这里只做扫描与索引`, "info");
  },

  /** 左栏「自动分析」：一次性打开/关闭所有目录的自动扫描 */
  async setAllAutoScan(auto: boolean) {
    if (!wb.caseId || !recordingSources.value.length) {
      toast("还没有录像目录，先添加视频或挂载目录", "warn");
      return;
    }
    recordingBusy.value = true;
    try {
      for (const source of recordingSources.value) {
        if (source.auto_scan === auto) continue;
        await mainApi.setRecordingAutoScan(source.id, auto);
      }
      await loadRecordings(wb.caseId);
      toast(auto ? "已开启自动分析：新增录像会按间隔自动解析" : "已关闭自动分析", "info");
    } catch (error) {
      toast(errorText(error), "danger");
    } finally {
      recordingBusy.value = false;
    }
  },

  /** 录像资源：人工核对识别名称，保存后立即按台账重新匹配摄像头（只改扫描结果，不动录像文件） */
  async setRecordingClipName(clipId: string, name: string, note?: string) {
    recordingBusy.value = true;
    try {
      const clip = await mainApi.setRecordingClipName(clipId, name, note);
      await loadRecordings(wb.caseId);
      if (!name.trim()) toast("已清空该片段的识别名称", "info");
      else if (clip.camera_uuid) toast("已匹配摄像头：" + (clip.camera_name || ""), "ok");
      else toast("名称已保存，但台账里没有唯一对应的摄像头，已记为未匹配", "warn");
      return clip;
    } catch (error) {
      toast(errorText(error), "danger");
      return null;
    } finally {
      recordingBusy.value = false;
    }
  },

  /** 录像资源：用当前台账重新匹配摄像头，保留人工审核结论 */
  async rematchRecordingSource(sourceId: string) {
    recordingBusy.value = true;
    try {
      const result = await mainApi.rematchRecordingSource(sourceId);
      await loadRecordings(wb.caseId);
      toast("重新匹配完成：" + result.updated + " 更新 · " + result.unchanged + " 未变 · 保留人工 " + result.human_kept, "ok");
      return result;
    } catch (error) {
      toast(errorText(error), "danger");
      return null;
    } finally {
      recordingBusy.value = false;
    }
  },

  setDockThumbMode(mode: DockThumbMode) {
    ui.dockThumbMode = mode;
    storeThumbMode(mode);
  },

  /** 点卡片：打开这一段的人工审核弹窗 */
  openReviewClip(jobId: string) {
    const job = jobList.value.find((item) => item.id === jobId);
    if (!job) return;
    wb.selectedJobId = jobId;
    if (job.cameraId) wb.selectedCameraId = job.cameraId;
    ui.reviewClipJobId = jobId;
    ui.reviewClipOpen = true;
  },

  closeReviewClip() {
    ui.reviewClipOpen = false;
  },

  /**
   * 记录人工审核结论：确认 → 进候选结果；排除 → 不计入候选。
   * 结论目前只写在前端作业列表里，后端审核接口接入后由这里回写。
   */
  reviewJob(jobId: string, verdict: "accept" | "exclude", note = "") {
    const job = jobList.value.find((item) => item.id === jobId);
    if (!job) return;
    job.needsReview = false;
    job.reviewNote = note.trim() || undefined;
    if (verdict === "accept") {
      job.status = "done";
      job.statusLabel = "人工已确认";
      job.candidates = Math.max(1, job.candidates);
      job.progress = 100;
      toast("已确认该片段为候选；结论记在本轮审核记录里", "ok");
    } else {
      job.status = "done";
      job.statusLabel = "人工已排除";
      job.candidates = 0;
      toast("已排除该片段；不计入候选结果，录像文件保持不动", "warn");
    }
    ui.reviewClipOpen = false;
  },
};

/* 开发期直达入口：#coverage / ?modal=coverage 直接打开对应界面，便于截图核对 */
export function applyDemoHash() {
  const param = new URLSearchParams(window.location.search).get("modal");
  const hash = window.location.hash.replace("#", "");
  const key = param || hash;
  if (!key) return;
  const map: Record<string, () => void> = {
    coverage: () => (ui.coverageOpen = true),
    cases: () => actions.openCases(),
    element: () => (ui.addElementOpen = true),
    elements: () => (ui.elementsOpen = true),
    feature: () => (ui.featureDetailOpen = true),
    correction: () => (ui.correctionOpen = true),
    empty: () => actions.switchScene(true),
  };
  map[key]?.();
}
