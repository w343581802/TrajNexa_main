<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { mainApi, type RecordingClip, type RecordingKeyframePreview, type M4dAttributePreview } from "../../services/mainApi";
import { wb } from "../../store/workbench";

/** Model proposals and requested video offsets are evidence candidates, not a tracklet. */
const props = defineProps<{ clip: RecordingClip }>();
const detect = ref(false);
const offsetsInput = ref("0.5,2,8");
const loading = ref(false);
const error = ref("");
const result = ref<RecordingKeyframePreview | null>(null);
const selectedOffset = ref<number | null>(null);
const frameFailed = ref(false);
const vlmBusy = ref(false);
const vlmResult = ref<M4dAttributePreview | null>(null);
const vlmError = ref("");
let requestEpoch = 0;
let vlmEpoch = 0;

watch(() => [props.clip.id, wb.caseId], () => {
  requestEpoch += 1;
  vlmEpoch += 1;
  detect.value = false;
  offsetsInput.value = "0.5,2,8"; error.value = "";
  result.value = null; selectedOffset.value = null; frameFailed.value = false;
  vlmResult.value = null; vlmError.value = ""; vlmBusy.value = false;
});

const currentFrame = computed(() => result.value?.frames.find(
  (f) => f.requested_offset_seconds === selectedOffset.value,
) ?? null);
const imageUrl = computed(() => wb.caseId && selectedOffset.value !== null
  ? mainApi.recordingKeyframeImageUrl(wb.caseId, props.clip.id, selectedOffset.value)
  : "");
const pictureRatio = computed(() => {
  const frame = currentFrame.value;
  return frame?.width && frame?.height
    ? String(frame.width) + " / " + String(frame.height) : "16 / 9";
});

function choose(offset: number) {
  vlmEpoch += 1;
  vlmBusy.value = false;
  vlmResult.value = null; vlmError.value = "";
  frameFailed.value = false;
  selectedOffset.value = offset;
}

async function inspectAttributes() {
  const caseId = wb.caseId;
  const clipId = props.clip.id;
  const offset = selectedOffset.value;
  if (!caseId || offset === null || vlmBusy.value || frameFailed.value ||
      currentFrame.value?.status !== "frame_available") return;
  const ticket = ++vlmEpoch;
  vlmBusy.value = true; vlmResult.value = null; vlmError.value = "";
  try {
    const data = await mainApi.previewM4dFrameAttributes(caseId, clipId, offset);
    if (ticket !== vlmEpoch || wb.caseId !== caseId ||
        props.clip.id !== clipId || selectedOffset.value !== offset) return;
    if (data.case_id !== caseId || data.clip_id !== clipId ||
        data.requested_offset_seconds !== offset || data.identity_established !== false) {
      throw new Error("模型分析来源不一致，已拒绝显示");
    }
    vlmResult.value = data;
  } catch (exc) {
    if (ticket === vlmEpoch) vlmError.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    if (ticket === vlmEpoch) vlmBusy.value = false;
  }
}

async function inspect() {
  const caseId = wb.caseId;
  const clipId = props.clip.id;
  if (!caseId || loading.value) {
    error.value = "请先选择案件";
    return;
  }
  const rawParts = offsetsInput.value.split(",");
  const parts = rawParts.map(s => Number(s.trim()));
  if (!parts.length || parts.length > 6 || rawParts.some(s => !s.trim()) ||
      parts.some(n => !Number.isFinite(n) || n < 0 || n > 21600) ||
      new Set(parts).size !== parts.length) {
    error.value = "偏移量必须为 1–6 个不重复的非负秒数，用英文逗号分隔";
    return;
  }
  const ticket = ++requestEpoch;
  vlmEpoch += 1;
  vlmBusy.value = false; vlmResult.value = null; vlmError.value = "";
  error.value = ""; loading.value = true;
  result.value = null; selectedOffset.value = null;
  frameFailed.value = false;
  try {
    const response = await mainApi.previewRecordingKeyframes(caseId,clipId,{
      offsets_seconds: parts, include_detection: detect.value,
    });
    if (ticket !== requestEpoch || wb.caseId !== caseId || props.clip.id !== clipId) return;
    if (response.case_id !== caseId || response.clip_id !== clipId ||
        response.status !== "sampled_keyframes_unverified") {
      throw new Error("录像抽帧结果来源不一致，已拒绝显示");
    }
    result.value = response;
    const available = response.frames.find(f => f.status === "frame_available");
    if (available) choose(available.requested_offset_seconds);
    else error.value = "指定偏移未取得有效画面；不代表录像中不存在目标";
  } catch (exc) {
    if (ticket === requestEpoch) error.value = exc instanceof Error ? exc.message : String(exc);
  } finally {
    if (ticket === requestEpoch) loading.value = false;
  }
}
</script>

<template>
  <section class="m4c-panel">
    <strong>M4c · 视频关键帧检查（只读）</strong>
    <p>从已扫描录像请求少量画面。指定秒数为请求偏移，不保证等于实际呈现帧 PTS；设备时间、身份及地图位置均需核实。</p>
    <label class="m4c-input">
      请求时间偏移（秒）
      <input v-model="offsetsInput" placeholder="0.5,2,8" spellcheck="false" />
    </label>
    <label class="m4c-permission">
      <input v-model="detect" type="checkbox" /> 附加 D-FINE 检测候选（须已配置真实 ONNX）
    </label>
    <button type="button" class="btn btn--outline" :disabled="loading" @click="inspect">
      {{ loading ? "正在读取录像…" : "读取关键帧" }}
    </button>
    <p v-if="error" class="m4c-error">{{ error }}</p>
    <div v-if="result" class="m4c-result">
      <p>起始时间候选：{{ result.start_time_candidate || "未知" }}
        （来源 {{ result.start_time_source || "未知" }}）；未确认精确帧时间。</p>
      <p>设备 UUID 候选：{{ result.camera_uuid_candidate || "未知" }}；
        本次观测未核实设备；Tracklet 尚未建立。</p>
      <div class="m4c-tabs">
        <button v-for="frame in result.frames" :key="frame.requested_offset_seconds"
          type="button" class="m4c-chip"
          :disabled="frame.status !== 'frame_available'"
          :class="{ picked: selectedOffset === frame.requested_offset_seconds }"
          @click="choose(frame.requested_offset_seconds)">
          {{ frame.requested_offset_seconds }}s
          {{ frame.status === "frame_available" ? "可用" : "无帧" }}
        </button>
      </div>
      <div v-if="currentFrame && currentFrame.status === 'frame_available' && !frameFailed"
        class="m4c-image" :style="{ aspectRatio: pictureRatio }">
        <img :key="imageUrl" :src="imageUrl" alt="录像指定偏移的关键帧（时间尚未经 PTS 校准）"
          @error="frameFailed = true" />
        <svg v-if="currentFrame.detections" viewBox="0 0 1 1"
          preserveAspectRatio="none" aria-label="未核实的检测候选框">
          <rect v-for="d in currentFrame.detections.candidates" :key="d.proposal_index"
            :x="d.bbox_normalized[0]" :y="d.bbox_normalized[1]"
            :width="d.bbox_normalized[2] - d.bbox_normalized[0]"
            :height="d.bbox_normalized[3] - d.bbox_normalized[1]"
            fill="none" stroke="#4dd8fd" stroke-width=".003" />
        </svg>
      </div>
      <p v-if="frameFailed" class="m4c-error">关键帧图片读取失败，请确认录像未变化并重新扫描。</p>
      <div v-if="currentFrame && currentFrame.status === 'frame_available'" class="m4c-summary">
        <button type="button" class="btn btn--outline" :disabled="vlmBusy || loading || frameFailed"
          @click="inspectAttributes">{{ vlmBusy ? "模型分析中…" : "M4d · 分析当前帧属性" }}</button>
        <p v-if="vlmError" class="m4c-error">{{ vlmError }}</p>
        <div v-if="vlmResult">
          <p>模型状态：{{ vlmResult.vlm_status }}；属性状态：{{ vlmResult.attributes_status }}。
            仅供人工复核，不确认身份或跨镜头关联。</p>
          <p v-for="attr in vlmResult.attributes" :key="attr.name">
            {{ attr.name }}：{{ attr.state }}{{ attr.value ? " · " + attr.value : "" }}
          </p>
        </div>
      </div>
      <div v-if="currentFrame" class="m4c-summary">
        <p>画质提示：{{ currentFrame.quality?.warnings.join("、") || "无规则告警（仍未核实）" }}</p>
        <p v-if="currentFrame.detections">
          {{ currentFrame.detections.model_family }}：
          {{ currentFrame.detections.candidates.length }} 个未核实建议框；非身份关联。
        </p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.m4c-panel{display:grid;gap:9px;border:1px solid var(--line-2);border-radius:7px;
 padding:10px;background:var(--bg-1);font-size:11px;min-width:0}
.m4c-panel strong{color:var(--text-0);font-size:12px}
.m4c-panel p{color:var(--text-3);margin:0;line-height:1.5}
.m4c-input{display:grid;gap:4px;color:var(--text-2)}
.m4c-input input{background:var(--bg-2);border:1px solid var(--line-2);
 border-radius:5px;color:var(--text-1);padding:7px}
.m4c-permission{display:flex;align-items:center;gap:6px;color:var(--text-2)}
.m4c-tabs{display:flex;flex-wrap:wrap;gap:5px}
.m4c-chip{border:1px solid var(--line-2);background:var(--bg-2);
 color:var(--text-2);padding:4px 7px;border-radius:5px;cursor:pointer}
.m4c-chip.picked{border-color:var(--accent)}
.m4c-chip:disabled{opacity:.5;cursor:not-allowed}
.m4c-image{position:relative;max-height:245px;background:#0b131f;overflow:hidden}
.m4c-image img{position:absolute;inset:0;width:100%;height:100%;object-fit:fill}
.m4c-image svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.m4c-result{display:grid;gap:8px}.m4c-summary{display:grid;gap:3px}
.m4c-error{color:#e9ae67 !important}
</style>
