<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { proxyVideoUrl, type TimelineCard } from "../services/mainApi";
import {
  actions,
  activeTrackingRun,
  currentTimelineTargetId,
  lastExtendDispatch,
  selectedTimelineCard,
  timelineAnchorLinked,
  timelineCards,
  timelineError,
  timelineEvents,
  timelineLoading,
  timelineNote,
  timelinePhase,
  trackingWindows,
  wb,
} from "../store/workbench";

const order = ref<"asc" | "desc">("asc");

/* 右侧时间线可拖宽：默认 460px，最小 380，最大 720 / 视口 55% */
const RAIL_KEY = "trajnexa.rail.width";
const RAIL_DEFAULT = 460;
const RAIL_MIN = 380;
const RAIL_MAX = 720;
const railWidth = ref(RAIL_DEFAULT);

function applyRailWidth(value: number) {
  const max = Math.min(RAIL_MAX, Math.round(window.innerWidth * 0.55));
  const width = Math.round(Math.min(Math.max(value, RAIL_MIN), Math.max(RAIL_MIN, max)));
  railWidth.value = width;
  document.documentElement.style.setProperty("--rail-w", `${width}px`);
}

let detachResize: (() => void) | null = null;

function startResize(event: PointerEvent) {
  if (event.button !== 0) return;
  event.preventDefault();
  const startX = event.clientX;
  const startWidth = railWidth.value;
  document.body.style.cursor = "col-resize";
  document.body.style.userSelect = "none";

  const onMove = (moveEvent: PointerEvent) => {
    // 拖动左边缘：向左拖变宽
    applyRailWidth(startWidth + (startX - moveEvent.clientX));
  };
  const onUp = () => {
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    detachResize = null;
    try { localStorage.setItem(RAIL_KEY, String(railWidth.value)); } catch { /* 本地显示偏好 */ }
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  detachResize = onUp;
}

function resetRailWidth() {
  applyRailWidth(RAIL_DEFAULT);
  try { localStorage.setItem(RAIL_KEY, String(railWidth.value)); } catch { /* 本地显示偏好 */ }
}

/* B8 追踪任务卡片：正在研判的镜头 + 已研判窗口（缩略图接口预留，先不伪造画面） */
const nowMs = ref(Date.now());
let ticker: number | null = null;
onMounted(() => {
  ticker = window.setInterval(() => { nowMs.value = Date.now(); }, 1000);
  let saved = RAIL_DEFAULT;
  try { saved = Number(localStorage.getItem(RAIL_KEY)) || RAIL_DEFAULT; } catch { /* 本地显示偏好 */ }
  applyRailWidth(saved);
});
onBeforeUnmount(() => {
  if (ticker !== null) window.clearInterval(ticker);
  detachResize?.();
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
});

const active = computed(() => activeTrackingRun.value?.active ?? null);
const activeElapsed = computed(() => {
  const run = activeTrackingRun.value;
  if (!run) return 0;
  // research_seconds_used 是跨执行轮次的累计值；正在分析的窗口再叠加当前已过秒数
  const base = run.research_seconds_used ?? run.progress?.research_seconds_used ?? 0;
  const started = active.value?.started_at;
  const live = started ? Math.max(0, (nowMs.value - new Date(started).getTime()) / 1000) : 0;
  return Math.floor(base + live);
});
const researchLimit = computed(() => activeTrackingRun.value?.research_seconds ?? 120);
const overLimit = computed(() => activeElapsed.value > researchLimit.value);
const hhmm = (seconds: number) =>
  String(Math.floor(seconds / 3600) % 24).padStart(2, "0") + ":" +
  String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
const expandedId = ref<string | null>(null);

/* ---------------- B8 V1.2 时间轴：目标追踪进展视角 ---------------- */

type PresentationFilter = "all" | TimelineCard["presentation"];

const PRESENTATION_TABS: { id: TimelineCard["presentation"]; label: string }[] = [
  { id: "review_track", label: "待核查轨迹" },
  { id: "confirmed_track", label: "已确认轨迹" },
  { id: "investigating_region", label: "正在研判区域" },
];

const KIND_LABEL: Record<TimelineCard["kind"], string> = {
  anchor: "起始目标证据",
  region_investigation: "区域研判",
  camera_task: "任务执行",
  track_segment: "候选线索",
  gap: "轨迹中断",
  spatial_observation: "空间观测",
};

const viewFilter = ref<PresentationFilter>("all");

/** 起始目标一旦关联到研判任务，就收进轨迹详情，不再单占一张主卡 */
const visibleForCounts = computed(() =>
  timelineCards.value.filter((card) => !(card.kind === "anchor" && timelineAnchorLinked.value)),
);

const cards = computed(() => {
  const rows = visibleForCounts.value.filter(
    (card) => viewFilter.value === "all" || card.presentation === viewFilter.value,
  );
  return order.value === "asc" ? rows : [...rows].reverse();
});

const presentationCounts = computed<Record<TimelineCard["presentation"], number>>(() => {
  const counts = { confirmed_track: 0, review_track: 0, investigating_region: 0 };
  for (const card of visibleForCounts.value) counts[card.presentation] += 1;
  return counts;
});

const anchorCard = computed(() => timelineCards.value.find((card) => card.kind === "anchor") ?? null);
const selectedCard = computed(() => selectedTimelineCard());

/** 只显示当前选中区域卡所属 run 的回执，避免 A 区域显示 B 区域的预算结果 */
const regionDispatch = computed(() => {
  const region = selectedCard.value?.region;
  const last = lastExtendDispatch.value;
  if (!region || !last || last.runId !== region.runId) return null;
  return last.dispatch;
});
const regionDispatchTone = computed(() => {
  const status = regionDispatch.value?.status;
  if (status === 'budget_updated') return 'ok';
  if (status === 'running') return 'info';
  return 'warn';
});

const REGION_STATUS_TEXT: Record<string, string> = {
  planned: "区域待规划",
  queued: "排队中",
  running: "研判中",
  awaiting_review: "区域研判待审核",
  completed_with_evidence: "已产出候选",
  completed_no_match: "已检查，无可靠候选",
  failed: "分析失败",
  cancelled: "已取消",
  budget_exhausted: "预算用尽",
};

const WINDOW_PHASE_TEXT: Record<string, string> = {
  completed: "已完成",
  running: "分析中",
  queued: "排队中",
  no_recording: "无可用录像",
  failed: "失败",
  unknown: "状态未知",
};

const regionStatusText = (card: TimelineCard) =>
  REGION_STATUS_TEXT[card.region?.status ?? ""] ?? card.region?.status ?? "状态未知";

const windowPhaseText = (phase: string) => WINDOW_PHASE_TEXT[phase] ?? phase;

const regionPhaseSummary = (card: TimelineCard) => {
  const counts = card.region?.phaseCounts;
  if (!counts) return "窗口统计不可用";
  return `完成 ${counts.completed} · 排队 ${counts.queued} · 分析中 ${counts.running} · 失败 ${counts.failed} · 无录像 ${counts.no_recording}`;
};

/** 结果级文案：只报候选与待核查数量，绝不写“目标没有经过”这类结论 */
const regionResultText = (card: TimelineCard) => {
  const region = card.region;
  if (!region) return "无结果信息";
  if (!region.plannedWindows) return "尚未排出设备窗口";
  if (!region.candidateCount) return "窗口已检查，未产出候选线索（不代表目标没有经过）";
  return `候选线索 ${region.candidateCount} 条 · 待核查 ${region.pendingCandidates} 条；候选不等于已确认轨迹`;
};

const regionUpdatedText = (card: TimelineCard) => {
  const stamp = card.region?.lastUpdatedAt;
  return stamp ? stamp.slice(0, 19).replace("T", " ") : "未知";
};

/** 设备映射依据：同名匹配只是台账同名，不是设备身份确认 */
const CAMERA_SOURCE_TEXT: Record<string, string> = {
  candidate_explicit: "候选显式绑定",
  catalog_name_exact: "台账完全同名",
};
const cameraSourceText = (task: { cameraUuidSource?: string | null }) =>
  task.cameraUuidSource ? (CAMERA_SOURCE_TEXT[task.cameraUuidSource] ?? task.cameraUuidSource) : "未解析";

/** 计划录像范围：没有起始墙钟时间就只报片段内秒数，不换算成具体时刻 */
function regionRangeText(card: TimelineCard): string {
  const win = card.region?.plannedObservationWindow;
  if (!win || win.startSeconds === null) return "计划未确定（不按距离猜设备）";
  const seconds = win.startSeconds.toFixed(1) + "–" + (win.endSeconds?.toFixed(1) ?? "?") + "s";
  if (!win.startAt) return "片段内 " + seconds + " · 缺少起始墙钟时间，无法换算";
  return win.startAt.slice(11, 19) + "–" + (win.endAt ?? "").slice(11, 19) +
    " · 时间可信度 " + (card.region?.timeQuality ?? "未核验");
}

function bboxText(card: TimelineCard): string {
  const bbox = (card.evidence as { bboxPixels?: number[] } | null)?.bboxPixels;
  if (!Array.isArray(bbox) || bbox.length !== 4) return "没有框选坐标";
  return "框选 " + bbox.map((value) => Math.round(value)).join(", ");
}

const PHASE_TEXT: Record<string, string> = {
  not_started: "未开始研判",
  queued: "排队中",
  running: "研判中",
  awaiting_review: "待人工审核",
  budget_exhausted: "预算用尽",
  unresolved: "未找到候选",
  completed: "研判结束",
  failed: "研判失败",
  cancelled: "已取消",
};

const phaseText = computed(() => PHASE_TEXT[timelinePhase.value] ?? timelinePhase.value);

/** 卡片时间：优先真实观测时间，其次分析时间；都没有就显示「无时间」而不是拿当前时间顶上 */
function cardTime(card: TimelineCard) {
  const stamp = card.observedAt || card.analysisAt;
  if (!stamp) return "无时间";
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return stamp.slice(11, 19);
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

function cardTone(card: TimelineCard): "anchor" | "ok" | "warn" | "info" | "muted" {
  if (card.kind === "anchor") return "anchor";
  if (card.kind === "gap") return "warn";
  if (card.kind === "region_investigation") return "info";
  if (card.degradeReason) return "warn";
  if (card.kind === "track_segment") return "info";
  if (card.kind === "spatial_observation") return card.position ? "ok" : "muted";
  return "ok";
}

/** 身份状态与空间质量分开说：候选未确认永远不能被读成“已确认目标” */
function identityText(card: TimelineCard) {
  if (card.kind === "gap") return "证据缺口";
  if (card.identityStatus === "candidate_unverified") return "候选未确认";
  if (card.identityStatus === "ambiguous") return "身份歧义";
  if (!card.identityStatus) return "无身份结论";
  return card.identityStatus;
}

function positionText(card: TimelineCard) {
  if (card.displayHint === "device_point" && card.position) {
    return `设备点 ${card.position.lat.toFixed(5)}, ${card.position.lon.toFixed(5)}`;
  }
  return "无地图坐标";
}

const EVENT_TEXT: Record<string, string> = {
  "task.queued": "任务排队",
  "task.running": "开始研判",
  "window.analyzed": "窗口完成",
  "candidate.found": "发现候选",
  "review.decided": "审核决定",
  "task.finished": "任务结束",
};

const eventLabel = (type: string) => EVENT_TEXT[type] ?? type;

function eventTime(stamp: string) {
  const date = new Date(stamp);
  return Number.isNaN(date.getTime()) ? stamp.slice(11, 19) : date.toLocaleTimeString("zh-CN", { hour12: false });
}

const playbackText = computed(() => {
  const card = selectedCard.value;
  const playback = card?.playback;
  if (!card || !playback || !playback.clipId) return null;
  const offset = playback.offsetSeconds;
  const at = offset === null || offset === undefined ? "位置未知" : `片段内 ${offset.toFixed(1)}s`;
  return `${playback.clipId.slice(0, 8)}… · ${at} · ${playback.timeQuality ?? playback.timeBasis ?? "时间未核验"}`;
});

/* B2-3 卡片回看：起播 = 卡片偏移前 30s，一次取 60s 代理片段（H.264 重编码） */
const playbackOpen = ref(false);
const playbackUrl = ref<string | null>(null);
const playbackSeek = ref(0);
const playbackNote = ref<string | null>(null);
const playbackVideo = ref<HTMLVideoElement | null>(null);

function closePlayback() {
  playbackOpen.value = false;
  playbackUrl.value = null;
  playbackNote.value = null;
}

function openPlayback(card: TimelineCard) {
  const clipId = card.playback?.clipId;
  const offset = card.playback?.offsetSeconds ?? null;
  if (!clipId || offset === null || offset === undefined) {
    playbackNote.value = "这张卡没有片段内位置，不能跳转播放。";
    return;
  }
  if (!wb.caseId) {
    playbackNote.value = "没有选中案件，无法校验片段归属。";
    return;
  }
  const start = Math.max(0, offset - 30);
  playbackUrl.value = proxyVideoUrl(wb.caseId, clipId, start, 60);
  playbackSeek.value = Math.max(0, offset - start);
  playbackNote.value = null;
  playbackOpen.value = true;
}

function seekPlaybackVideo() {
  const video = playbackVideo.value;
  if (!video) return;
  const target = Math.min(playbackSeek.value, video.duration || playbackSeek.value);
  video.currentTime = target;
  void video.play().catch(() => { /* 自动播放可能被浏览器拒绝，用户可手动点播放 */ });
}

watch(selectedCard, () => closePlayback());

/* 空态按真实原因给下一步，不用示例数据填充 */
const hasTarget = computed(() => currentTimelineTargetId() !== null);

const emptyTitle = computed(() => {
  if (!wb.caseId) return "还没有选择案件";
  if (!hasTarget.value) return "该案件还没有研判任务";
  return "时间轴为空";
});

const emptyText = computed(() => {
  if (!wb.caseId) return "选择案件后，时间轴按真实研判记录显示锚点、任务、候选轨迹与缺口卡片。";
  if (!hasTarget.value) return "时间轴卡片由追踪任务派生。先启动一次研判，卡片才会出现。";
  return "这个目标目前没有可显示的卡片；没有数据时不会填充示例数据。";
});

</script>

<template>
  <aside class="rail" aria-label="观测时间线">
    <div
      class="rail-resizer"
      role="separator"
      aria-orientation="vertical"
      :aria-label="`时间线宽 ${railWidth} 像素，拖动调整，双击恢复默认`"
      title="拖动调宽，双击恢复默认"
      @pointerdown="startResize"
      @dblclick="resetRailWidth"
    />
    <header class="rail-head">
      <span class="rail-mark"><AppIcon name="target" :size="13" /></span>
      <span class="rail-title">观测时间线</span>
      <span class="rail-count num">({{ timelineCards.length }})</span>
      <button
        class="icon-btn rail-refresh"
        aria-label="刷新时间线"
        title="刷新时间线"
        :disabled="timelineLoading"
        @click="actions.refreshTimeline()"
      >
        <AppIcon name="refresh" :size="14" />
      </button>
    </header>

    <div v-if="timelineCards.length" class="rail-filters rail-filters--views">
      <div class="view-tabs" role="tablist" aria-label="按研判视角筛选">
        <button
          class="view-tab"
          :class="{ 'is-active': viewFilter === 'all' }"
          role="tab"
          :aria-selected="viewFilter === 'all'"
          @click="viewFilter = 'all'"
        >全部</button>
        <button
          v-for="tab in PRESENTATION_TABS"
          :key="tab.id"
          class="view-tab"
          :class="{ 'is-active': viewFilter === tab.id }"
          role="tab"
          :aria-selected="viewFilter === tab.id"
          @click="viewFilter = tab.id"
        >{{ tab.label }}<span class="num">{{ presentationCounts[tab.id] }}</span></button>
      </div>
      <div class="view-meta">
        <select v-model="order" class="select select--sm" aria-label="排序">
          <option value="asc">时间正序</option>
          <option value="desc">时间倒序</option>
        </select>
        <span class="rail-phase num" :title="timelinePhase">{{ phaseText }}</span>
      </div>
    </div>

    <div v-else class="rail-filters rail-filters--empty">
      <span class="rail-phase num">暂无卡片</span>
    </div>


      <section v-if="active || trackingWindows.length" class="track-block" aria-label="追踪研判">
        <div v-if="active" class="track-live" :class="{ 'is-over': overLimit }">
          <span class="track-live-dot" />
          <div class="track-live-main">
            <strong>正在研判 · {{ active.camera_label }}</strong>
            <span class="num">
              {{ hhmm(active.window[0]) }} 起 · 已研判 {{ activeElapsed }}s / {{ researchLimit }}s
            </span>
          </div>
          <button class="btn btn--sm btn--outline" @click="actions.extendTracking(60)">+60s</button>
        </div>
        <p v-if="overLimit" class="track-over">超过系统研判窗口，可延长或取消，不会无限计时。</p>
        <details v-for="win in trackingWindows" :key="win.id" class="track-card" open>
          <summary>
            <span class="track-thumb" aria-hidden="true">缩略图接口已预留</span>
            <span class="track-info">
              <strong>{{ win.camera_label }}</strong>
              <span class="num">{{ hhmm(win.window[0]) }}–{{ hhmm(win.window[1]) }}</span>
              <span class="muted">{{ win.status }} · 轨迹 {{ win.tracks }}</span>
            </span>
          </summary>
          <div class="track-detail">
            <div class="track-thumb track-thumb--lg" aria-hidden="true">截取录像 / 大图接口预留</div>
            <span class="muted">片段与证据地址由后端返回后填入，不在浏览器里拼路径。</span>
          </div>
        </details>
      </section>

    <div class="rail-list scroll-y">
      <template v-if="timelineCards.length">
        <div class="day-head">
          <span class="day-label">证据时间线 · {{ cards.length }} 张</span>
          <span class="day-label num">{{ phaseText }}</span>
        </div>
        <p v-if="timelineError" class="rail-error">{{ timelineError }}</p>
        <p v-if="timelineNote" class="rail-note">{{ timelineNote }}</p>
        <div class="tl">
          <article
            v-for="card in cards"
            :key="card.cardId"
            class="tl-row"
            :class="{ 'is-selected': wb.selectedCardId === card.cardId }"
          >
            <span class="tl-dot" :data-tone="cardTone(card)" />
            <button class="tl-card" @click="actions.selectTimelineCard(card.cardId)">
              <div class="tl-top">
                <span class="tl-time num">{{ cardTime(card) }}</span>
                <span class="tl-cam">{{ card.cameraLabel || KIND_LABEL[card.kind] }}</span>
                <span class="tl-round num">{{ KIND_LABEL[card.kind] }}</span>
              </div>
              <div class="tl-mid">
                <span class="tl-status" :data-tone="cardTone(card)">{{ card.title }}</span>
                <span class="tl-note">{{ card.subtitle }}</span>
              </div>
              <div class="tl-sub tl-sub--badges">
                <span class="tl-badge">{{ identityText(card) }}</span>
                <span class="tl-badge" :data-tone="card.position ? 'ok' : 'muted'">{{ positionText(card) }}</span>
                <span v-if="card.spatialQuality" class="tl-badge">{{ card.spatialQuality }}</span>
                <span v-if="card.degradeReason" class="tl-badge" data-tone="warn">降级：{{ card.degradeReason }}</span>
              </div>
            </button>
          </article>
        </div>
        <p v-if="!cards.length" class="rail-empty-text">当前类型下没有卡片。</p>
        <div v-if="selectedCard" class="card-detail">
          <div class="card-detail-head">
            <strong>{{ selectedCard.title }}</strong>
            <span class="num dim">{{ selectedCard.cardId }}</span>
          </div>
          <div v-if="selectedCard.kind === 'region_investigation' && selectedCard.region" class="region-block">
            <p class="card-detail-line">
              <span class="dim">区域状态：</span>{{ regionStatusText(selectedCard) }}
              <span class="dim"> · 设备 {{ selectedCard.region.deviceCount }} 台</span>
            </p>
            <p class="card-detail-line">
              <span class="dim">计划录像范围：</span>{{ regionRangeText(selectedCard) }}
            </p>
            <p class="card-detail-line">
              <span class="dim">窗口：</span>{{ regionPhaseSummary(selectedCard) }}
              <span class="dim"> · 最近真实更新 </span>{{ regionUpdatedText(selectedCard) }}
            </p>
            <p class="card-detail-line">
              <span class="dim">结果：</span>{{ regionResultText(selectedCard) }}
            </p>
            <ul class="region-tasks">
              <li v-for="task in selectedCard.region.cameraTasks" :key="task.windowId" class="region-task">
                <div class="region-task-head">
                  <strong>{{ task.cameraLabel }}</strong>
                  <span class="tl-badge">{{ windowPhaseText(task.phase) }}</span>
                  <span v-if="task.errorCode" class="tl-badge" data-tone="warn">降级：{{ task.errorCode }}</span>
                </div>
                <span class="dim num region-task-meta">
                  {{ task.cameraUuid ? '相机 ' + task.cameraUuid.slice(0, 8) + '…' : '未关联地图相机' }}（{{ cameraSourceText(task) }}） ·
                  片段 {{ task.clipId.slice(0, 8) }}… · {{ task.windowSeconds[0] }}–{{ task.windowSeconds[1] }}s · 轨迹 {{ task.tracks }}
                </span>
              </li>
            </ul>
            <p v-if="!selectedCard.region.cameraTasks.length" class="card-detail-line dim">
              还没有排进具体设备；不按距离或名称补造排队摄像头。
            </p>
            <div class="region-budget">
              <span class="dim num">
                预算 {{ selectedCard.region.researchBudgetSeconds ?? '未设置' }}s · 累计已执行 {{ selectedCard.region.researchSecondsUsed ?? selectedCard.region.elapsedAnalysisSeconds ?? '—' }}s · 窗口 {{ selectedCard.region.completedWindows }}/{{ selectedCard.region.plannedWindows }}<template v-if="selectedCard.region.executionCount"> · 执行 {{ selectedCard.region.executionCount }} 轮</template><template v-if="selectedCard.region.stopReason"> · 上次停止：{{ selectedCard.region.stopReason }}</template>
              </span>
              <button class="btn btn--sm btn--outline" @click="actions.extendTracking(60, selectedCard.region.runId)">继续研判 +60s（该区域）</button>
              <button class="btn btn--sm btn--ghost" disabled title="尚未实现：需要新增扩大录像检索范围的接口">扩大录像检索（未实现）</button>
            </div>
            <p v-if="regionDispatch" class="card-detail-line" :data-tone="regionDispatchTone">
              <span class="dim">调度回执：</span>{{ regionDispatch.message }}
              <span class="dim num">预算 {{ regionDispatch.previousBudgetSeconds }}→{{ regionDispatch.budgetSeconds }}s（+{{ regionDispatch.addedSeconds }}）· 剩余 {{ regionDispatch.remainingSeconds }}s</span>
            </p>
            <p class="card-detail-line dim">
              +60s 只增加系统执行预算，不扩大录像检索范围；区域卡不代表目标已经到达该区域。
            </p>
          </div>
          <p v-else-if="selectedCard.kind === 'anchor'" class="card-detail-line">
            起始目标证据 · {{ bboxText(selectedCard) }} · 尚未关联到研判任务
          </p>
          <p v-if="anchorCard && timelineAnchorLinked && selectedCard.kind !== 'anchor'" class="card-detail-line dim">
            起始目标证据已关联本次研判：{{ bboxText(anchorCard) }}
          </p>
          <p v-if="playbackText" class="card-detail-line"><span class="dim">回看：</span>{{ playbackText }}</p>
          <p v-else class="card-detail-line dim">这张卡没有可回看的片段位置。</p>
          <div v-if="selectedCard.playback?.clipId" class="card-play">
            <button v-if="!playbackOpen" class="btn btn--sm btn--outline" @click="openPlayback(selectedCard)">
              <AppIcon name="play" :size="12" />回看片段（从偏移前 30s 起）
            </button>
            <button v-else class="btn btn--sm btn--ghost" @click="closePlayback">
              <AppIcon name="close" :size="12" />收起回看
            </button>
            <p v-if="playbackNote" class="card-detail-line dim">{{ playbackNote }}</p>
            <video
              v-if="playbackOpen && playbackUrl"
              ref="playbackVideo"
              class="card-play-video"
              :src="playbackUrl"
              controls
              preload="metadata"
              @loadedmetadata="seekPlaybackVideo"
            />
            <p v-if="playbackOpen" class="card-detail-line dim">
              这是 H.264 代理片段（重编码）；容器时间戳尚未与画面 OSD 核对，只能算“大致位置”。
            </p>
          </div>
          <p class="card-detail-line dim">身份只由审核事件改变；空间质量只描述几何，两者不互相升级。</p>
        </div>
      </template>
      <template v-else>
        <div v-if="timelineLoading" class="rail-empty">
          <span class="rail-empty-icon"><AppIcon name="clock" :size="16" /></span>
          <p class="rail-empty-title">正在读取时间轴</p>
          <p class="rail-empty-text">卡片由后端按既有记录派生，不在浏览器里拼事实。</p>
        </div>
        <div v-else class="rail-empty">
          <span class="rail-empty-icon"><AppIcon name="clock" :size="16" /></span>
          <p class="rail-empty-title">{{ emptyTitle }}</p>
          <p class="rail-empty-text">{{ emptyText }}</p>
          <button v-if="!wb.caseId" class="btn btn--sm btn--outline" @click="actions.openCases()">
            选择案件
          </button>
          <button v-else-if="!hasTarget" class="btn btn--sm btn--outline" @click="actions.openTracking()">
            <AppIcon name="target" :size="12" />去启动研判
          </button>
        </div>
        <p v-if="timelineError" class="rail-error">{{ timelineError }}</p>
      </template>
    </div>

    <details v-if="timelineEvents.length || timelineCards.length" class="log-block">
      <summary class="log-head">
        <span>研判日志</span>
        <span class="dim num">{{ timelineEvents.length }} 条</span>
      </summary>
      <ul class="log-list">
        <li v-for="event in timelineEvents" :key="event.eventId" class="log-row">
          <span class="num log-time">{{ eventTime(event.at) }}</span>
          <span class="log-type">{{ eventLabel(event.type) }}</span>
          <span class="dim log-status">{{ event.status || "" }}</span>
        </li>
      </ul>
      <p v-if="!timelineEvents.length" class="rail-empty-text">还没有任务事件。</p>
    </details>

    <footer class="rail-foot">
      <span class="dim">按真实观测时间排序 · 不构成身份结论</span>
      <span class="dim num">{{ timelineCards.length }} 条</span>
    </footer>
  </aside>
</template>

<style scoped>
.rail {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #080c12;
  border-left: 1px solid var(--line);
}

.rail-resizer {
  position: absolute;
  left: -4px;
  top: 0;
  bottom: 0;
  z-index: 20;
  width: 8px;
  cursor: col-resize;
  touch-action: none;
}

.rail-resizer::after {
  content: "";
  position: absolute;
  left: 3px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: transparent;
  transition: background var(--dur) var(--ease);
}

.rail-resizer:hover::after,
.rail-resizer:active::after {
  background: var(--accent-line);
}

.rail-head {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 42px;
  padding: 0 10px 0 12px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.rail-mark {
  display: grid;
  place-items: center;
  color: var(--accent-hi);
}

.rail-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-0);
}

.rail-count {
  font-size: 12.5px;
  color: var(--text-3);
}

.rail-refresh {
  margin-left: auto;
  width: 24px;
  height: 24px;
}

.rail-filters {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  flex: none;
}

.select--sm {
  height: 28px;
  font-size: 12.5px;
  padding: 0 6px;
  border-radius: 6px;
}

.rail-list {
  flex: 1;
  min-height: 0;
  padding: 6px 10px 12px;
}

.day-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0 8px;
}

.day-toggle {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1px solid var(--line-3);
  color: var(--text-3);
}

.day-label {
  font-size: 12.5px;
  color: var(--text-2);
}

.tl {
  position: relative;
  display: grid;
  gap: 6px;
}

.tl::before {
  content: "";
  position: absolute;
  left: 7px;
  top: 8px;
  bottom: 8px;
  width: 1px;
  background: var(--line-2);
}

.tl-row {
  position: relative;
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  gap: 8px;
  align-items: start;
}

.tl-dot {
  position: relative;
  z-index: 1;
  width: 9px;
  height: 9px;
  margin: 9px 0 0 3px;
  border-radius: 50%;
  background: var(--text-3);
  box-shadow: 0 0 0 3px #080c12;
}

.tl-dot[data-tone="ok"] {
  background: var(--ok);
}

.tl-dot[data-tone="warn"] {
  background: var(--warn);
}

.tl-dot[data-tone="info"] {
  background: var(--accent);
}

.tl-dot[data-tone="anchor"] {
  background: var(--violet);
}

.tl-card {
  display: grid;
  gap: 6px;
  width: 100%;
  padding: 8px 9px 9px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: #0c121a;
  text-align: left;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.tl-card:hover {
  border-color: var(--line-3);
}

.tl-row.is-selected .tl-card {
  border-color: var(--accent-line);
  background: #0d1622;
}

.tl-top {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.tl-time {
  font-size: 13.5px;
  font-weight: 650;
  color: var(--text-0);
  letter-spacing: 0.01em;
}

.tl-cam {
  font-size: 12.5px;
  color: var(--text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tl-el {
  flex: none;
  padding: 1px 6px;
  border-radius: 4px;
  background: #5b3fd0;
  color: #efe9ff;
  font-size: 11.5px;
  font-weight: 600;
}

.tl-round {
  margin-left: auto;
  flex: none;
  font-size: 11px;
  color: var(--text-4);
}

.tl-mid {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tl-thumb {
  flex: none;
  width: 62px;
  height: 42px;
  border-radius: 5px;
  overflow: hidden;
  border: 1px solid var(--line-2);
  background: #0a0f15;
}

.tl-thumb :deep(.mock-frame) {
  width: 100%;
  height: 100%;
  border-radius: 0;
}

.tl-status {
  flex: none;
  padding: 3px 9px;
  border-radius: 5px;
  font-size: 12.5px;
  font-weight: 600;
  color: #06110c;
  background: var(--text-3);
}

.tl-status[data-tone="ok"],
.tl-status[data-tone="anchor"] {
  background: #24c48b;
  color: #04150e;
}

.tl-status[data-tone="warn"] {
  background: #f0a63c;
  color: #1a1003;
}

.tl-status[data-tone="info"] {
  background: #3b82f6;
  color: #f2f7ff;
}

.tl-status[data-tone="muted"] {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-1);
}

.tl-act {
  flex: none;
  padding: 3px 9px;
  border-radius: 5px;
  border: 1px solid var(--line-3);
  background: rgba(77, 141, 255, 0.08);
  color: #8fb6ff;
  font-size: 12.5px;
}

.tl-note {
  font-size: 12.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tl-sub {
  font-size: 12.5px;
  line-height: 1.4;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tl-sub-label {
  color: var(--text-4);
}

.rail-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 30px;
  padding: 0 10px;
  border-top: 1px solid var(--line);
  font-size: 11px;
  flex: none;
}

.rail-empty {
  display: grid;
  justify-items: center;
  gap: 6px;
  padding: 26px 16px;
  text-align: center;
}

.rail-empty-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px dashed var(--line-3);
  color: var(--text-3);
}

.rail-empty-title {
  font-size: var(--fs-label);
  color: var(--text-1);
  font-weight: 600;
}

.rail-empty-text {
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-3);
}

/* B8 V1.1 时间轴卡片：无坐标的卡明写“无地图坐标”，不靠留空让人猜 */
.rail-filters--cards { grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr) auto; align-items: center; }
.rail-phase { font-size: 11.5px; color: var(--text-3); white-space: nowrap; }
.rail-error { margin: 4px 0 6px; font-size: 11.5px; color: var(--danger); }
.rail-note { margin: 4px 0 6px; font-size: 11px; line-height: 1.5; color: var(--text-4); }
.tl-sub--badges { display: flex; flex-wrap: wrap; gap: 4px; white-space: normal; overflow: visible; }
.tl-badge { padding: 1px 5px; border-radius: 3px; border: 1px solid var(--line-3); font-size: 10.5px; color: var(--text-3); }
.tl-badge[data-tone="ok"] { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
.tl-badge[data-tone="warn"] { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
.tl-badge[data-tone="muted"] { color: var(--text-4); }
.card-detail { margin: 8px 0 4px; padding: 8px; border: 1px solid var(--line-2); border-radius: 7px; background: var(--bg-1); display: flex; flex-direction: column; gap: 4px; }
.card-detail-head { display: flex; align-items: baseline; justify-content: space-between; gap: 6px; font-size: 12px; }
.card-detail-line { margin: 0; font-size: 11.5px; line-height: 1.5; color: var(--text-2); word-break: break-all; }
.card-play { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; margin-top: 2px; }
.card-play-video { width: 100%; max-height: 220px; border-radius: 6px; background: #000; }
.log-block { flex: none; max-height: 180px; display: flex; flex-direction: column; border-top: 1px solid var(--line-2); padding: 6px 10px 8px; }
.log-head { display: flex; align-items: center; justify-content: space-between; font-size: 11.5px; color: var(--text-2); cursor: pointer; list-style: none; }
.log-head::-webkit-details-marker { display: none; }
.log-list { margin: 6px 0 0; padding: 0; list-style: none; overflow-y: auto; min-height: 0; display: flex; flex-direction: column; gap: 3px; }
.log-row { display: flex; gap: 6px; font-size: 11px; color: var(--text-3); }
.log-time { color: var(--text-4); }
.log-type { color: var(--text-2); }
.log-status { margin-left: auto; }

/* B8 tracking cards */
.track-block { display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; border-bottom: 1px solid var(--line-2); }
.track-live { display: flex; align-items: center; gap: 8px; padding: 7px 8px; border: 1px solid color-mix(in srgb, var(--ok) 45%, transparent); border-radius: 7px; background: color-mix(in srgb, var(--ok) 8%, transparent); }
.track-live.is-over { border-color: color-mix(in srgb, var(--danger) 50%, transparent); }
.track-live-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); animation: track-pulse 1.2s ease-in-out infinite; }
.track-live.is-over .track-live-dot { background: var(--danger); animation: none; }
@keyframes track-pulse { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
.track-live-main { display: flex; flex-direction: column; gap: 1px; flex: 1; min-width: 0; font-size: 12px; }
.track-over { margin: 0; font-size: 11px; color: var(--danger); }
.track-card { border: 1px solid var(--line-2); border-radius: 7px; padding: 6px; background: var(--bg-1); }
.track-card summary { display: flex; gap: 8px; cursor: pointer; list-style: none; }
.track-card summary::-webkit-details-marker { display: none; }
.track-thumb { flex: 0 0 64px; height: 40px; border-radius: 4px; border: 1px dashed var(--line-3); background: var(--bg-2); color: var(--text-3); font-size: 9px; display: flex; align-items: center; justify-content: center; text-align: center; line-height: 1.3; }
.track-thumb--lg { flex: none; width: 100%; height: 120px; margin: 8px 0 4px; }
.track-info { display: flex; flex-direction: column; gap: 1px; font-size: 12px; min-width: 0; }
.track-detail { font-size: 11px; }

/* B8 V1.2：一级筛选是研判视角，不是卡片类型；区域卡只高亮设备，不画目标线 */
.rail-filters--views { display: flex; flex-direction: column; gap: 6px; }
.view-tabs { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 4px; }
.view-tab { display: flex; align-items: center; justify-content: center; gap: 3px; padding: 5px 2px; border: 1px solid var(--line-3); border-radius: 6px; background: transparent; color: var(--text-3); font-size: 11px; line-height: 1.2; }
.view-tab.is-active { border-color: var(--accent-line); color: var(--text-0); background: rgba(77, 141, 255, 0.12); }
.view-meta { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
.region-block { display: flex; flex-direction: column; gap: 5px; padding: 7px; border: 1px solid var(--line-2); border-radius: 6px; background: rgba(59, 130, 246, 0.05); }
.region-tasks { margin: 2px 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 5px; max-height: 220px; overflow-y: auto; }
.region-task { display: flex; flex-direction: column; gap: 2px; padding: 5px 6px; border: 1px dashed var(--line-2); border-radius: 5px; }
.region-task-head { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 11.5px; color: var(--text-1); }
.region-task-meta { font-size: 10.5px; line-height: 1.45; }
.region-budget { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; font-size: 10.5px; }
</style>
