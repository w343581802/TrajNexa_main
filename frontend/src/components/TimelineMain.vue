<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { candidateEvidenceUrl, keyframeUrl, mainApi, proxyVideoUrl, type TimelineCard, type TimelineViewCard, type TimelineViewRegion } from "../services/mainApi";
import { demoConfirmedCards, DEMO_LABEL } from "../data/timelineDemo";
import {
  actions,
  timelineCards,
  timelineView,
  timelineViewError,
  timelineViewLoading,
  toast,
  wb,
} from "../store/workbench";

type ViewFilter = "all" | "confirmed" | "region";

const filter = ref<ViewFilter>("all");
const detailId = ref<string | null>(null);
const focusedCandidateId = ref<string | null>(null);
const playbackCardId = ref<string | null>(null);
const playbackUrl = ref<string | null>(null);
const playbackSeek = ref(0);
const playbackVideo = ref<HTMLVideoElement | null>(null);
const reviewBusy = ref(false);

const DRAWER_WIDTH_KEY = "trajnexa.timeline.drawerWidth";
const DRAWER_MIN = 360;
const DRAWER_MAX = 760;

function loadDrawerWidth(): number {
  try {
    const raw = localStorage.getItem(DRAWER_WIDTH_KEY);
    // 未保存时 getItem 返回 null，Number(null) 是 0，不能当成有效宽度。
    if (raw !== null && raw.trim() !== "") {
      const saved = Number(raw);
      if (Number.isFinite(saved)) return Math.min(DRAWER_MAX, Math.max(DRAWER_MIN, saved));
    }
  } catch { /* 隐私模式下不可用，使用默认值 */ }
  return 420;
}
const drawerWidth = ref(loadDrawerWidth());
const drawerTop = ref(94);
const evidencePreview = ref<PendingGroup[]>([]);

const view = computed(() => timelineView.value);

/** 仅开发环境、且该目标没有真实已确认轨迹时，追加明确标注的演示卡。 */
const demoData = computed(() => {
  const payload = view.value;
  if (!payload || payload.counts.confirmed > 0) return { cards: [], techCards: [], active: false };
  return demoConfirmedCards(payload.caseId, payload.targetId);
});
const demoCards = computed(() => demoData.value.cards);
const allViewCards = computed(() => {
  const rows = [...(view.value?.cards ?? []), ...demoCards.value];
  return rows
    .map((card, index) => ({ card, index, time: card.observedAt ? Date.parse(card.observedAt) : Number.NaN }))
    .sort((a, b) => {
      const at = Number.isFinite(a.time) ? a.time : Number.POSITIVE_INFINITY;
      const bt = Number.isFinite(b.time) ? b.time : Number.POSITIVE_INFINITY;
      return at === bt ? a.index - b.index : at - bt;
    })
    .map((row) => row.card);
});

const FILTERS: { id: ViewFilter; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "confirmed", label: "已确认轨迹" },
  { id: "region", label: "区域排查" },
];

const KIND_LABEL: Record<TimelineViewCard["kind"], string> = {
  confirmed_track: "已确认轨迹",
  candidate_clue: "待核查线索",
  region_investigation: "区域排查",
};

const FILTER_KIND: Record<Exclude<ViewFilter, "all">, TimelineViewCard["kind"]> = {
  confirmed: "confirmed_track",
  region: "region_investigation",
};

/** 主视图暂不展示“待核查线索”卡；候选信息收进区域摄像头行与详情。 */
const cards = computed(() => {
  const rows = allViewCards.value.filter((card) => card.kind !== "candidate_clue");
  if (filter.value === "all") return rows;
  const kind = FILTER_KIND[filter.value];
  return rows.filter((card) => card.kind === kind);
});

const viewCounts = computed(() => {
  const rows = allViewCards.value;
  const confirmed = rows.filter((card) => card.kind === "confirmed_track").length;
  const region = rows.filter((card) => card.kind === "region_investigation").length;
  return { confirmed, region, total: confirmed + region };
});

const detailCard = computed(() => allViewCards.value.find((card) => card.cardId === detailId.value) ?? null);

function techCardsFor(card: TimelineViewCard | null): TimelineCard[] {
  if (!card) return [];
  const rows = card.detailCardIds
    .map((id) => timelineCards.value.find((item) => item.cardId === id))
    .filter((item): item is TimelineCard => Boolean(item));
  if (rows.length) return rows;
  return card.detailCardIds
    .map((id) => demoData.value.techCards.find((item) => item.cardId === id))
    .filter((item): item is TimelineCard => Boolean(item));
}

function isDemoCard(card: TimelineViewCard | null): boolean {
  return Boolean(card?.labels?.includes(DEMO_LABEL));
}

function isDemoCandidate(card: TimelineCard | null): boolean {
  return Boolean(card?.cardId.startsWith("demo-segment-"));
}

const detailTechCards = computed<TimelineCard[]>(() => techCardsFor(detailCard.value));

type CardThumb = { key: string; url: string; alt: string };

/** 已确认轨迹右侧缩略帧：从该段录像连续取三帧，折叠展示；不伪造证据图。 */
function confirmedThumbs(card: TimelineViewCard): CardThumb[] {
  if (card.kind !== "confirmed_track" || !wb.caseId) return [];
  const tech = techCardsFor(card).find((item) => item.playback?.clipId
    && item.playback.offsetSeconds !== null && item.playback.offsetSeconds !== undefined);
  const clipId = tech?.playback?.clipId;
  const offset = tech?.playback?.offsetSeconds;
  if (!clipId || offset === null || offset === undefined) return [];
  const seen = new Set<string>();
  const rows: CardThumb[] = [];
  for (const delta of [-1.5, 0, 1.5]) {
    const at = Math.max(0, Number(offset) + delta);
    const key = at.toFixed(1);
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push({ key, url: keyframeUrl(wb.caseId, clipId, at), alt: card.place + " 录像关键帧" });
  }
  return rows;
}

function confirmedFrameTotal(card: TimelineViewCard | null): number {
  return techCardsFor(card).reduce((total, item) => {
    const frames = Number(item.segment?.frames ?? item.evidence?.frames ?? 0);
    return total + (Number.isFinite(frames) ? frames : 0);
  }, 0);
}

function segmentFrameText(card: TimelineCard): string {
  const frames = card.segment?.frames ?? card.evidence?.frames;
  const breakReason = card.segment?.breakReason;
  const framesText = frames === null || frames === undefined ? "帧数未记录" : `${frames} 帧`;
  return breakReason ? `${framesText} · 断点：${String(breakReason)}` : framesText;
}

function segmentWindowText(card: TimelineCard): string {
  const window = card.segment?.window;
  if (!Array.isArray(window) || window.length < 2) return "窗口未记录";
  const start = Number(window[0]);
  const end = Number(window[1]);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return "窗口未记录";
  return `${daySecondsClock(start)}–${daySecondsClock(end)}`;
}

const candidateCards = computed(() => detailTechCards.value.filter((card) => card.kind === "track_segment"));

const focusedCandidate = computed<TimelineCard | null>(() => {
  if (!candidateCards.value.length) return null;
  return candidateCards.value.find((card) => card.cardId === focusedCandidateId.value) ?? candidateCards.value[0];
});

function timeLabel(stamp: string | null): string {
  if (!stamp) return "时间未映射";
  const value = stamp.slice(11, 19);
  return value || stamp;
}

function clockRange(card: TimelineViewCard): string {
  const region = card.region;
  if (card.kind === "region_investigation" && region?.plannedObservationWindow) {
    const win = region.plannedObservationWindow;
    const gap = regionHasGap(region) ? "（不连续）" : "";
    if (win.startAt && win.endAt) return timeLabel(win.startAt) + "–" + timeLabel(win.endAt) + gap;
    if (win.startSeconds !== null) return win.startSeconds.toFixed(0) + "–" + (win.endSeconds ?? 0).toFixed(0) + "s" + gap;
    return "计划时间未确定";
  }
  return timeLabel(card.observedAt);
}

function cardTimeHint(card: TimelineViewCard): string {
  if (!card.observedAt) return "目标事件时间未映射，不按分析时间冒充";
  return card.observedAtSource === "event_time" ? "目标事件时间" : "时间来源未映射";
}

function kindTone(card: TimelineViewCard): "ok" | "warn" | "info" {
  if (card.kind === "confirmed_track") return "ok";
  if (card.kind === "region_investigation") return "info";
  return "warn";
}

function identityText(card: TimelineViewCard): string {
  if (card.identityStatus === "human_confirmed" || card.identityStatus === "confirmed") return "身份已确认";
  if (card.identityStatus === "ambiguous") return "身份歧义";
  if (card.identityStatus === "rejected") return "已排除";
  return "候选目标｜身份未确认";
}

function detailIdentityText(card: TimelineViewCard): string {
  return card.kind === "region_investigation" ? "区域排查" : identityText(card);
}

function detailSpatialText(card: TimelineViewCard): string {
  if (card.kind === "region_investigation") return "仅设备位置，不代表目标到达";
  return "空间：" + (card.spatialQuality || "无地图坐标");
}

function techIdentityText(card: TimelineCard): string {
  if (card.identityStatus === "human_confirmed" || card.identityStatus === "confirmed") return "身份已确认";
  if (card.identityStatus === "ambiguous") return "身份歧义";
  if (card.identityStatus === "rejected") return "已排除";
  return "候选目标｜身份未确认";
}

function regionStatusText(status: string): string {
  const text: Record<string, string> = {
    planned: "待规划",
    queued: "排队中",
    running: "研判中",
    awaiting_review: "待人工审核",
    completed_with_evidence: "已产出候选",
    completed_no_match: "已检查，暂无可靠候选",
    failed: "分析失败",
    cancelled: "已取消",
    budget_exhausted: "预算用尽",
  };
  return text[status] ?? status;
}

function windowPhaseText(phase: string): string {
  const text: Record<string, string> = {
    completed: "已完成",
    running: "分析中",
    queued: "等待处理",
    no_recording: "无可用录像",
    failed: "失败",
    unknown: "状态未知",
  };
  return text[phase] ?? phase;
}

type CameraWindow = { start: number; end: number; startAt: string | null; endAt: string | null };

type PendingGroup = {
  key: string;
  card: TimelineCard;
  cameraLabel: string;
  identity: string;
  targetUrl: string | null;
  fullUrl: string | null;
  hasBbox: boolean;
};

type CameraRow = {
  key: string;
  label: string;
  uuid: string | null;
  windows: CameraWindow[];
  windowCount: number;
  runCount: number;
  candidateCount: number;
  pendingCount: number;
  phase: string;
  clipId: string | null;
  offsetSeconds: number | null;
  pendingGroups: PendingGroup[];
};

function regionCameras(region: TimelineViewRegion): CameraRow[] {
  const rows = new Map<string, CameraRow>();
  for (const task of region.cameraTasks) {
    const key = task.cameraUuid || task.cameraLabel;
    let row = rows.get(key);
    if (!row) {
      row = {
        key,
        label: task.cameraLabel,
        uuid: task.cameraUuid,
        windows: [],
        windowCount: 0,
        runCount: 0,
        candidateCount: 0,
        pendingCount: 0,
        phase: task.phase,
        clipId: task.clipId ?? null,
        offsetSeconds: task.offsetSeconds ?? null,
        pendingGroups: [],
      };
      rows.set(key, row);
    }
    const ranges: { startSeconds: number; endSeconds: number; startAt: string | null; endAt: string | null }[] = task.windowRanges?.length
      ? task.windowRanges
      : task.windowSeconds?.[0] !== undefined && task.windowSeconds?.[1] !== undefined
        ? [{ startSeconds: task.windowSeconds[0], endSeconds: task.windowSeconds[1], startAt: task.plannedStartAt, endAt: task.plannedEndAt }]
        : [];
    for (const range of ranges) {
      if (!row.windows.some((win) => win.start === range.startSeconds && win.end === range.endSeconds)) {
        row.windows.push({ start: range.startSeconds, end: range.endSeconds, startAt: range.startAt, endAt: range.endAt });
      }
    }
    row.windowCount = Math.max(row.windowCount, task.windowCount ?? 0);
    row.runCount = Math.max(row.runCount, task.runIds?.length ?? (task.runId ? 1 : 0));
    row.candidateCount = Math.max(row.candidateCount, task.candidateCount ?? 0);
    row.pendingCount = Math.max(row.pendingCount, task.pendingCandidateCount ?? 0);
    if (task.clipId) row.clipId = task.clipId;
    if (task.offsetSeconds !== null && task.offsetSeconds !== undefined) row.offsetSeconds = task.offsetSeconds;
    if (task.phase === "running") row.phase = "running";
    else if (task.phase === "failed" && row.phase !== "running") row.phase = "failed";
  }
  const result = [...rows.values()];
  for (const row of result) row.pendingGroups = pendingGroupsFor(row);
  return result;
}

function pendingGroupsFor(camera: CameraRow): PendingGroup[] {
  const groups = new Map<string, PendingGroup>();
  for (const card of candidateCards.value) {
    if (card.cameraLabel !== camera.label) continue;
    const identity = card.identityStatus ?? "";
    if (identity === "confirmed" || identity === "human_confirmed" || identity === "rejected") continue;
    const evidence = (card.evidence ?? {}) as Record<string, unknown>;
    const runId = runIdFor(card) ?? (typeof evidence.runId === "string" ? evidence.runId : null);
    const candidateId = candidateIdFor(card);
    const bbox = Array.isArray(evidence.bboxPixels) && evidence.bboxPixels.length === 4;
    // 同一轨迹会被多轮检索重复产出；按 trackId 归并，避免把重复检索当成多组目标。
    const dedupe = (typeof evidence.trackId === "string" && evidence.trackId) ? evidence.trackId : (candidateId ?? card.cardId);
    if (groups.has(dedupe)) continue;
    const fullUrl = runId && candidateId && wb.caseId
      ? candidateEvidenceUrl(wb.caseId, runId, candidateId, "full")
      : null;
    const targetUrl = runId && candidateId && wb.caseId
      ? candidateEvidenceUrl(wb.caseId, runId, candidateId, bbox ? "crop" : "full")
      : null;
    groups.set(dedupe, {
      key: card.cardId,
      card,
      cameraLabel: camera.label,
      identity: techIdentityText(card),
      targetUrl,
      fullUrl,
      hasBbox: bbox,
    });
  }
  return [...groups.values()];
}

type CameraState = "pending" | "running" | "done";

function cameraState(camera: CameraRow): CameraState {
  if (camera.pendingGroups.length || camera.pendingCount > 0) return "pending";
  if (camera.phase === "running" || camera.phase === "queued") return "running";
  return "done";
}

const regionCameraRows = computed<CameraRow[]>(() => {
  const region = detailCard.value?.region;
  return region ? regionCameras(region) : [];
});

const regionCameraCounts = computed(() => {
  const counts = { done: 0, running: 0, pending: 0 };
  for (const camera of regionCameraRows.value) counts[cameraState(camera)] += 1;
  return counts;
});

function cameraStateText(camera: CameraRow): string {
  const state = cameraState(camera);
  if (state === "pending") return "待核查" + (camera.pendingGroups.length ? " " + camera.pendingGroups.length : "");
  if (state === "running") return "分析中";
  return "分析完成";
}

function cameraShotUrl(camera: CameraRow): string | null {
  if (!wb.caseId || !camera.clipId || camera.offsetSeconds === null) return null;
  return keyframeUrl(wb.caseId, camera.clipId, camera.offsetSeconds);
}

function openEvidencePreview(groups: PendingGroup[]): void {
  evidencePreview.value = groups;
}

function closeEvidencePreview(): void {
  evidencePreview.value = [];
}

function saveDrawerWidth(): void {
  try { localStorage.setItem(DRAWER_WIDTH_KEY, String(drawerWidth.value)); } catch { /* 忽略不可写存储 */ }
}

function setDrawerWidth(value: number): void {
  drawerWidth.value = Math.min(DRAWER_MAX, Math.max(DRAWER_MIN, Math.round(value)));
}

function beginDrawerResize(event: PointerEvent): void {
  const startX = event.clientX;
  const startWidth = drawerWidth.value;
  const move = (next: PointerEvent) => setDrawerWidth(startWidth + (startX - next.clientX));
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    document.body.style.userSelect = "";
    saveDrawerWidth();
  };
  document.body.style.userSelect = "none";
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
}

function nudgeDrawer(delta: number): void {
  setDrawerWidth(drawerWidth.value + delta);
  saveDrawerWidth();
}

function resetDrawerWidth(): void {
  setDrawerWidth(420);
  saveDrawerWidth();
}

/** 抽屉 teleport 到 body 后要自己对齐时间线内容区顶部，避免盖住栏头。 */
function syncDrawerTop(): void {
  const host = document.querySelector(".tm");
  if (!host) return;
  drawerTop.value = Math.max(0, Math.round(host.getBoundingClientRect().top));
}

/** 抽屉/大图打开时通知栏级拖拽把手让位，避免在弹层里误拉时间线宽度。 */
watch(detailCard, (card) => {
  document.body.classList.toggle("tm-drawer-open", Boolean(card));
  if (card) syncDrawerTop();
}, { immediate: true });

watch(evidencePreview, (groups) => {
  document.body.classList.toggle("tm-preview-open", groups.length > 0);
}, { immediate: true });

onMounted(() => window.addEventListener("resize", syncDrawerTop));
onBeforeUnmount(() => {
  window.removeEventListener("resize", syncDrawerTop);
  document.body.classList.remove("tm-drawer-open", "tm-preview-open");
});

function daySecondsClock(seconds: number): string {
  const total = Math.round(seconds);
  const hh = String(Math.floor(total / 3600) % 24).padStart(2, "0");
  const mm = String(Math.floor(total / 60) % 60).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return hh + ":" + mm + ":" + ss;
}

function cameraWindowText(window: CameraWindow): string {
  if (window.startAt && window.endAt) return timeLabel(window.startAt) + "–" + timeLabel(window.endAt);
  return daySecondsClock(window.start) + "–" + daySecondsClock(window.end);
}

function regionWindowText(region: TimelineViewRegion): string {
  if (!region.windowRanges.length) return "尚未排出检索窗口";
  const first = region.windowRanges[0];
  const last = region.windowRanges[region.windowRanges.length - 1];
  if (first.startAt && last.endAt) return timeLabel(first.startAt) + "–" + timeLabel(last.endAt);
  return first.startSeconds.toFixed(0) + "–" + last.endSeconds.toFixed(0) + "s";
}

function regionHasGap(region: TimelineViewRegion): boolean {
  return region.hasWindowGap || region.windowRanges.some((range) => !range.contiguousToPrev);
}

function filterTone(id: ViewFilter): "ok" | "warn" | "info" | "all" {
  if (id === "confirmed") return "ok";
  if (id === "region") return "info";
  return "all";
}

function kindIcon(card: TimelineViewCard): "check" | "warning" | "layers" {
  if (card.kind === "confirmed_track") return "check";
  if (card.kind === "region_investigation") return "layers";
  return "warning";
}

function placeIcon(card: TimelineViewCard): "camera" | "pin" | "layers" {
  if (card.kind === "confirmed_track") return "camera";
  if (card.kind === "region_investigation") return "layers";
  return "pin";
}

function regionPending(region: TimelineViewRegion): number {
  return region.phaseCounts.queued + region.phaseCounts.running;
}

function cameraIcon(camera: CameraRow): "check" | "refresh" | "clock" | "warning" {
  // 有未核查候选时，用“待核查线索”的同款警示图标标出这台设备需要人工核查。
  if (camera.pendingCount > 0) return "warning";
  if (camera.phase === "completed") return "check";
  if (camera.phase === "running") return "refresh";
  if (camera.phase === "failed" || camera.phase === "no_recording") return "warning";
  return "clock";
}

function cameraResult(camera: CameraRow): string {
  if (camera.pendingCount > 0) return "待核查 " + camera.pendingCount + " 条";
  if (camera.phase === "running") return "正在分析";
  if (camera.phase === "queued") return "等待处理";
  if (camera.phase === "failed") return "分析失败";
  if (camera.phase === "no_recording") return "无可用录像";
  if (camera.phase === "unknown") return "状态未知";
  return camera.candidateCount ? "发现 " + camera.candidateCount + " 条候选" : "暂无可靠结果";
}

function candidateTrackId(card: TimelineCard): string {
  const evidence = card.evidence as { trackId?: string } | null;
  return evidence?.trackId ?? "未提供";
}

function candidateScore(card: TimelineCard): string {
  const evidence = card.evidence as { retrievalScore?: number; marginTop1Top2?: number } | null;
  if (evidence?.retrievalScore === undefined || evidence.retrievalScore === null) return "未提供";
  const score = Number(evidence.retrievalScore).toFixed(4);
  const margin = evidence.marginTop1Top2 === undefined ? "" : " · margin " + Number(evidence.marginTop1Top2).toFixed(4);
  return score + margin;
}

function evidenceRefs(card: TimelineCard): string[] {
  const evidence = card.evidence as { evidenceRefs?: string[] } | null;
  return evidence?.evidenceRefs ?? [];
}

function runIdFor(card: TimelineCard): string | null {
  const evidence = (card.evidence ?? {}) as Record<string, unknown>;
  if (typeof evidence.runId === "string" && evidence.runId) return evidence.runId;
  for (const ref of evidenceRefs(card)) {
    const match = /tracking_runs[\/]([^\/]+)/.exec(ref);
    if (match) return match[1];
  }
  return null;
}

function candidateIdFor(card: TimelineCard): string | null {
  const evidence = (card.evidence ?? {}) as Record<string, unknown>;
  if (typeof evidence.candidateId === "string" && evidence.candidateId) return evidence.candidateId;
  return card.cardId.startsWith("segment:") ? card.cardId.slice("segment:".length) : null;
}

function selectViewCard(card: TimelineViewCard): void {
  detailId.value = card.cardId;
  evidencePreview.value = [];
  playbackCardId.value = null;
  playbackUrl.value = null;
  focusedCandidateId.value = card.detailCardIds[0] ?? null;
  if (isDemoCard(card)) {
    // 演示卡没有对应的真实技术卡，不进入地图选中流程。
    return;
  }
  if (card.kind === "region_investigation") {
    const oldRegion = timelineCards.value.find((item) =>
      item.kind === "region_investigation" && item.region?.runId && card.region?.runIds.includes(item.region.runId));
    actions.selectTimelineCard(oldRegion?.cardId ?? card.detailCardIds[0] ?? null);
  } else {
    const first = candidateCards.value[0] ?? detailTechCards.value[0];
    actions.selectTimelineCard(first?.cardId ?? card.detailCardIds[0] ?? null);
  }
}

function selectUnderlyingCard(card: TimelineViewCard): string | null {
  if (isDemoCard(card)) return null;
  if (card.kind === "region_investigation") {
    const oldRegion = timelineCards.value.find((item) =>
      item.kind === "region_investigation" && item.region?.runId && card.region?.runIds.includes(item.region.runId));
    return oldRegion?.cardId ?? card.detailCardIds[0] ?? null;
  }
  const tech = techCardsFor(card);
  return (tech.find((item) => item.kind === "track_segment") ?? tech[0])?.cardId ?? card.detailCardIds[0] ?? null;
}

function locateViewCard(card: TimelineViewCard): void {
  const cardId = selectUnderlyingCard(card);
  if (!cardId) {
    toast("演示样本没有可定位的真实地图要素", "info");
    return;
  }
  // 清空后重新选中，确保重复点击“定位”也会触发地图缩放。
  actions.selectTimelineCard(null);
  window.setTimeout(() => actions.selectTimelineCard(cardId), 0);
}

function closeDetail(): void {
  detailId.value = null;
  evidencePreview.value = [];
  playbackCardId.value = null;
  playbackUrl.value = null;
}

function focusCandidate(card: TimelineCard): void {
  focusedCandidateId.value = card.cardId;
  actions.selectTimelineCard(card.cardId);
  playbackCardId.value = null;
  playbackUrl.value = null;
}

function canPlay(card: TimelineCard | null): boolean {
  return Boolean(card?.playback?.clipId && card.playback.offsetSeconds !== null && card.playback.offsetSeconds !== undefined);
}

function openPlayback(card: TimelineCard): void {
  const clipId = card.playback?.clipId;
  const offset = card.playback?.offsetSeconds ?? null;
  if (!clipId || offset === null || !wb.caseId) {
    toast("这张候选卡没有片段内位置，不能跳转播放", "warn");
    return;
  }
  const start = Math.max(0, offset - 30);
  playbackUrl.value = proxyVideoUrl(wb.caseId, clipId, start, 60);
  playbackSeek.value = Math.max(0, offset - start);
  playbackCardId.value = card.cardId;
}

function seekPlayback(): void {
  const video = playbackVideo.value;
  if (!video) return;
  video.currentTime = Math.min(playbackSeek.value, video.duration || playbackSeek.value);
  void video.play().catch(() => { /* 浏览器可能拒绝自动播放 */ });
}

async function review(decision: "approve" | "reject" | "revoke"): Promise<void> {
  const card = focusedCandidate.value;
  const runId = card ? runIdFor(card) : null;
  const candidateId = card ? candidateIdFor(card) : null;
  if (!wb.caseId || !runId || !candidateId) {
    toast("缺少候选所属研判任务，不能提交审核", "warn");
    return;
  }
  reviewBusy.value = true;
  try {
    await mainApi.reviewTrackingCandidate(wb.caseId, runId, { candidate_id: candidateId, decision });
    toast(decision === "approve" ? "已确认候选；空间位置仍需单独审核，不会因此升级为 L2"
      : decision === "reject" ? "已排除该候选" : "已撤销确认", decision === "approve" ? "ok" : "warn");
    await actions.refreshTimeline();
  } catch (error) {
    toast(error instanceof Error ? error.message : "审核失败", "danger");
  } finally {
    reviewBusy.value = false;
  }
}

const earlier = computed(() => view.value?.navigation.earlier ?? null);
const later = computed(() => view.value?.navigation.later ?? null);

watch(() => wb.caseId, () => closeDetail());
watch(() => view.value?.targetId, () => closeDetail());
</script>

<template>
  <div class="tm">
    <div class="tm-scroll">
      <div class="tm-summary">
        <button
          v-for="item in FILTERS"
          :key="item.id"
          class="tm-filter"
          :data-tone="filterTone(item.id)"
          :class="{ 'is-active': filter === item.id }"
          @click="filter = item.id"
        >
          <span v-if="item.id !== 'all'" class="tm-filter-dot" />
          <span class="tm-filter-label">{{ item.label }}</span>
          <b class="num">{{ item.id === "all" ? viewCounts.total : item.id === "confirmed" ? viewCounts.confirmed : viewCounts.region }}</b>
        </button>
      </div>

      <p class="tm-rule">按目标事件时间从早到晚排列；相邻卡片不代表存在连续轨迹。</p>

      <div v-if="demoCards.length" class="tm-demo-banner">
        <AppIcon name="info" :size="13" />
        <span>演示：{{ demoCards.length }} 张“已确认轨迹”为界面样例（轨迹段与帧数为示意），使用真实录像关键帧；身份未经真实审核，不能作为证据。</span>
      </div>

      <div class="tm-nav tm-nav--earlier">
        <div class="tm-nav-head">
          <span class="tm-nav-title">↑ 向前追踪（更早时间）</span>
          <span class="tm-nav-soon" :title="earlier?.disabledReason || '尚未实现'">暂未开放</span>
        </div>
        <span class="tm-nav-text">以最早一条有效目标观测为起点，继续搜索此前录像</span>
      </div>

      <div v-if="timelineViewError" class="tm-error">{{ timelineViewError }}</div>
      <div v-else-if="timelineViewLoading && !view" class="tm-empty">
        <AppIcon name="clock" :size="16" />
        <p>正在读取目标追踪时间线</p>
      </div>
      <div v-else-if="!view" class="tm-empty">
        <AppIcon name="clock" :size="16" />
        <p>该目标还没有可用于主视图的研判记录。</p>
      </div>
      <div v-else-if="!cards.length" class="tm-empty">
        <AppIcon name="target" :size="16" />
        <p>当前筛选下没有卡片。</p>
      </div>

      <ol v-else class="tm-list">
        <li v-for="card in cards" :key="card.cardId" class="tm-row" :data-kind="card.kind">
          <span class="tm-dot" :data-tone="kindTone(card)" />
          <article class="tm-card" :data-kind="card.kind" :class="{ 'is-open': detailId === card.cardId }">
            <div class="tm-card-layout" :class="{ 'has-thumbs': confirmedThumbs(card).length > 0 }">
              <div class="tm-card-content">
                <header class="tm-card-head">
                  <span class="tm-time num">{{ clockRange(card) }}</span>
                  <span v-if="card.kind !== 'confirmed_track'" class="tm-badge" :data-tone="kindTone(card)">
                    <AppIcon :name="kindIcon(card)" :size="11" />
                    {{ KIND_LABEL[card.kind] }}
                  </span>
                  <span v-if="isDemoCard(card)" class="tm-demo-tag">{{ DEMO_LABEL }}</span>
                </header>

                <h3 class="tm-place">
                  <span class="tm-place-icon" :data-tone="kindTone(card)">
                    <AppIcon :name="placeIcon(card)" :size="13" />
                  </span>
                  <span class="tm-place-text">{{ card.place }}</span>
                </h3>

                <template v-if="card.kind === 'region_investigation' && card.region">
                  <div class="tm-region-sum">
                    <span>{{ card.region.deviceCount }} 台摄像头 · {{ card.region.plannedWindows }} 次检索</span>
                    <span class="tm-region-count">已完成 {{ card.region.completedWindows }} · 待处理 {{ regionPending(card.region) }}</span>
                  </div>
                  <ul class="tm-cam-list">
                    <li v-for="camera in regionCameras(card.region)" :key="camera.key" class="tm-cam-item">
                      <button type="button" class="tm-cam" @click="selectViewCard(card)">
                        <span class="tm-cam-state" :data-phase="camera.phase" :data-pending="camera.pendingCount > 0">
                          <AppIcon :name="cameraIcon(camera)" :size="11" />
                        </span>
                        <span class="tm-cam-name">{{ camera.label }}</span>
                        <span class="tm-cam-result" :data-phase="camera.phase">{{ cameraResult(camera) }}</span>
                        <span class="tm-cam-open"><AppIcon name="chevron-right" :size="12" /></span>
                      </button>
                      <span v-if="camera.phase === 'running'" class="tm-cam-bar" aria-hidden="true"><i /></span>
                    </li>
                  </ul>
                  <p v-if="regionHasGap(card.region)" class="tm-gap">
                    <AppIcon name="warning" :size="11" />
                    检索窗口不连续，不代表目标连续经过。
                  </p>
                </template>

                <p v-else class="tm-sub">{{ card.subtitle }}</p>

                <footer class="tm-card-foot">
                  <button class="tm-detail-btn" @click="selectViewCard(card)">
                    查看详情
                    <AppIcon name="chevron-right" :size="12" />
                  </button>
                  <button class="tm-locate-btn" @click="locateViewCard(card)">
                    <AppIcon name="target" :size="12" />
                    定位
                  </button>
                </footer>
                <p v-if="!card.observedAt" class="tm-time-hint">{{ cardTimeHint(card) }}</p>
              </div>

              <div
                v-if="card.kind === 'confirmed_track' && confirmedThumbs(card).length"
                class="tm-thumbs"
                :title="'录像关键帧 ' + confirmedThumbs(card).length + ' 张（请求偏移，未校验 PTS）'"
              >
                <img
                  v-for="(thumb, index) in confirmedThumbs(card)"
                  :key="thumb.key"
                  class="tm-thumb"
                  :src="thumb.url"
                  :alt="thumb.alt"
                  :style="{ '--i': index, zIndex: 6 - index }"
                />
                <span v-if="confirmedThumbs(card).length > 1" class="tm-thumb-count num">{{ confirmedThumbs(card).length }}</span>
              </div>
            </div>
          </article>
        </li>
      </ol>

      <div class="tm-nav tm-nav--later">
        <div class="tm-nav-head">
          <span class="tm-nav-title">↓ 向后追踪（更晚时间）</span>
          <span class="tm-nav-soon" :title="later?.disabledReason || '尚未实现'">暂未开放</span>
        </div>
        <span class="tm-nav-text">从最新有效目标观测继续搜索后续可能出现的区域</span>
      </div>

      <p class="tm-foot-note">{{ view?.note }}</p>
    </div>

    <Teleport to="body">
      <section
        v-if="detailCard"
        class="tm-drawer"
        aria-label="卡片详情"
        :style="{ width: drawerWidth + 'px', top: drawerTop + 'px' }"
      >
      <div
        class="tm-drawer-resize"
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整详情宽度，双击恢复默认"
        tabindex="0"
        title="拖动调整宽度，双击恢复默认"
        @pointerdown.prevent="beginDrawerResize"
        @dblclick="resetDrawerWidth"
        @keydown.left.prevent="nudgeDrawer(-24)"
        @keydown.right.prevent="nudgeDrawer(24)"
      />
      <header class="tm-drawer-head">
        <div>
          <span class="tm-badge" :data-tone="kindTone(detailCard)">{{ KIND_LABEL[detailCard.kind] }}</span>
          <h2>{{ detailCard.place }}</h2>
          <p class="tm-drawer-sub">{{ clockRange(detailCard) }} · {{ detailIdentityText(detailCard) }} · {{ detailSpatialText(detailCard) }}</p>
        </div>
        <button class="tm-close" aria-label="关闭详情" @click="closeDetail"><AppIcon name="close" :size="14" /></button>
      </header>

      <div class="tm-drawer-body">
        <template v-if="detailCard.kind === 'confirmed_track'">
          <p v-if="isDemoCard(detailCard)" class="tm-demo-note">
            演示样本：这张卡只用于验收卡片与详情结构，轨迹段与帧数为示意；身份未经真实审核，轨迹与空间位置都不能作为证据。
          </p>
          <p class="tm-kv"><span>轨迹段</span>{{ detailTechCards.length }} 段 · {{ confirmedFrameTotal(detailCard) }} 帧</p>
          <p class="tm-kv"><span>身份</span>身份已确认（审核结论）；空间位置仍需单独 L2 审核</p>
          <template v-if="confirmedThumbs(detailCard).length">
            <h4 class="tm-section-title">轨迹关键帧</h4>
            <div class="tm-drawer-thumbs">
              <img
                v-for="thumb in confirmedThumbs(detailCard)"
                :key="thumb.key"
                class="tm-drawer-thumb"
                :src="thumb.url"
                :alt="thumb.alt"
                loading="lazy"
              />
            </div>
            <p class="tm-hint">关键帧按片段内偏移请求，容器 PTS 尚未与画面 OSD 核对。</p>
          </template>
        </template>

        <template v-if="detailCard.kind === 'region_investigation' && detailCard.region">
          <div class="tm-region-counts" aria-label="区域摄像头状态计数">
            <span class="tm-count tm-count--done">分析完成 <b>{{ regionCameraCounts.done }}</b></span>
            <span class="tm-count tm-count--running">分析中 <b>{{ regionCameraCounts.running }}</b></span>
            <span class="tm-count tm-count--pending">待核查 <b>{{ regionCameraCounts.pending }}</b></span>
          </div>
          <p class="tm-kv"><span>区域状态</span>{{ regionStatusText(detailCard.region.status) }}</p>
          <p class="tm-kv"><span>计划录像范围</span>{{ regionWindowText(detailCard.region) }} · 时间可信度 {{ detailCard.region.timeQuality }}</p>
          <p class="tm-kv"><span>窗口统计</span>完成 {{ detailCard.region.phaseCounts.completed }} · 排队 {{ detailCard.region.phaseCounts.queued }} · 分析中 {{ detailCard.region.phaseCounts.running }} · 失败 {{ detailCard.region.phaseCounts.failed }}</p>
          <p class="tm-kv"><span>区域结果</span>{{ detailCard.region.candidateCount ? detailCard.region.candidateCount + ' 条候选，' + detailCard.region.pendingCandidates + ' 条待核查；候选不等于已确认轨迹' : '已检查，暂无可靠候选（不代表目标没有经过）' }}</p>

          <h4 class="tm-section-title">摄像头与检索窗口</h4>
          <ul class="tm-drawer-cams">
            <li
              v-for="camera in regionCameraRows"
              :key="camera.key"
              class="tm-drawer-cam"
              :data-state="cameraState(camera)"
            >
              <div class="tm-drawer-cam-head">
                <strong>{{ camera.label }}</strong>
                <span class="tm-pill" :data-state="cameraState(camera)">{{ cameraStateText(camera) }}</span>
              </div>
              <p class="tm-drawer-meta">
                {{ camera.uuid ? '相机 ' + camera.uuid.slice(0, 8) + '…' : '未关联地图相机' }} ·
                {{ camera.windowCount }} 次检索 · 覆盖 {{ camera.windows.length }} 段 ·
                {{ camera.candidateCount ? camera.candidateCount + ' 条候选' : '暂无候选' }}
              </p>
              <p class="tm-drawer-meta">
                {{ camera.windows.map(cameraWindowText).join('、') }}
                <span v-if="cameraState(camera) === 'running'" class="tm-analysis-plus">分析时长 +5 分钟</span>
              </p>

              <figure v-if="cameraState(camera) !== 'pending' && cameraShotUrl(camera)" class="tm-cam-shot">
                <img :src="cameraShotUrl(camera) || ''" :alt="camera.label + ' 摄像头截图'" loading="lazy" />
                <figcaption>摄像头截图 · 片段内偏移 {{ camera.offsetSeconds?.toFixed(1) }}s · 容器 PTS 未核对</figcaption>
              </figure>
              <p v-else-if="cameraState(camera) === 'running'" class="tm-hint">窗口片段偏移尚未解析，暂不能取摄像头截图。</p>

              <div v-if="cameraState(camera) === 'pending'" class="tm-pending">
                <p class="tm-pending-title">
                  待核查 {{ camera.pendingGroups.length || camera.pendingCount }} 组
                </p>
                <button
                  v-if="camera.pendingGroups.length >= 5"
                  class="tm-pending-entry"
                  @click="openEvidencePreview(camera.pendingGroups)"
                >
                  <span>查看 {{ camera.pendingGroups.length }} 组待核查大图</span>
                  <span class="tm-pending-entry-sub">目标截取大图 + 完整大图 · 点击放大核对</span>
                </button>
                <div v-else-if="camera.pendingGroups.length" class="tm-pending-grid">
                  <button
                    v-for="(group, index) in camera.pendingGroups"
                    :key="group.key"
                    class="tm-pending-group"
                    @click="openEvidencePreview([group])"
                  >
                    <span class="tm-pending-label">疑似 {{ index + 1 }} · {{ group.identity }}</span>
                    <span class="tm-pending-pair">
                      <img v-if="group.hasBbox && group.targetUrl" :src="group.targetUrl" :alt="'疑似 ' + (index + 1) + ' 目标裁剪'" loading="lazy" />
                      <img v-if="group.fullUrl" :src="group.fullUrl" :alt="'疑似 ' + (index + 1) + ' 完整帧'" loading="lazy" />
                    </span>
                    <span class="tm-pending-note">{{ group.hasBbox ? '目标裁剪 + 完整帧' : '无目标框，仅完整帧' }}</span>
                  </button>
                </div>
                <p v-else class="tm-hint">该设备有 {{ camera.pendingCount }} 条待核查候选，但当前详情没有可展开的目标卡。</p>
                <p class="tm-hint">候选目标｜身份未确认；图片只用于核对，不构成身份确认，也不升级为正式 L2。</p>
              </div>
            </li>
          </ul>

          <h4 class="tm-section-title">分析预算</h4>
          <p class="tm-kv">
            <span>预算 / 已用</span>
            {{ detailCard.region.researchBudgetSeconds === null ? '未设置' : detailCard.region.researchBudgetSeconds + 's' }}
            /
            {{ detailCard.region.researchSecondsUsed === null ? '未记录' : detailCard.region.researchSecondsUsed + 's' }}
            · 执行 {{ detailCard.region.executionCount }} 轮
          </p>
          <button
            class="tm-action"
            :disabled="!detailCard.region.primaryRunId"
            @click="actions.extendTracking(60, detailCard.region.primaryRunId ?? undefined)"
          >增加执行预算 +60s</button>
          <p class="tm-hint">+60s 只增加系统执行预算，不扩大录像检索范围。</p>

          <h4 class="tm-section-title">录像检索范围</h4>
          <p class="tm-kv"><span>当前</span>{{ regionWindowText(detailCard.region) }}</p>
          <div class="tm-actions-row">
            <button class="tm-action" disabled title="R2-B 接口尚未实现">向前扩大 2 分钟（待实现）</button>
            <button class="tm-action" disabled title="R2-B 接口尚未实现">向后扩大 2 分钟（待实现）</button>
          </div>
          <p class="tm-hint">区域卡只表示正在检查这些设备，不代表目标到达该区域。</p>
        </template>

        <template v-else>
          <div v-if="candidateCards.length > 1" class="tm-candidate-tabs">
            <button
              v-for="(card, index) in candidateCards"
              :key="card.cardId"
              class="tm-candidate-tab"
              :class="{ 'is-active': focusedCandidate?.cardId === card.cardId }"
              @click="focusCandidate(card)"
            >{{ detailCard.kind === "confirmed_track" ? "轨迹段" : "候选" }} {{ index + 1 }}</button>
          </div>

          <template v-if="focusedCandidate">
            <p v-if="detailCard.kind !== 'confirmed_track'" class="tm-kv"><span>身份</span>{{ techIdentityText(focusedCandidate) }}</p>
            <p class="tm-kv"><span>摄像头 / 时间</span>{{ focusedCandidate.cameraLabel || '未标注' }} · {{ timeLabel(focusedCandidate.observedAt) }}</p>
            <p class="tm-kv"><span>追踪 ID</span>{{ candidateTrackId(focusedCandidate) }}</p>
            <p class="tm-kv"><span>相似度</span>{{ candidateScore(focusedCandidate) }}</p>
            <p class="tm-kv"><span>空间位置</span>{{ focusedCandidate.position ? focusedCandidate.position.lat + ', ' + focusedCandidate.position.lon : '无地图坐标；空间位置仍需单独审核' }}</p>
            <p v-if="focusedCandidate.degradeReason" class="tm-kv tm-warn"><span>降级原因</span>{{ focusedCandidate.degradeReason }}</p>

            <div v-if="evidenceRefs(focusedCandidate).length" class="tm-evidence">
              <span class="tm-evidence-title">证据帧</span>
              <code v-for="ref in evidenceRefs(focusedCandidate)" :key="ref">{{ ref }}</code>
            </div>

            <div class="tm-actions-row">
              <button v-if="canPlay(focusedCandidate)" class="tm-action" @click="openPlayback(focusedCandidate)">查看录像附近片段</button>
              <button v-else class="tm-action" disabled title="该候选卡没有可用的片段内偏移">无片段位置可回看</button>
            </div>

            <div v-if="playbackCardId === focusedCandidate.cardId && playbackUrl" class="tm-video-wrap">
              <video ref="playbackVideo" class="tm-video" :src="playbackUrl" controls preload="metadata" @loadedmetadata="seekPlayback" />
              <p class="tm-hint">H.264 代理片段，容器时间戳未与画面 OSD 核对，只能算大致位置。</p>
            </div>

            <template v-if="detailCard.kind !== 'confirmed_track'">
              <h4 class="tm-section-title">候选审核</h4>
              <p class="tm-hint">审核只改变身份状态，不会把实验空间位置升级为正式 L2。</p>
              <div class="tm-actions-row">
                <button class="tm-action tm-action--ok" :disabled="reviewBusy" @click="review('approve')">确认目标</button>
                <button class="tm-action tm-action--danger" :disabled="reviewBusy" @click="review('reject')">排除</button>
                <button v-if="techIdentityText(focusedCandidate) === '身份已确认'" class="tm-action" :disabled="reviewBusy" @click="review('revoke')">撤销确认</button>
              </div>
              <p class="tm-hint">确认身份不等于空间标定通过；地图位置仍需单独审核。</p>
            </template>
          </template>
          <p v-else class="tm-empty-text">{{ detailCard.kind === "confirmed_track" ? "这张卡没有可展开的轨迹明细。" : "这张卡没有可展开的候选明细。" }}</p>

          <p class="tm-kv"><span>技术卡</span>{{ detailTechCards.length }} 张（任务、窗口与候选证据已收进详情）</p>
        </template>
      </div>
      </section>
    </Teleport>

    <Teleport to="body">
      <div
      v-if="evidencePreview.length"
      class="tm-preview-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="待核查证据大图"
      @click.self="closeEvidencePreview"
    >
      <div class="tm-preview">
        <header class="tm-preview-head">
          <div>
            <h3>{{ evidencePreview[0].cameraLabel }} · 待核查目标</h3>
            <p>共 {{ evidencePreview.length }} 组 · 目标裁剪与完整帧来自各自的候选记录</p>
          </div>
          <button class="tm-close" aria-label="关闭大图" @click="closeEvidencePreview"><AppIcon name="close" :size="14" /></button>
        </header>
        <div class="tm-preview-groups" :class="{ 'is-single': evidencePreview.length === 1 }">
          <article v-for="(group, index) in evidencePreview" :key="group.key" class="tm-preview-group">
            <p class="tm-preview-group-title">疑似 {{ index + 1 }} · {{ group.identity }}<span v-if="!group.hasBbox"> · 无目标框</span></p>
            <div class="tm-preview-grid">
              <figure v-if="group.hasBbox && group.targetUrl">
                <img :src="group.targetUrl" alt="目标截取大图" />
                <figcaption>目标截取大图（按已保存框裁剪，外扩 8%）</figcaption>
              </figure>
              <figure v-if="group.fullUrl">
                <img :src="group.fullUrl" alt="完整大图" />
                <figcaption>完整摄像头大图</figcaption>
              </figure>
            </div>
          </article>
        </div>
        <p class="tm-hint">
          候选目标｜身份未确认｜空间位置为实验估计 · 非正式 L2。图片只用于人工核查来源，不改变身份或空间等级。
        </p>
      </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.tm {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 0;
  /* 详情抽屉可以越过右栏覆盖地图，不能被右栏裁掉。 */
  overflow: visible;
}

.tm-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 10px 18px;
}

.tm-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 4px;
  padding: 3px 0 4px;
}

.tm-filter {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  min-width: 0;
  min-height: 27px;
  padding: 4px 6px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #0d141c;
  color: var(--text-2);
  font-size: 11px;
  white-space: nowrap;
}

.tm-filter:hover { border-color: var(--line-3); color: var(--text-1); }

.tm-filter-label { overflow: hidden; text-overflow: ellipsis; }

.tm-filter b {
  color: var(--text-0);
  font-size: 12.5px;
}

.tm-filter.is-active {
  border-color: color-mix(in srgb, var(--accent) 70%, transparent);
  background: color-mix(in srgb, var(--accent) 26%, #0d141c);
  color: #fff;
}

.tm-filter.is-active b { color: #fff; }

.tm-filter-dot {
  display: none;
}

.tm-filter[data-tone="ok"] .tm-filter-dot { background: var(--ok); }
.tm-filter[data-tone="warn"] .tm-filter-dot { background: var(--warn); }
.tm-filter[data-tone="info"] .tm-filter-dot { background: var(--accent); }

.tm-rule {
  margin: 8px 1px;
  color: var(--text-3);
  font-size: 11.5px;
  line-height: 1.5;
}

.tm-demo-banner {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 8px 0;
  padding: 8px 9px;
  border: 1px dashed color-mix(in srgb, var(--warn) 42%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  color: var(--warn);
  font-size: 10.5px;
  line-height: 1.55;
}

.tm-demo-banner svg { flex: none; margin-top: 1px; }

.tm-demo-tag {
  margin-left: auto;
  padding: 1px 5px;
  border: 1px dashed color-mix(in srgb, var(--warn) 50%, transparent);
  border-radius: 4px;
  color: var(--warn);
  font-size: 9.5px;
  letter-spacing: 0.02em;
}

.tm-nav {
  display: grid;
  gap: 3px;
  padding: 9px 10px;
  border: 1px dashed var(--line-3);
  border-radius: 8px;
  background: #0a1017;
}

.tm-nav--earlier {
  margin-bottom: 8px;
}

.tm-nav--later {
  margin-top: 8px;
}

.tm-nav-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.tm-nav-title {
  color: var(--text-1);
  font-size: 12px;
  font-weight: 600;
}

.tm-nav-soon {
  flex: none;
  padding: 1px 6px;
  border: 1px solid var(--line-2);
  border-radius: 999px;
  color: var(--text-3);
  font-size: 10px;
}

.tm-nav-text {
  color: var(--text-3);
  font-size: 11.5px;
}

.tm-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.tm-row {
  position: relative;
  display: grid;
  grid-template-columns: 14px minmax(0, 1fr);
  gap: 8px;
}

.tm-row::before {
  content: "";
  position: absolute;
  left: 6px;
  top: 16px;
  bottom: -14px;
  width: 1px;
  background: var(--line-2);
}

.tm-row:last-child::before {
  display: none;
}

.tm-dot {
  position: relative;
  z-index: 1;
  width: 9px;
  height: 9px;
  margin: 15px 0 0 2px;
  border-radius: 50%;
  background: var(--text-3);
  box-shadow: 0 0 0 3px #080c12;
}

.tm-dot[data-tone="ok"] { background: var(--ok); }
.tm-dot[data-tone="warn"] { background: var(--warn); }
.tm-dot[data-tone="info"] { background: var(--accent); }

.tm-card {
  display: grid;
  gap: 8px;
  width: 100%;
  padding: 10px 11px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: #0d141c;
}

.tm-card[data-kind="confirmed_track"] {
  border-color: color-mix(in srgb, var(--ok) 32%, var(--line));
  background: linear-gradient(180deg, color-mix(in srgb, var(--ok) 7%, #0d141c), #0d141c 58%);
}

.tm-card[data-kind="candidate_clue"] {
  border-color: color-mix(in srgb, var(--warn) 30%, var(--line));
  background: linear-gradient(180deg, color-mix(in srgb, var(--warn) 6%, #0d141c), #0d141c 58%);
}

.tm-card[data-kind="region_investigation"] {
  border-color: color-mix(in srgb, var(--accent) 34%, var(--line));
  background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 7%, #0d141c), #0d141c 58%);
}

.tm-card.is-open { border-color: var(--accent-line); }

.tm-card-layout {
  display: block;
  gap: 10px;
  min-width: 0;
}

.tm-card-layout.has-thumbs {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(112px, 34%);
  align-items: stretch;
}

.tm-card-content {
  display: grid;
  flex: 1;
  gap: 8px;
  min-width: 0;
}

.tm-thumbs {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 100%;
  margin-top: 1px;
}

.tm-thumb {
  position: absolute;
  top: calc(var(--i, 0) * 4px);
  right: calc(var(--i, 0) * 4px);
  width: calc(100% - var(--i, 0) * 4px);
  height: calc(100% - var(--i, 0) * 4px);
  border: 1px solid #2a3745;
  border-radius: 5px;
  background: #0a1017;
  box-shadow: 0 2px 8px #0007;
  object-fit: cover;
}

.tm-thumb-count {
  position: absolute;
  right: -3px;
  bottom: -4px;
  z-index: 9;
  display: grid;
  place-items: center;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  border: 1px solid #2b3a49;
  border-radius: 999px;
  background: #101923;
  color: var(--text-2);
  font-size: 9px;
}

.tm-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.tm-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid var(--line-2);
  border-radius: 999px;
  color: var(--text-2);
  font-size: 10.5px;
  white-space: nowrap;
}

.tm-badge[data-tone="ok"] { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 38%, transparent); background: color-mix(in srgb, var(--ok) 11%, transparent); }
.tm-badge[data-tone="warn"] { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 38%, transparent); background: color-mix(in srgb, var(--warn) 11%, transparent); }
.tm-badge[data-tone="info"] { color: var(--accent-hi); border-color: var(--accent-line); background: color-mix(in srgb, var(--accent) 13%, transparent); }

.tm-time {
  color: var(--text-0);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.2px;
}

.tm-place {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--text-0);
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.35;
}

.tm-place-icon {
  display: grid;
  flex: none;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--text-3) 14%, transparent);
  color: var(--text-2);
}

.tm-place-icon[data-tone="ok"] { color: var(--ok); background: color-mix(in srgb, var(--ok) 13%, transparent); }
.tm-place-icon[data-tone="warn"] { color: var(--warn); background: color-mix(in srgb, var(--warn) 13%, transparent); }
.tm-place-icon[data-tone="info"] { color: var(--accent-hi); background: color-mix(in srgb, var(--accent) 15%, transparent); }

.tm-place-text { min-width: 0; }

.tm-sub {
  margin: 0;
  color: var(--text-2);
  font-size: 11.5px;
  line-height: 1.5;
}

.tm-region-sum {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--text-2);
  font-size: 11px;
  line-height: 1.4;
}

.tm-region-count { flex: none; color: var(--text-3); white-space: nowrap; }

.tm-cam-list {
  display: grid;
  margin: 0;
  padding: 2px 7px;
  border: 1px solid #18232e;
  border-radius: 8px;
  background: #0a1017;
  list-style: none;
}

.tm-cam-item + .tm-cam-item { border-top: 1px solid #141e28; }

.tm-cam {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  min-width: 0;
  padding: 6px 0 7px;
  border: 0;
  background: transparent;
  color: var(--text-2);
  font-size: 11px;
  text-align: left;
}

.tm-cam:hover .tm-cam-name { color: var(--text-0); }
.tm-cam:hover .tm-cam-open { color: var(--accent-hi); }

.tm-cam-state {
  display: grid;
  flex: none;
  place-items: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--text-3) 14%, transparent);
  color: var(--text-3);
}

.tm-cam-state[data-phase="completed"] { color: var(--ok); background: color-mix(in srgb, var(--ok) 14%, transparent); }
.tm-cam-state[data-phase="running"] { color: var(--accent-hi); background: color-mix(in srgb, var(--accent) 16%, transparent); }
.tm-cam-state[data-phase="running"] svg { animation: tm-spin 2.4s linear infinite; }
.tm-cam-state[data-phase="failed"], .tm-cam-state[data-phase="no_recording"] { color: var(--danger); background: color-mix(in srgb, var(--danger) 14%, transparent); }
.tm-cam-state[data-pending="true"] { color: var(--warn); background: color-mix(in srgb, var(--warn) 15%, transparent); }

.tm-cam-name { flex: 1; min-width: 0; overflow: hidden; color: var(--text-1); text-overflow: ellipsis; white-space: nowrap; }

.tm-cam-result { flex: none; color: var(--text-3); white-space: nowrap; }
.tm-cam-result[data-phase="completed"] { color: var(--text-2); }
.tm-cam-result[data-phase="running"] { color: var(--accent-hi); }
.tm-cam-result[data-phase="failed"], .tm-cam-result[data-phase="no_recording"] { color: var(--danger); }

.tm-cam-open { display: grid; flex: none; place-items: center; color: var(--text-3); }

.tm-cam-bar {
  display: block;
  height: 3px;
  margin: 0 0 6px 23px;
  overflow: hidden;
  border-radius: 999px;
  background: color-mix(in srgb, var(--text-3) 18%, transparent);
}

.tm-cam-bar > i {
  display: block;
  width: 38%;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  animation: tm-indeterminate 1.3s ease-in-out infinite;
}

@keyframes tm-indeterminate {
  0% { transform: translateX(-110%); }
  100% { transform: translateX(280%); }
}

@keyframes tm-spin {
  to { transform: rotate(360deg); }
}

.tm-gap {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 0;
  color: var(--warn);
  font-size: 10.5px;
}

.tm-clue {
  margin: 0;
  color: color-mix(in srgb, var(--warn) 80%, var(--text-1));
  font-size: 11.5px;
  line-height: 1.5;
}

.tm-card-foot {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  margin-top: 2px;
  padding-top: 8px;
  border-top: 1px solid color-mix(in srgb, var(--line) 75%, transparent);
}

.tm-detail-btn {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 3px;
  padding: 4px 9px;
  border: 1px solid color-mix(in srgb, var(--accent) 42%, transparent);
  border-radius: 7px;
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  color: var(--accent-hi);
  font-size: 11px;
  white-space: nowrap;
}

.tm-detail-btn:hover { border-color: var(--accent-line); background: color-mix(in srgb, var(--accent) 18%, transparent); }

.tm-locate-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 4px 9px;
  border: 1px solid color-mix(in srgb, var(--ok) 42%, transparent);
  border-radius: 7px;
  background: color-mix(in srgb, var(--ok) 8%, transparent);
  color: var(--ok);
  font-size: 11px;
  white-space: nowrap;
}

.tm-locate-btn:hover { border-color: color-mix(in srgb, var(--ok) 72%, transparent); background: color-mix(in srgb, var(--ok) 15%, transparent); }

.tm-time-hint { margin: -2px 0 0; color: var(--text-3); font-size: 10px; }

.tm-foot-note {
  margin: 10px 1px 0;
  color: var(--text-3);
  font-size: 10.5px;
  line-height: 1.5;
}

.tm-empty {
  display: grid;
  place-items: center;
  gap: 8px;
  padding: 36px 14px;
  color: var(--text-3);
  text-align: center;
  font-size: 12px;
}

.tm-error {
  margin: 8px 0;
  padding: 8px;
  border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent);
  border-radius: 7px;
  color: var(--danger);
  font-size: 11.5px;
}

.tm-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  left: auto;
  z-index: 900;
  display: flex;
  flex-direction: column;
  max-width: calc(100vw - 24px);
  border-left: 1px solid var(--line-3);
  background: #0a1017;
  box-shadow: -12px 0 28px rgba(0, 0, 0, 0.38);
}

/* 抽屉打开时，栏级拖拽把手不再接收指针，避免误拉时间线宽度。 */
:global(body.tm-drawer-open .rail-resizer) { pointer-events: none; }
/* 大图打开时，抽屉自己的宽度把手也让位。 */
:global(body.tm-preview-open .tm-drawer-resize) { pointer-events: none; }

.tm-drawer-resize {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -3px;
  z-index: 2;
  width: 7px;
  cursor: col-resize;
}
.tm-drawer-resize:hover,
.tm-drawer-resize:focus-visible { background: color-mix(in srgb, var(--accent) 45%, transparent); outline: none; }

.tm-region-counts { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 9px; }
.tm-count { padding: 2px 7px; border: 1px solid var(--line-2); border-radius: 999px; color: var(--text-2); font-size: 10.5px; }
.tm-count b { margin-left: 2px; color: var(--text-0); }
.tm-count--done { border-color: color-mix(in srgb, var(--ok) 42%, transparent); color: var(--ok); }
.tm-count--running { border-color: color-mix(in srgb, var(--accent) 42%, transparent); color: var(--accent); }
.tm-count--pending { border-color: color-mix(in srgb, var(--warn) 45%, transparent); color: var(--warn); }

.tm-pill[data-state="pending"] { border-color: color-mix(in srgb, var(--warn) 45%, transparent); color: var(--warn); }
.tm-pill[data-state="running"] { border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
.tm-pill[data-state="done"] { border-color: color-mix(in srgb, var(--ok) 38%, transparent); color: var(--ok); }

.tm-analysis-plus {
  display: inline-block;
  margin-left: 5px;
  padding: 0 4px;
  border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  border-radius: 4px;
  color: var(--accent);
  font-size: 9.5px;
}

.tm-cam-shot { margin: 6px 0 0; }
.tm-cam-shot img {
  display: block;
  width: 100%;
  max-height: 180px;
  object-fit: contain;
  border: 1px solid var(--line-2);
  border-radius: 5px;
  background: #000;
}
.tm-cam-shot figcaption { margin-top: 3px; color: var(--text-3); font-size: 9.5px; line-height: 1.4; }

.tm-pending { margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--line-2); }
.tm-pending-title { margin: 0 0 5px; color: var(--warn); font-size: 10.5px; }
.tm-pending-entry {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  padding: 8px;
  border: 1px dashed color-mix(in srgb, var(--warn) 45%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--warn) 7%, transparent);
  color: var(--text-1);
  text-align: left;
  font-size: 11px;
}
.tm-pending-entry-sub { color: var(--text-3); font-size: 9.5px; }
.tm-pending-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px; }
.tm-pending-group {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 4px;
  border: 1px solid var(--line-2);
  border-radius: 5px;
  background: #0a1017;
  color: var(--text-2);
  text-align: left;
}
.tm-pending-group:hover { border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
.tm-pending-label { overflow: hidden; color: var(--warn); font-size: 9.5px; text-overflow: ellipsis; white-space: nowrap; }
.tm-pending-pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 2px; }
.tm-pending-pair img { width: 100%; height: 62px; object-fit: contain; border-radius: 3px; background: #000; }
.tm-pending-note { color: var(--text-3); font-size: 9px; }

.tm-preview-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(3, 7, 12, 0.78);
}
.tm-preview {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(1100px, 94vw);
  max-height: 92vh;
  overflow-y: auto;
  padding: 12px;
  border: 1px solid var(--line-3);
  border-radius: 9px;
  background: #0a1017;
  box-shadow: 0 18px 55px rgba(0, 0, 0, 0.55);
}
.tm-preview-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.tm-preview-head h3 { margin: 0; color: var(--text-0); font-size: 14px; }
.tm-preview-head p { margin: 3px 0 0; color: var(--text-3); font-size: 10.5px; }
.tm-preview-groups { display: grid; gap: 12px; }
.tm-preview-group { display: grid; gap: 5px; }
.tm-preview-group + .tm-preview-group { padding-top: 11px; border-top: 1px dashed var(--line-2); }
.tm-preview-group-title { margin: 0; color: var(--warn); font-size: 10.5px; }
.tm-preview-grid { display: grid; grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.8fr); gap: 8px; }
.tm-preview-grid figure { margin: 0; }
.tm-preview-grid img {
  display: block;
  width: 100%;
  max-height: 68vh;
  object-fit: contain;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: #000;
}
.tm-preview-groups:not(.is-single) .tm-preview-grid img { max-height: 32vh; }
.tm-preview-grid figcaption { margin-top: 3px; color: var(--text-3); font-size: 10px; }

.tm-drawer-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 11px;
  border-bottom: 1px solid var(--line);
}

.tm-drawer-head > div { flex: 1; min-width: 0; }
.tm-drawer-head h2 { margin: 4px 0 2px; color: var(--text-0); font-size: 15px; }
.tm-drawer-sub { margin: 0; color: var(--text-3); font-size: 11px; }
.tm-close { width: 26px; height: 26px; border: 0; background: transparent; color: var(--text-2); }

.tm-drawer-thumbs {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin: 2px 0 6px;
}

.tm-drawer-thumb {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: #0a1017;
}

.tm-demo-note {
  margin: 0 0 8px;
  padding: 8px 9px;
  border: 1px dashed color-mix(in srgb, var(--warn) 42%, transparent);
  border-radius: 7px;
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  color: var(--warn);
  font-size: 10.5px;
  line-height: 1.55;
}

.tm-drawer-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 11px 18px;
}

.tm-kv {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 7px;
  margin: 0 0 7px;
  color: var(--text-1);
  font-size: 11.5px;
  line-height: 1.5;
}

.tm-kv > span { color: var(--text-3); }
.tm-kv.tm-warn { color: var(--warn); }

.tm-section-title {
  margin: 14px 0 6px;
  padding-top: 9px;
  border-top: 1px solid var(--line);
  color: var(--text-1);
  font-size: 12px;
}

.tm-drawer-cams {
  display: grid;
  gap: 5px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.tm-drawer-cam {
  padding: 6px 7px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #0c121a;
}

.tm-drawer-cam-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.tm-drawer-cam-head strong { color: var(--text-1); font-size: 11.5px; }
.tm-pill { padding: 1px 5px; border: 1px solid var(--line-2); border-radius: 999px; color: var(--text-3); font-size: 10px; }
.tm-drawer-meta { margin: 3px 0 0; color: var(--text-3); font-size: 10.5px; }

.tm-action {
  padding: 5px 9px;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: #0c121a;
  color: var(--text-1);
  font-size: 11.5px;
}

.tm-action:disabled { color: var(--text-3); opacity: 0.65; }
.tm-action--ok { border-color: color-mix(in srgb, var(--ok) 50%, transparent); color: var(--ok); }
.tm-action--danger { border-color: color-mix(in srgb, var(--danger) 45%, transparent); color: var(--danger); }
.tm-actions-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }

.tm-hint { margin: 4px 0 0; color: var(--text-3); font-size: 10.5px; line-height: 1.5; }

.tm-candidate-tabs { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }
.tm-candidate-tab {
  padding: 3px 7px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: transparent;
  color: var(--text-3);
  font-size: 10.5px;
}
.tm-candidate-tab.is-active { border-color: var(--accent-line); color: var(--accent-hi); background: #101922; }

.tm-evidence {
  display: grid;
  gap: 3px;
  margin: 8px 0;
  padding: 6px 7px;
  border-radius: 6px;
  background: #0c121a;
}
.tm-evidence-title { color: var(--text-3); font-size: 10.5px; }
.tm-evidence code { color: var(--text-2); font-size: 10px; word-break: break-all; }

.tm-video-wrap { margin: 8px 0; }
.tm-video { width: 100%; max-height: 220px; border-radius: 6px; background: #000; }
.tm-empty-text { color: var(--text-3); font-size: 11.5px; }
</style>
