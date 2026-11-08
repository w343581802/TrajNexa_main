<script setup lang="ts">
/**
 * 批量自动标定：一次把夜空里所有「参数齐全且有参照帧」的镜头都发起一遍。
 *
 * 为什么要有这个入口：单台标定藏在镜头卡第三页签里，31 台要点 31 次；
 * 而自动标定的价值恰恰在「先把能算的都算一遍」，再人工审核几条。
 *
 * 三条硬边界（与单台一致，前端不做任何放宽）：
 * - 参数不齐的镜头不出作业：blockers 由 Map 判定，前端只显示不补齐；
 * - 发起不等于通过：作业成功只生成 experimental 候选，不会写正式 L2；
 * - 审核通过必须带独立留出真值 p90，这个弹窗只负责发起，不提供「一键通过」。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import { cameraCode, loadMapSnapshot, type SnapshotCamera } from "../../services/mapSnapshot";
import { mainApi, type AutoCalibrationJob, type AutoCalibrationOptions } from "../../services/mainApi";
import { toast, ui } from "../../store/workbench";

type RowState = {
  status: "idle" | "loading" | "ready" | "error";
  ready: boolean;
  blockers: string[];
  error: string;
  job: AutoCalibrationJob | null;
  /** 该镜头的水平 FOV 是台账真值还是业务默认 80°（缺省补齐）。 */
  fovDefault: boolean;
  /** 该镜头当前有多少条候选（作业完成后由刷新补齐）。 */
  candidates: number;
  busy: boolean;
};

const rows = ref<Record<string, RowState>>({});
const cameras = ref<SnapshotCamera[]>([]);
const scanning = ref(false);
const batching = ref(false);
const refreshing = ref(false);
const scanned = ref(0);
const actor = ref("批量标定");
let pollTimer: number | null = null;

const BLOCKER_TEXT: Record<string, string> = {
  auto_calibration_disabled: "主系统未开启自动标定（B8_AUTO_CALIBRATION_ENABLED）",
  auto_calibration_unavailable: "未配置 Map 接口地址（MAP_API_BASE_URL）",
  map_unreachable: "Map 服务不可达",
  no_video_frame: "Map 侧没有录像参考帧",
  no_reference_frame: "Map 侧没有参考帧",
};

/** Map 的 blocker 有两种形态：错误码，或「缺少水平视场角 horizontal_fov_deg」这种句子。 */
const BLOCKER_FIELDS: Record<string, string> = {
  horizontal_fov_deg: "水平视场角",
  mount_height_m: "安装高度",
  bearing_deg: "方位角",
  tilt_deg: "俯角",
};

function blockerText(value: string): string {
  if (BLOCKER_TEXT[value]) return BLOCKER_TEXT[value];
  const field = Object.keys(BLOCKER_FIELDS).find((key) => value.includes(key));
  if (field) return `缺${BLOCKER_FIELDS[field]}（等外场台账）`;
  return value;
}

const JOB_STATUS: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  done: "已完成",
  failed: "失败",
  blocked: "参数不齐",
};

function jobLabel(status: string | undefined): string {
  if (!status) return "已发起";
  return JOB_STATUS[status] ?? status;
}

function jobTone(status: string | undefined): string {
  if (status === "done") return "bc-ok";
  if (status === "failed" || status === "blocked") return "bc-bad";
  return "bc-dim";
}

function rowOf(camera: SnapshotCamera): RowState {
  const key = camera.camera_uuid ?? `id:${camera.camera_id}`;
  if (!rows.value[key]) {
    rows.value[key] = { status: "idle", ready: false, blockers: [], error: "",
                        fovDefault: false, job: null, candidates: 0, busy: false };
  }
  return rows.value[key];
}

const targetCameras = computed(() => cameras.value.filter((camera) => Boolean(camera.camera_uuid)));

const readyCount = computed(() =>
  targetCameras.value.filter((camera) => rowOf(camera).status === "ready" && rowOf(camera).ready).length,
);

const blockedCount = computed(() =>
  targetCameras.value.filter((camera) => rowOf(camera).status === "ready" && !rowOf(camera).ready).length,
);

const errorCount = computed(() =>
  targetCameras.value.filter((camera) => rowOf(camera).status === "error").length,
);

const jobCount = computed(() =>
  targetCameras.value.filter((camera) => rowOf(camera).job !== null).length,
);

const candidateTotal = computed(() =>
  targetCameras.value.reduce((sum, camera) => sum + rowOf(camera).candidates, 0),
);

const pendingCount = computed(() => targetCameras.value.length - scanned.value);

const hasRunningJob = computed(() =>
  targetCameras.value.some((camera) => {
    const status = rowOf(camera).job?.status;
    return status === "queued" || status === "running";
  }),
);

/** 逐台读取参数（Map 每次只认一台，串行最稳，也便于显示进度）。 */
async function scan(): Promise<void> {
  if (scanning.value) return;
  scanning.value = true;
  scanned.value = 0;
  rows.value = {};
  try {
    const snapshot = await loadMapSnapshot();
    cameras.value = snapshot.cameras;
  } catch (error) {
    scanning.value = false;
    toast(error instanceof Error ? error.message : "Map 空间数据不可用", "warn");
    return;
  }
  for (const camera of targetCameras.value) {
    const row = rowOf(camera);
    row.status = "loading";
    try {
      const options: AutoCalibrationOptions = await mainApi.autoCalibrationOptions(camera.camera_uuid as string);
      row.ready = options.ready;
      row.blockers = options.blockers ?? [];
      row.fovDefault = options.pose?.horizontal_fov_source === "default_80";
      row.status = "ready";
      row.error = "";
    } catch (error) {
      row.ready = false;
      row.status = "error";
      row.error = error instanceof Error ? error.message : String(error);
    }
    scanned.value += 1;
  }
  await loadKnownJobs();
  await loadCandidateCounts();
  scanning.value = false;
}

/**
 * 一次拉全量作业（`camera_uuid` 传空串时 Map 返回全部），按镜头取最近一条。
 * 这样重开弹窗也能看到以前发起过、已经出候选的镜头，而不是从「未发起」重来。
 */
async function loadKnownJobs(): Promise<void> {
  let items: AutoCalibrationJob[] = [];
  try {
    const payload = await mainApi.autoCalibrationJobs("");
    items = payload.items ?? [];
  } catch {
    return; // 作业列表不可读不影响参数扫描结果
  }
  const latest = new Map<string, AutoCalibrationJob>();
  for (const job of items) {
    if (!job.camera_uuid) continue;
    const seen = latest.get(job.camera_uuid);
    if (!seen || (job.created_at ?? "") > (seen.created_at ?? "")) latest.set(job.camera_uuid, job);
  }
  for (const camera of targetCameras.value) {
    const job = camera.camera_uuid ? latest.get(camera.camera_uuid) : undefined;
    if (job) rowOf(camera).job = job;
  }
}

/**
 * 批量发起：只挑 ready 的镜头，串行提交，一台失败不影响后面。
 * 作业是异步的（Map 侧排队跑），这里只报告「已提交」与「立即失败」。
 */
async function startBatch(): Promise<void> {
  if (batching.value) return;
  const targets = targetCameras.value.filter((camera) => {
    const row = rowOf(camera);
    return row.status === "ready" && row.ready && !row.busy;
  });
  if (!targets.length) {
    toast("没有参数齐全的镜头可发起；缺参数的镜头要先补台账", "warn");
    return;
  }
  batching.value = true;
  let submitted = 0;
  let failed = 0;
  for (const camera of targets) {
    const row = rowOf(camera);
    row.busy = true;
    try {
      const payload = await mainApi.requestAutoCalibrationJob({
        camera_uuid: camera.camera_uuid as string,
        actor: actor.value.trim() || "批量标定",
      });
      row.job = payload.job;
      submitted += 1;
    } catch (error) {
      row.error = error instanceof Error ? error.message : String(error);
      failed += 1;
    } finally {
      row.busy = false;
    }
  }
  batching.value = false;
  ensurePoll();
  if (failed) toast(`已提交 ${submitted} 台，失败 ${failed} 台；失败原因见列表`, "warn");
  else toast(`已提交 ${submitted} 台自动标定；作业完成后在镜头卡里审核候选`, "ok");
}

function close(): void {
  stopPoll();
  ui.batchCalibrationOpen = false;
}

/**
 * 刷新已发起镜头的作业状态与候选数。
 * 作业在 Map 侧排队跑，这里只读结果，不重试、不改状态。
 */
async function refreshJobs(): Promise<void> {
  if (refreshing.value) return;
  refreshing.value = true;
  await loadKnownJobs();
  await loadCandidateCounts();
  refreshing.value = false;
}

/** 只给已有作业的镜头数候选；没有作业的镜头不必多打一次接口。 */
async function loadCandidateCounts(): Promise<void> {
  for (const camera of targetCameras.value) {
    const row = rowOf(camera);
    if (!row.job) continue;
    try {
      const candidates = await mainApi.autoCalibrationCandidates(camera.camera_uuid as string);
      row.candidates = (candidates.items ?? []).length;
    } catch {
      /* 单台失败不影响其它镜头；状态保持上一次结果 */
    }
  }
}

function stopPoll(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

/** 还有排队/运行中的作业时每 5 秒自动刷新一次，跑完就停。 */
function ensurePoll(): void {
  if (pollTimer !== null) return;
  pollTimer = window.setInterval(() => {
    if (!ui.batchCalibrationOpen || !hasRunningJob.value) {
      stopPoll();
      return;
    }
    void refreshJobs();
  }, 5000);
}

onBeforeUnmount(stopPoll);

/** 只在打开时扫描一次；关掉不清数据，方便再打开对比。 */
watch(
  () => ui.batchCalibrationOpen,
  (open) => {
    if (open && !scanning.value) void scan();
    if (!open) stopPoll();
  },
);
</script>

<template>
  <BaseModal
    :open="ui.batchCalibrationOpen"
    title="批量自动标定"
    subtitle="对参数齐全的 Map 镜头批量发起自动标定作业。作业成功只生成实验候选，不会自动变成正式 L2。"
    width="lg"
    @close="close"
  >
    <div class="bc">
      <div class="bc-bar">
        <div class="bc-stat">
          <span class="bc-k">镜头</span>
          <b class="num">{{ targetCameras.length }}</b>
        </div>
        <div class="bc-stat">
          <span class="bc-k">参数齐全</span>
          <b class="num ok">{{ readyCount }}</b>
        </div>
        <div class="bc-stat">
          <span class="bc-k">缺参数</span>
          <b class="num warn">{{ blockedCount }}</b>
        </div>
        <div class="bc-stat">
          <span class="bc-k">已提交</span>
          <b class="num">{{ jobCount }}</b>
        </div>
        <div class="bc-stat">
          <span class="bc-k">已出候选</span>
          <b class="num ok">{{ candidateTotal }}</b>
        </div>
        <span class="grow" />
        <label class="bc-actor">
          <span>操作人</span>
          <input v-model="actor" class="input" placeholder="操作人" />
        </label>
        <button class="btn btn--ghost btn--sm" :disabled="scanning || batching" @click="scan">
          <AppIcon name="refresh" :size="12" />{{ scanning ? `读取中 ${scanned}/${targetCameras.length}` : "重新读取参数" }}
        </button>
        <button class="btn btn--ghost btn--sm" :disabled="refreshing || !jobCount" @click="refreshJobs">
          <AppIcon name="clock" :size="12" />{{ refreshing ? "刷新中…" : "刷新作业状态" }}
        </button>
        <button class="btn btn--primary btn--sm" :disabled="scanning || batching || readyCount === 0" @click="startBatch">
          <AppIcon name="sliders" :size="12" />{{ batching ? "提交中…" : `批量发起（${readyCount} 台）` }}
        </button>
      </div>

      <p v-if="scanning" class="bc-hint">
        正在逐台读取 Map 标定参数：{{ scanned }}/{{ targetCameras.length }}，还剩 {{ pendingCount }} 台…
      </p>
      <p v-else-if="!targetCameras.length" class="bc-empty">Map 里没有带 camera_uuid 的镜头，无法发起标定。</p>
      <p v-else-if="errorCount" class="bc-hint warn">{{ errorCount }} 台读取失败：见下表「状态」列；其它镜头不受影响。</p>

      <div class="bc-table" role="table" aria-label="批量标定镜头列表">
        <div class="bc-row bc-row--head" role="row">
          <span role="columnheader">镜头</span>
          <span role="columnheader">参数</span>
          <span role="columnheader">作业</span>
          <span role="columnheader">候选</span>
        </div>
        <div
          v-for="camera in targetCameras"
          :key="camera.camera_uuid ?? camera.camera_id"
          class="bc-row"
          :class="{ 'is-blocked': rowOf(camera).status === 'ready' && !rowOf(camera).ready }"
          role="row"
        >
          <span class="bc-name" role="cell">
            <b>{{ cameraCode(camera) }}</b>
            <em v-if="!camera.has_video">Map 侧无参照帧</em>
          </span>
          <span class="bc-param" role="cell">
            <template v-if="rowOf(camera).status === 'loading'"><i class="bc-dim">读取中…</i></template>
            <template v-else-if="rowOf(camera).status === 'error'">
              <i class="bc-bad" :title="rowOf(camera).error">读取失败</i>
            </template>
            <template v-else-if="rowOf(camera).ready">
              <i class="bc-ok">齐全</i>
              <em v-if="rowOf(camera).fovDefault" class="bc-dim">（FOV 默认 80°）</em>
            </template>
            <template v-else>
              <i class="bc-warn">{{ rowOf(camera).blockers.map(blockerText).join("；") || "参数不齐" }}</i>
            </template>
          </span>
          <span class="bc-job" role="cell">
            <template v-if="rowOf(camera).busy"><i class="bc-dim">提交中…</i></template>
            <template v-else-if="rowOf(camera).error && !rowOf(camera).job"><i class="bc-bad" :title="rowOf(camera).error">发起失败</i></template>
            <template v-else-if="rowOf(camera).job">
              <i :class="jobTone(rowOf(camera).job?.status)">{{ jobLabel(rowOf(camera).job?.status) }}</i>
              <em v-if="rowOf(camera).job?.blockers.length" class="bc-dim">（{{ rowOf(camera).job?.blockers.map(blockerText).join("；") }}）</em>
              <em v-else-if="rowOf(camera).job?.error" class="bc-dim" :title="rowOf(camera).job?.error">（{{ rowOf(camera).job?.error }}）</em>
            </template>
            <template v-else><i class="bc-dim">未发起</i></template>
          </span>
          <span class="bc-cand" role="cell">
            <template v-if="rowOf(camera).candidates">
              <i class="bc-ok">{{ rowOf(camera).candidates }} 条</i>
            </template>
            <template v-else-if="rowOf(camera).job?.candidate_id"><i class="bc-ok">已生成</i></template>
            <template v-else><i class="bc-dim">—</i></template>
          </span>
        </div>
      </div>

      <div class="bc-note">
        <AppIcon name="info" :size="13" />
        <span>
          自动标定只用 Map 已有参照帧与镜头参数推算，候选恒为实验估计；审核通过要填独立留出真值的 p90 残差，
          没有真值就只能驳回。台账没录水平视场角的镜头按业务默认 80° 参与试算，它是假设内参、不是实测值。
        </span>
      </div>
    </div>

    <template #footer>
      <span class="dim foot-hint">不修改 Map 基础数据，不写正式 L2 标定</span>
      <span class="grow" />
      <button class="btn btn--ghost" @click="close">关闭</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.bc {
  display: grid;
  gap: 10px;
  padding: 12px 16px 14px;
}

.bc-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}

.bc-stat {
  display: flex;
  align-items: baseline;
  gap: 5px;
}

.bc-k {
  font-size: 10.5px;
  color: var(--text-3);
}

.bc-stat b {
  font-size: 13px;
}

.bc-stat b.ok { color: #4fd1d9; }
.bc-stat b.warn { color: #ffbf00; }

.bc-actor {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--text-3);
}

.bc-actor .input {
  width: 96px;
  height: 24px;
}

.bc-hint {
  margin: 0;
  font-size: 11px;
  color: var(--text-3);
}

.bc-hint.warn { color: #ffbf00; }

.bc-empty {
  margin: 0;
  padding: 18px 0;
  text-align: center;
  font-size: 11.5px;
  color: var(--text-3);
}

.bc-table {
  max-height: min(56vh, 480px);
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
}

.bc-row {
  display: grid;
  grid-template-columns: minmax(180px, 1.4fr) minmax(200px, 1.6fr) minmax(110px, 0.9fr) 76px;
  gap: 8px;
  align-items: center;
  padding: 7px 10px;
  border-bottom: 1px solid var(--line-soft, rgba(255, 255, 255, 0.05));
  font-size: 11px;
}

.bc-row:last-child { border-bottom: none; }

.bc-row--head {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--bg-2);
  font-size: 10.5px;
  color: var(--text-3);
  text-transform: none;
}

.bc-row.is-blocked { background: rgba(255, 191, 0, 0.04); }

.bc-name {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.bc-name b {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bc-name em {
  font-style: normal;
  font-size: 9.5px;
  color: var(--text-3);
}

.bc-param,
.bc-job,
.bc-cand {
  min-width: 0;
  display: flex;
  gap: 4px;
  align-items: center;
  flex-wrap: wrap;
}

.bc-param i,
.bc-job i,
.bc-cand i {
  font-style: normal;
}

.bc-ok { color: #4fd1d9; }
.bc-warn { color: #ffbf00; }
.bc-bad { color: #ff6b6b; }
.bc-dim { color: var(--text-3); }

.bc-note {
  display: flex;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-2);
  background: var(--bg-2);
}

.foot-hint { font-size: 10.5px; }
</style>