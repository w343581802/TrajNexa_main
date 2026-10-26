import { computed, reactive, ref } from "vue";
import {
  cameras,
  elements as seedElements,
  features as seedFeatures,
  jobs as seedJobs,
  observations as seedObservations,
  indexInfo,
  roundInfo,
  taskMeta,
  timeWindowPresets,
  type Job,
  type Observation,
  type TargetElement,
  type TargetFeature,
  type TimeWindow,
} from "../data/mock";

export type RoundStatus = "draft" | "ready" | "running" | "paused" | "review" | "done";
export type Direction = "backward" | "forward";
export type TimelineFilter = "all" | "anchor" | "review" | "approved" | "excluded";
export type ToastTone = "info" | "ok" | "warn" | "danger";

export type Toast = { id: number; text: string; tone: ToastTone };

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

const seedElementList = clone(seedElements);
const seedFeatureList = clone(seedFeatures);
const seedObservationList = clone(seedObservations);
const seedJobList = clone(seedJobs);

/* ------------------------------ 工作台状态 ------------------------------ */

export const wb = reactive({
  emptyMode: false,
  taskName: taskMeta.name,
  taskId: taskMeta.id,
  authorized: taskMeta.authorized,

  selectedCameraId: "CAM-01" as string | null,
  selectedElementId: "E01" as string | null,
  selectedAnchorId: "E01" as string | null,
  selectedFeatureId: "F-UPPER" as string | null,
  selectedObservationId: "O-03" as string | null,
  selectedJobId: null as string | null,
  selectedIntervalId: null as string | null,

  direction: "backward" as Direction,
  from: "18:00",
  to: "20:12",

  roundStatus: "running" as RoundStatus,
  roundProgress: roundInfo.progress,

  previewOn: false,
  previewJobId: "job-1042" as string | null,

  newCandidates: 2,
  timelineFilter: "all" as TimelineFilter,
  timelineRound: "all",
  timelineCamera: "all",
});

export const layers = reactive({
  cameras: true,
  fov: true,
  anchors: true,
  tracks: true,
  labels: true,
});

export const timeWindow = reactive<TimeWindow & { preset: string }>({
  start: 1080,
  end: 1440,
  preset: "night",
});

export const featureBar = reactive({ collapsed: false });

export const ui = reactive({
  coverageOpen: false,
  coverageCameraId: null as string | null,
  addElementOpen: false,
  editingElementId: null as string | null,
  elementsOpen: false,
  featureDetailOpen: false,
  correctionOpen: false,
  dockCollapsed: false,
  dockHeight: 248,
  toasts: [] as Toast[],
  nextElementSeq: 5,
});

export const elementList = ref<TargetElement[]>(seedElementList);
export const featureList = ref<TargetFeature[]>(seedFeatureList);
export const observationList = ref<Observation[]>(seedObservationList);
export const jobList = ref<Job[]>(seedJobList);
export const featureStats = reactive({ pending: 2, conflicts: 1, refs: 6 });

/* ------------------------------ 派生视图 ------------------------------ */

export const visibleElements = computed(() => (wb.emptyMode ? [] : elementList.value));
export const visibleFeatures = computed(() => (wb.emptyMode ? [] : featureList.value));
export const visibleObservations = computed(() => (wb.emptyMode ? [] : observationList.value));
export const visibleJobs = computed(() => (wb.emptyMode ? [] : jobList.value));

export const selectedCamera = computed(() => cameras.find((c) => c.id === wb.selectedCameraId) ?? null);
export const selectedElement = computed(() => elementList.value.find((e) => e.id === wb.selectedElementId) ?? null);
export const selectedAnchor = computed(() => elementList.value.find((e) => e.id === wb.selectedAnchorId) ?? null);
export const selectedFeature = computed(() => featureList.value.find((f) => f.id === wb.selectedFeatureId) ?? null);
export const selectedJob = computed(() => jobList.value.find((j) => j.id === wb.previewJobId) ?? null);

export const indexView = computed(() =>
  wb.emptyMode
    ? {
        dir: null,
        status: "未选择录像目录",
        progress: 0,
        lastRun: "—",
        files: 0,
        parsed: 0,
        mapped: 0,
        unmapped: 0,
        errors: 0,
        stages: indexInfo.stages,
      }
    : indexInfo,
);

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

export const canStartRound = computed(
  () => startConditions.value.every((c) => c.ok) && (wb.roundStatus === "ready" || wb.roundStatus === "draft" || wb.roundStatus === "paused" || wb.roundStatus === "done"),
);

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

/* ------------------------------ 添加 / 编辑要素 ------------------------------ */

export type ElementAttributeInput = { key: string; value: string; unknown: boolean };

export type ElementPayload = {
  /** 画面角标日期，默认案发日；OCR 解析出日期时用识别值 */
  date: string;
  timeLabel: string;
  timeUnknown: boolean;
  cameraId: string | null;
  cameraLabel: string;
  status: TargetElement["status"];
  statusLabel: string;
  note: string;
  tone: TargetElement["tone"];
  quality: TargetElement["quality"];
  category: NonNullable<TargetElement["category"]>;
  attributes: ElementAttributeInput[];
  boxes: NonNullable<TargetElement["boxes"]>;
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
  };
}

function buildAnchorObservation(element: TargetElement): Observation {
  return {
    id: `O-${element.id}`,
    timeLabel: element.timeLabel,
    kind: "anchor",
    cameraId: element.cameraId,
    title: `起始锚点 ${element.id} · 用户添加要素`,
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
      meta: `要素 ${elementId} · 人工录入`,
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
    tickRound();
    toast("已开始新一轮研判；录像索引保持不变", "ok");
  },
  pauseRound() {
    if (wb.roundStatus !== "running") return;
    wb.roundStatus = "paused";
    stopTicker();
    toast("已暂停本轮研判；后台已完成的证据保留", "warn");
  },
  resumeRound() {
    if (wb.roundStatus !== "paused") return;
    wb.roundStatus = "running";
    tickRound();
    toast("已恢复本轮研判", "ok");
  },
  stopRound() {
    if (wb.roundStatus === "review" || wb.roundStatus === "done") return;
    stopTicker();
    wb.roundStatus = "done";
    toast("本轮已人工终止；历史证据与审核记录保留", "warn");
  },
  openCoverage(cameraId?: string) {
    ui.coverageOpen = true;
    ui.coverageCameraId = cameraId ?? wb.selectedCameraId;
    if (cameraId) wb.selectedCameraId = cameraId;
  },
  openAddElement(elementId?: string) {
    ui.addElementOpen = true;
    ui.editingElementId = elementId ?? null;
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
        ? `${id} 已保存：${proposals} 项特征候选进入待核查${unknowns ? `，${unknowns} 项标为未知` : ""}`
        : `${id} 已保存，未录入可观察属性`,
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
      toast(`要素 ${elementId} 不存在，可能已被删除`, "danger");
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
    toast(`${elementId} 已更新；特征候选按最新录入重算`, "ok");
    return elementId;
  },
  togglePreview(on?: boolean) {
    wb.previewOn = on ?? !wb.previewOn;
  },
  switchScene(empty: boolean) {
    wb.emptyMode = empty;
    featureBar.collapsed = false;
    wb.roundStatus = empty ? "draft" : "running";
    wb.roundProgress = empty ? 0 : roundInfo.progress;
    wb.selectedCameraId = empty ? null : "CAM-01";
    wb.selectedElementId = empty ? null : "E01";
    wb.selectedAnchorId = empty ? null : "E01";
    wb.selectedFeatureId = empty ? null : "F-UPPER";
    wb.selectedObservationId = empty ? null : "O-03";
    ui.nextElementSeq = empty ? 1 : 5;
    elementList.value = empty ? [] : clone(seedElements);
    featureList.value = empty ? [] : clone(seedFeatures);
    observationList.value = empty ? [] : clone(seedObservations);
    jobList.value = empty ? [] : clone(seedJobs);
    featureStats.pending = empty ? 0 : 2;
    featureStats.conflicts = empty ? 0 : 1;
    featureStats.refs = empty ? 0 : 6;
    wb.newCandidates = empty ? 0 : 2;
    stopTicker();
  },
};

/* ------------------------------ 本地模拟的研判推进 ------------------------------ */

let ticker: number | undefined;

function stopTicker() {
  if (ticker !== undefined) {
    window.clearInterval(ticker);
    ticker = undefined;
  }
}

function tickRound() {
  stopTicker();
  ticker = window.setInterval(() => {
    if (wb.roundStatus !== "running") {
      stopTicker();
      return;
    }
    wb.roundProgress = Math.min(100, wb.roundProgress + 1);
    if (wb.roundProgress >= 100) {
      stopTicker();
      wb.roundStatus = "review";
      toast("本轮分析完成，新增候选待人工复核", "info");
    }
  }, 900);
}

/* 开发期演示入口：#coverage / ?modal=coverage 直接打开对应界面，便于截图核对 */
export function applyDemoHash() {
  const param = new URLSearchParams(window.location.search).get("modal");
  const hash = window.location.hash.replace("#", "");
  const key = param || hash;
  if (!key) return;
  const map: Record<string, () => void> = {
    coverage: () => (ui.coverageOpen = true),
    element: () => (ui.addElementOpen = true),
    elements: () => (ui.elementsOpen = true),
    feature: () => (ui.featureDetailOpen = true),
    correction: () => (ui.correctionOpen = true),
    empty: () => actions.switchScene(true),
  };
  map[key]?.();
}
