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

const canStart = computed(() => Boolean(wb.caseId && observationId.value && clipId.value && !trackingLoading.value));
const isBusy = computed(() => {
  const status = activeTrackingRun.value?.status;
  return status === "queued" || status === "running";
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
    clip_id: clipId.value,
    offset_seconds: Number(offset.value) || 0,
    max_windows: Number(maxWindows.value) || 2,
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

onBeforeUnmount(stopPolling);
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
          <span class="field-label">起始录像段（已匹配摄像头）</span>
          <select v-model="clipId" class="input">
            <option value="">选择一段录像</option>
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
        <div v-if="trackingWindows.length" class="window-list">
          <div v-for="win in trackingWindows" :key="win.id" class="window-row">
            <span class="num">{{ win.camera_label }}</span>
            <span class="num">{{ clock(win.window[0]) }}–{{ clock(win.window[1]) }}</span>
            <span>{{ win.status }}</span>
            <span class="num">轨迹 {{ win.tracks }}</span>
            <span class="num muted">取帧 {{ win.cost.frames_saved ?? 0 }}</span>
          </div>
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
              <button class="btn btn--sm btn--primary" :disabled="cand.identity_status === 'human_confirmed'" @click="actions.reviewTracking(cand.id, 'approve')">
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
  grid-template-columns: minmax(220px, 1.2fr) minmax(260px, 1.4fr) 120px 120px auto;
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
