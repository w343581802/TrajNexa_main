<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import {
  actions,
  activeTrackingRun,
  caseObservations,
  currentCaseName,
  recordingClips,
  trackingCandidates,
  trackingError,
  trackingLoading,
  trackingRuns,
  trackingReviews,
  trackingStatusText,
  trackingWindows,
  ui,
  wb,
} from "../../store/workbench";

const observationId = ref("");
const clipId = ref("");
const offset = ref(0);
const maxWindows = ref(2);
const direction = ref<"forward" | "backward">("forward");
const nowMs = ref(Date.now());
let ticker: number | null = null;

const matchedClips = computed(() =>
  recordingClips.value
    .filter((clip) => clip.camera_name && clip.duration_seconds && clip.duration_seconds > 0)
    .filter((clip) => clip.match_status.startsWith("matched") || clip.match_status.startsWith("suggested"))
    .sort((a, b) => (a.start_local ?? "").localeCompare(b.start_local ?? "")),
);

const observationLabel = (obs: (typeof caseObservations.value)[number]) => {
  const bbox = obs.analysis_json?.bbox_pixels;
  const size = Array.isArray(bbox) && bbox.length === 4 ? ",已框选" : "";
  return `${obs.object_class} · ${obs.id.slice(0, 8)} · ${obs.review_state}${size}`;
};

const clipLabel = (clip: (typeof matchedClips.value)[number]) => {
  const start = clip.start_local ? clip.start_local.replace("T", " ") : "时间未知";
  const seconds = clip.duration_seconds ? Math.round(clip.duration_seconds) : 0;
  return `${clip.camera_name ?? "未命名镜头"} · ${start} · ${seconds}s`;
};

const statusTone = computed(() => {
  const status = activeTrackingRun.value?.status;
  if (status === "failed" || status === "cancelled") return "danger";
  if (status === "awaiting_review" || status === "budget_exhausted") return "warn";
  if (status === "unresolved") return "muted";
  return "ok";
});

const canStart = computed(() => Boolean(wb.caseId && observationId.value && !trackingLoading.value));
const isBusy = computed(() => {
  const status = activeTrackingRun.value?.status;
  return status === "queued" || status === "running";
});

const activeWindow = computed(() => activeTrackingRun.value?.active ?? null);
const elapsedSeconds = computed(() => {
  const started = activeWindow.value?.started_at;
  if (!started) return 0;
  return Math.max(0, Math.floor((nowMs.value - new Date(started).getTime()) / 1000));
});
const researchLimit = computed(() => activeTrackingRun.value?.research_seconds ?? 120);
const researchExceeded = computed(() => elapsedSeconds.value > researchLimit.value);

/** 同一小区域（镜头名前缀）下的镜头作为树分组，便于管理正在研判的镜头 */
const cameraArea = (label: string) => label.split("-").slice(0, 2).join("-") || "未分区";
const windowTree = computed(() => {
  const groups: Record<string, typeof trackingWindows.value> = {};
  for (const win of trackingWindows.value) {
    const key = cameraArea(win.camera_label);
    (groups[key] ||= []).push(win);
  }
  return Object.entries(groups).map(([area, windows]) => ({ area, windows }));
});

const clock = (seconds: number | null | undefined) => {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.floor(seconds);
  return `${String(Math.floor(total / 3600)).padStart(2, "0")}:${String(Math.floor((total % 3600) / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
};

const identityText = (status: string) => {
  if (status === "human_confirmed") return "人工已确认";
  if (status === "human_rejected") return "人工已否决";
  return "待核实候选";
};

const start = async () => {
  if (!canStart.value) return;
  const created = await actions.startTrackingRun({
    observation_id: observationId.value,
    ...(clipId.value ? { clip_id: clipId.value } : {}),
    offset_seconds: Number(offset.value) || 0,
    max_windows: Number(maxWindows.value) || 2,
    direction: direction.value,
  });
  if (created) poll();
};

let timer: number | null = null;
const stopPolling = () => {
  if (timer !== null) {
    window.clearTimeout(timer);
    timer = null;
  }
};

const poll = () => {
  stopPolling();
  const pollOnce = async () => {
    const run = activeTrackingRun.value;
    if (!run || !wb.caseId) return;
    await actions.openTrackingRefresh();
    if (isBusy.value) timer = window.setTimeout(pollOnce, 2500);
  };
  if (isBusy.value) timer = window.setTimeout(pollOnce, 1500);
};

watch(
  () => ui.trackingOpen,
  (open) => {
    if (open) {
      void actions.openTracking();
      poll();
    } else {
      stopPolling();
    }
    if (ticker !== null) window.clearInterval(ticker);
    if (open) ticker = window.setInterval(() => { nowMs.value = Date.now(); }, 1000);
  },
);

watch(
  () => activeTrackingRun.value?.id,
  () => poll(),
);

watch(
  () => activeTrackingRun.value?.status,
  () => poll(),
);

onBeforeUnmount(() => {
  stopPolling();
  if (ticker !== null) window.clearInterval(ticker);
});
</script>

<template>
  <BaseModal
    :open="ui.trackingOpen"
    title="案件追踪任务"
    :subtitle="wb.caseId ? `${currentCaseName} · 起点必须是案件内已人工框选的目标观察` : '先选择案件'"
    width="xl"
    @close="ui.trackingOpen = false"
  >
    <div v-if="!wb.caseId" class="empty">请先在左上角选择或新建案件。</div>

    <template v-else>
      <section class="start-box">
        <label class="field">
          <span class="field-label">目标观察（M4a 人工框选）</span>
          <select v-model="observationId" class="input">
            <option value="">选择案件内的观察</option>
            <option v-for="obs in caseObservations" :key="obs.id" :value="obs.id">
              {{ observationLabel(obs) }}
            </option>
          </select>
        </label>
        <label class="field">
          <span class="field-label">起始录像段（可选；留空则按照片角标时间自动找）</span>
          <select v-model="clipId" class="input">
            <option value="">自动从案件录像定位</option>
            <option v-for="clip in matchedClips" :key="clip.id" :value="clip.id">
              {{ clipLabel(clip) }}
            </option>
          </select>
        </label>
        <label class="field field--narrow">
          <span class="field-label">起点偏移（秒）</span>
          <input v-model.number="offset" class="input num" type="number" min="0" step="0.5" />
        </label>
        <label class="field field--narrow">
          <span class="field-label">最多分析窗口</span>
          <input v-model.number="maxWindows" class="input num" type="number" min="1" max="40" step="1" />
        </label>
        <div class="field field--narrow">
          <span class="field-label">研判方向</span>
          <div class="dir-toggle" role="group" aria-label="研判方向">
            <button type="button" :class="{ 'is-on': direction === 'forward' }"
              @click="direction = 'forward'">向前</button>
            <button type="button" :class="{ 'is-on': direction === 'backward' }"
              @click="direction = 'backward'">向后</button>
          </div>
        </div>
        <button class="btn btn--primary start-btn" :disabled="!canStart" @click="start">
          <AppIcon name="target" :size="14" />{{ trackingLoading ? "创建中…" : "开始追踪" }}
        </button>
      </section>

      <p class="hint">
        后台会从这段真实录像出发，按时间窗分析后续镜头；候选默认“待核实”，
        只有你在这里通过/否决才会改变身份状态。通过的候选会让任务继续往后搜索。
      </p>

      <p v-if="trackingError" class="error">{{ trackingError }}</p>

      <section v-if="activeTrackingRun" class="run-box">
        <header class="run-head">
          <span class="chip" :data-tone="statusTone">{{ trackingStatusText(activeTrackingRun) }}</span>
          <span class="run-id num">任务 {{ activeTrackingRun.id.slice(0, 8) }}</span>
          <span class="run-anchor">
            起点 {{ activeTrackingRun.anchor.camera_label }} · {{ clock(activeTrackingRun.anchor.anchor_seconds) }}
            · 时钟 {{ activeTrackingRun.anchor.clock_quality }}
          </span>
          <button v-if="isBusy" class="btn btn--danger btn--sm" @click="actions.cancelTracking()">
            <AppIcon name="stop" :size="12" />取消任务
          </button>
        </header>
        <div v-if="activeWindow" class="live-card" :class="{ 'is-over': researchExceeded }">
          <span class="live-dot" />
          <div class="live-main">
            <span class="live-label">正在研判：{{ activeWindow.camera_label }}</span>
            <span class="live-sub num">
              {{ clock(activeWindow.window[0]) }}–{{ clock(activeWindow.window[1]) }}
              · 已研判 {{ elapsedSeconds }}s / 窗口 {{ researchLimit }}s
            </span>
          </div>
          <button class="btn btn--sm btn--outline" @click="actions.extendTracking(60)">
            +60s
          </button>
        </div>
        <p v-if="researchExceeded" class="error">
          已超过系统研判时间窗口；可以点 +60s 延长，或取消任务。不会无限计时。
        </p>
        <p v-if="activeTrackingRun.error" class="error">
          {{ activeTrackingRun.error.code }}：{{ activeTrackingRun.error.message }}
        </p>
        <p v-if="activeTrackingRun.progress.reason" class="hint">
          没有可分析录像：{{ activeTrackingRun.progress.reason }}
        </p>
        <div class="facts num">
          <span>窗口 <b>{{ activeTrackingRun.windows }}</b></span>
          <span>候选 <b>{{ activeTrackingRun.candidates }}</b></span>
          <span>审核事件 <b>{{ activeTrackingRun.reviews }}</b></span>
          <span>已用帧 <b>{{ activeTrackingRun.progress.frames_used ?? 0 }}</b></span>
        </div>

        <h3 class="sub-title">已分析窗口</h3>
        <div v-if="trackingWindows.length" class="window-tree">
          <details v-for="group in windowTree" :key="group.area" open>
            <summary class="tree-area">{{ group.area }} · {{ group.windows.length }} 个镜头</summary>
            <div v-for="win in group.windows" :key="win.id" class="window-row">
              <span class="num">{{ win.camera_label }}</span>
              <span class="num">{{ clock(win.window[0]) }}–{{ clock(win.window[1]) }}</span>
              <span>{{ win.status }}</span>
              <span class="num">轨迹 {{ win.tracks }}</span>
              <span class="num muted">取帧 {{ win.cost.frames_saved ?? 0 }}</span>
            </div>
          </details>
        </div>
        <p v-else class="hint">还没有窗口结果；任务可能还在排队或没有覆盖该时段的录像。</p>

        <h3 class="sub-title">候选轨迹</h3>
        <div v-if="trackingCandidates.length" class="cand-list">
          <article v-for="cand in trackingCandidates" :key="cand.id" class="cand-card">
            <header>
              <span class="chip" :data-tone="cand.identity_status === 'human_confirmed' ? 'ok' : cand.identity_status === 'human_rejected' ? 'danger' : 'warn'">
                {{ identityText(cand.identity_status) }}
              </span>
              <span class="num">{{ cand.camera_label }} · track {{ cand.track_id }}</span>
              <span class="num score">相似度 {{ cand.retrieval_score ?? "—" }} · 间距 {{ cand.margin_top1_top2 ?? "—" }}</span>
              <span class="num muted">{{ clock(cand.window[0]) }}–{{ clock(cand.window[1]) }}</span>
            </header>
            <p class="cand-meta">
              状态 {{ cand.state }} · 证据 {{ cand.evidence_refs.length }} 张
              <template v-if="cand.details.unknown?.length"> · 未知：{{ cand.details.unknown.join("、") }}</template>
            </p>
            <div class="cand-actions">
              <button class="btn btn--sm btn--primary" :disabled="cand.identity_status === 'human_confirmed'" @click="actions.rememberCandidateObservation(cand); actions.reviewTracking(cand.id, 'approve')">
                通过并继续搜索
              </button>
              <button class="btn btn--sm btn--outline" :disabled="cand.identity_status === 'human_rejected'" @click="actions.reviewTracking(cand.id, 'reject')">
                否决
              </button>
              <button class="btn btn--sm btn--ghost" :disabled="cand.identity_status === 'candidate_unverified'" @click="actions.reviewTracking(cand.id, 'revoke')">
                撤销
              </button>
            </div>
          </article>
        </div>
        <p v-else class="hint">暂无候选。分析完成仍无候选时，任务状态会是“未找到候选”。</p>

        <template v-if="trackingReviews.length">
          <h3 class="sub-title">审核记录（append-only）</h3>
          <ul class="review-list">
            <li v-for="event in trackingReviews" :key="event.id">
              <span class="num">{{ event.decision }}</span>
              <span>{{ event.reviewer }}</span>
              <span class="muted">{{ event.created_at }}</span>
              <span v-if="event.note">{{ event.note }}</span>
            </li>
          </ul>
        </template>
      </section>

      <section v-else-if="trackingRuns.length" class="run-box">
        <h3 class="sub-title">本案件的任务</h3>
        <ul class="run-list">
          <li v-for="run in trackingRuns.slice(0, 8)" :key="run.id">
            <span class="num">{{ run.id.slice(0, 8) }}</span>
            <span>{{ trackingStatusText(run) }}</span>
            <span class="num">{{ run.anchor.camera_label }}</span>
            <span class="num">{{ run.candidates }} 候选</span>
            <button class="btn btn--sm btn--ghost" @click="actions.openTrackingRefresh(run.id)">查看</button>
          </li>
        </ul>
      </section>
    </template>

    <template #footer>
      <button class="btn btn--ghost" @click="ui.trackingOpen = false">关闭</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.empty { padding: 28px; color: var(--text-2); }
.start-box {
  display: grid;
  grid-template-columns: minmax(200px, 1.2fr) minmax(240px, 1.4fr) 110px 100px 120px auto;
  gap: 10px;
  align-items: end;
}
.field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.field-label { font-size: 11px; color: var(--text-3); }
.input {
  height: 32px;
  padding: 0 8px;
  color: var(--text-1);
  background: var(--bg-2);
  border: 1px solid var(--line-2);
  border-radius: 6px;
  font-size: 12px;
  min-width: 0;
}
.start-btn { height: 32px; white-space: nowrap; }
.live-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--ok) 45%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, var(--ok) 8%, transparent);
}
.live-card.is-over { border-color: color-mix(in srgb, var(--danger) 50%, transparent); }
.live-dot {
  width: 8px; height: 8px; border-radius: 50%; background: var(--ok);
  animation: live-pulse 1.2s ease-in-out infinite;
}
.live-card.is-over .live-dot { background: var(--danger); animation: none; }
@keyframes live-pulse { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
.live-main { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.live-label { font-size: 12px; color: var(--text-1); }
.live-sub { font-size: 11px; color: var(--text-3); }
.dir-toggle { display: flex; gap: 1px; overflow: hidden; border: 1px solid var(--line-2); border-radius: 6px; }
.dir-toggle button {
  flex: 1; height: 30px; border: 0; background: var(--bg-2); color: var(--text-3);
  font-size: 12px; cursor: pointer;
}
.dir-toggle button.is-on { background: var(--accent, #2b6cb0); color: #fff; }
.window-tree { display: flex; flex-direction: column; gap: 6px; }
.tree-area { font-size: 12px; color: var(--text-2); cursor: pointer; padding: 2px 0; }
.hint { margin: 10px 0 0; font-size: 12px; color: var(--text-3); line-height: 1.6; }
.error { margin: 10px 0 0; font-size: 12px; color: var(--danger); }
.run-box { margin-top: 14px; border-top: 1px solid var(--line-1); padding-top: 12px; }
.run-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.run-id, .run-anchor { font-size: 12px; color: var(--text-3); }
.chip {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  border: 1px solid var(--line-2);
  color: var(--text-2);
}
.chip[data-tone="ok"] { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
.chip[data-tone="warn"] { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
.chip[data-tone="danger"] { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 45%, transparent); }
.facts { display: flex; gap: 16px; margin-top: 8px; font-size: 12px; color: var(--text-3); }
.sub-title { margin: 16px 0 8px; font-size: 12px; color: var(--text-2); font-weight: 600; }
.window-list { display: flex; flex-direction: column; gap: 4px; }
.window-row {
  display: grid;
  grid-template-columns: 90px 150px 110px 80px 100px;
  gap: 10px;
  font-size: 12px;
  color: var(--text-2);
  padding: 5px 8px;
  background: var(--bg-2);
  border-radius: 6px;
}
.cand-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; }
.cand-card { border: 1px solid var(--line-2); border-radius: 8px; padding: 10px; background: var(--bg-1); }
.cand-card header { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; }
.score { color: var(--text-2); }
.cand-meta { margin: 8px 0; font-size: 12px; color: var(--text-3); }
.cand-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.review-list { margin: 0; padding-left: 16px; font-size: 12px; color: var(--text-2); }
.review-list li { margin-bottom: 4px; display: flex; gap: 8px; flex-wrap: wrap; }
.run-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.run-list li { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--text-2); }
.muted { color: var(--text-3); }
@media (max-width: 1100px) {
  .start-box { grid-template-columns: 1fr 1fr; }
}
</style>
