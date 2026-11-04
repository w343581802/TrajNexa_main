<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { mainApi, type M4DetectCandidate, type M5ImageModel, type M5VisionModel,
  type M5TestDetectionResult, type M5TestRoiResult, type M6PersonReidResult } from "../services/mainApi";
import { modelChoices, setSmallModelIds, setVisionModelId } from "../store/modelChoices";

type ObjectClass = "person" | "vehicle" | "other";
const file = ref<File | null>(null);
const preview = ref("");
const smallModels = ref<M5ImageModel[]>([]);
const visionModels = ref<M5VisionModel[]>([]);
const smallSelected = ref<string[]>([...modelChoices.smallModelIds]);
const visionSelected = ref<string[]>(modelChoices.visionModelId ? [modelChoices.visionModelId] : []);
const loadingCatalog = ref(false);
const busy = ref(false);
const elapsed = ref(0);
let elapsedTimer: ReturnType<typeof setInterval> | null = null;
function startElapsed() {
  elapsed.value = 0;
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => { elapsed.value += 1; }, 1000);
}
function stopElapsed() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
}
const roiBusy = ref(false);
const error = ref("");
const catalogError = ref("");
const threshold = ref(.35);
const detection = ref<M5TestDetectionResult | null>(null);
const roi = ref<M5TestRoiResult | null>(null);
const selectedBox = ref<[number, number, number, number] | null>(null);
const objectClass = ref<ObjectClass>("vehicle");
const prompt = ref("");
// M6 P1-A: image A is a manually selected PERSON reference; existing test image = B.
const referenceFile = ref<File | null>(null);
const referencePreview = ref("");
const referenceBox = ref<[number, number, number, number] | null>(null);
const referenceBoxText = computed(() =>
  referenceBox.value ? referenceBox.value.map((x) => x.toFixed(3)).join(", ") : "");
const reidDetectorId = ref("");
const reidResult = ref<M6PersonReidResult | null>(null);
const reidBusy = ref(false);
const reidError = ref("");
// P1-A-5 候选池与拒识的实验旋钮；默认值来自单摄像头小样本，不作为业务规则
const reidHighConf = ref(.4);
const reidMinHeight = ref(70);
const reidDedupeIou = ref(.5);
const reidSimilarityThreshold = ref(.72);
const REID_STATE_TEXT: Record<string, string> = {
  candidate_for_review: "候选值得核查",
  insufficient_evidence: "证据不足",
  no_person_candidate: "未检测到可用人员",
};
const REID_REASON_TEXT: Record<string, string> = {
  similarity_above_threshold: "最高相似度达到实验阈值，仍需人工核对",
  similarity_below_threshold: "没有足够证据表明当前画面存在该目标（最高相似度低于实验阈值）",
  no_high_quality_candidate: "只有低置信度或过小的候选，证据不足以进入优先比较",
  no_valid_embedding: "候选裁剪无法提取向量",
  detector_found_no_person: "检测层没有给出任何人员候选",
};
const reidDecisionText = computed(() =>
  reidResult.value ? REID_STATE_TEXT[reidResult.value.decision.state] ?? reidResult.value.decision.state : "");
const reidDecisionReason = computed(() =>
  reidResult.value ? REID_REASON_TEXT[reidResult.value.decision.reason] ?? reidResult.value.decision.reason : "");
const reidBestText = computed(() => {
  const best = reidResult.value?.decision.best_similarity;
  return best === null || best === undefined ? "—" : best.toFixed(4);
});
const reidMarginText = computed(() => {
  const margin = reidResult.value?.decision.margin;
  return margin === null || margin === undefined ? "—" : margin.toFixed(4);
});
let referenceStart: [number, number] | null = null;
let reidRequest = 0;
let selectionStart: [number, number] | null = null;
let generation = 0;
// 在途请求计数：只要还有请求在跑，按钮就保持「检测中」；全部结束必然复位
let detectInflight = 0;
let roiInflight = 0;
let catalogEpoch = 0;
const modelName = computed(() => new Map([
  ...smallModels.value.map(m => [m.id, m.name] as const),
  ...visionModels.value.map(m => [m.id, m.name] as const),
]));
const hasModels = computed(() => smallSelected.value.length + visionSelected.value.length > 0);
/** 只渲染坐标合法的候选：一个坏候选不该把整块画布渲染挂掉 */
const boxes = computed(() => (detection.value?.candidates || [])
  .filter(c => Array.isArray(c.bbox_normalized) && c.bbox_normalized.length === 4 &&
    c.bbox_normalized.every(Number.isFinite)));
/** 已框选坐标的文字：在脚本里算，模板不需要对元组做类型推断 */
const boxText = computed(() => selectedBox.value
  ? selectedBox.value.map((value) => value.toFixed(3)).join(", ")
  : "");

async function loadCatalog() {
  const ticket = ++catalogEpoch;
  loadingCatalog.value = true;
  catalogError.value = "";
  const [small, vision] = await Promise.allSettled([
    mainApi.listM5ImageModels(), mainApi.listM5VisionModels(),
  ]);
  if (ticket !== catalogEpoch) return;
  smallModels.value = small.status === "fulfilled" && small.value.enabled ? small.value.models : [];
  visionModels.value = vision.status === "fulfilled" && vision.value.enabled ? vision.value.models : [];
  const smallAllowed = new Set(smallModels.value.map(m => m.id));
  const visionAllowed = new Set(visionModels.value.filter(m => m.enabled).map(m => m.id));
  smallSelected.value = smallSelected.value.filter(id => smallAllowed.has(id));
  visionSelected.value = visionSelected.value.filter(id => visionAllowed.has(id));
  if (!smallSelected.value.length && smallModels.value.length) {
    smallSelected.value = [smallModels.value[0].id];
  }
  if (!visionSelected.value.length && visionModels.value.length) {
    const preferred = visionModels.value.find(m => m.enabled && m.default_for_roi) ||
      visionModels.value.find(m => m.enabled);
    visionSelected.value = preferred ? [preferred.id] : [];
  }
  if (!smallModels.value.some(m => m.id === reidDetectorId.value))
    reidDetectorId.value = smallModels.value[0]?.id || "";
  if (small.status === "rejected" || vision.status === "rejected") {
    catalogError.value = "部分模型服务不可用。已列出可连接的模型；也可以只测另一类模型。";
  }
  loadingCatalog.value = false;
}
onMounted(() => { void loadCatalog(); });
onUnmounted(() => {
  generation++;
  stopElapsed();
  if (preview.value) URL.revokeObjectURL(preview.value);
  if (referencePreview.value) URL.revokeObjectURL(referencePreview.value);
});
function loadFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const next = input.files?.[0] ?? null;
  if (preview.value) URL.revokeObjectURL(preview.value);
  generation++;
  file.value = null;
  preview.value = "";
  detection.value = null; roi.value = null; selectedBox.value = null; error.value = "";
  reidResult.value = null; reidError.value = "";
  if (!next) return;
  if (!["image/jpeg", "image/png", "image/webp"].includes(next.type) || next.size > 12 * 1024 * 1024) {
    error.value = "请选择不超过 12MB 的 PNG/JPEG/WebP 图片。";
    input.value = "";
    return;
  }
  file.value = next;
  preview.value = URL.createObjectURL(next);
}
function toggleSmall(id: string) {
  if (smallSelected.value.includes(id))
    smallSelected.value = smallSelected.value.filter(x => x !== id);
  else if (smallSelected.value.length < 5) smallSelected.value = [...smallSelected.value, id];
  else error.value = "最多选择 5 个检测模型。";
  setSmallModelIds(smallSelected.value);
}
function visionAllowedForTest(id: string) {
  return visionModels.value.some(m => m.id === id && m.enabled);
}
function toggleVision(id: string) {
  if (visionSelected.value.includes(id))
    visionSelected.value = visionSelected.value.filter(x => x !== id);
  else if (visionSelected.value.length < 3) visionSelected.value = [...visionSelected.value, id];
  else error.value = "最多同时比较 3 个视觉模型。";
  if (visionSelected.value.length) setVisionModelId(visionSelected.value[0]);
}
async function detect() {
  const chosen = file.value;
  if (!chosen || !hasModels.value || busy.value) return;
  const ticket = ++generation;
  detectInflight += 1;
  busy.value = true; error.value = ""; startElapsed();
  detection.value = null; roi.value = null; selectedBox.value = null;
  try {
    const data = await mainApi.testM5Detect(chosen,
      [...smallSelected.value], [...visionSelected.value], threshold.value);
    if (ticket === generation && file.value === chosen) {
      detection.value = data;
      if (data.status === "all_models_failed") error.value = "全部模型执行失败。请检查各模型错误码。";
    }
  } catch (err) {
    if (ticket === generation) error.value = err instanceof Error ? err.message : String(err);
  } finally {
    detectInflight -= 1;
    if (detectInflight <= 0) { detectInflight = 0; busy.value = false; stopElapsed(); }
  }
}
function choose(candidate: M4DetectCandidate) {
  const b = candidate.bbox_normalized;
  if (b.length !== 4 || !b.every(Number.isFinite) ||
      !(0 <= b[0] && b[0] < b[2] && b[2] <= 1 &&
        0 <= b[1] && b[1] < b[3] && b[3] <= 1)) return;
  selectedBox.value = [...b] as [number, number, number, number];
  objectClass.value = candidate.object_class;
  roi.value = null;
}
function point(e: PointerEvent): [number, number] {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  return [
    Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
    Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height)),
  ];
}
function drawStart(e: PointerEvent) {
  if (e.button !== 0) return;
  selectionStart = point(e);
  selectedBox.value = [selectionStart[0], selectionStart[1],
    selectionStart[0], selectionStart[1]];
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
}
function drawMove(e: PointerEvent) {
  if (!selectionStart) return;
  const p = point(e);
  selectedBox.value = [Math.min(p[0], selectionStart[0]), Math.min(p[1], selectionStart[1]),
    Math.max(p[0], selectionStart[0]), Math.max(p[1], selectionStart[1])];
}
function drawEnd(e: PointerEvent) {
  if (!selectionStart) return;
  drawMove(e);
  selectionStart = null;
  const b = selectedBox.value;
  if (!b || b[2] - b[0] < .01 || b[3] - b[1] < .01) selectedBox.value = null;
  roi.value = null;
}
function boxStyle(b: readonly number[]) {
  return { left: b[0] * 100 + "%", top: b[1] * 100 + "%",
    width: (b[2] - b[0]) * 100 + "%", height: (b[3] - b[1]) * 100 + "%" };
}
async function analyzeRoi() {
  const chosen = file.value, b = selectedBox.value;
  if (!chosen || !b || !visionSelected.value.length || roiBusy.value) return;
  const ticket = generation;
  roiInflight += 1;
  roiBusy.value = true; roi.value = null; error.value = "";
  try {
    const data = await mainApi.testM5Roi(chosen, b, objectClass.value,
      [...visionSelected.value], prompt.value);
    if (ticket === generation && file.value === chosen) roi.value = data;
  } catch (err) {
    if (ticket === generation) error.value = err instanceof Error ? err.message : String(err);
  } finally {
    roiInflight -= 1;
    if (roiInflight <= 0) { roiInflight = 0; roiBusy.value = false; }
  }
}
/** Reference image A is never added to case media or saved on the backend. */
function loadReference(event: Event) {
  const input = event.target as HTMLInputElement;
  const picked = input.files?.[0] || null;
  ++reidRequest;
  if (referencePreview.value) URL.revokeObjectURL(referencePreview.value);
  referenceFile.value = null; referencePreview.value = "";
  referenceBox.value = null; reidResult.value = null; reidError.value = "";
  if (!picked) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(picked.type) ||
      picked.size > 12 * 1024 * 1024) {
    reidError.value = "参考图片必须是 12MB 以下的 PNG、JPG 或 WebP。";
    input.value = "";
    return;
  }
  referenceFile.value = picked;
  referencePreview.value = URL.createObjectURL(picked);
}
function referencePoint(e: PointerEvent): [number, number] {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  return [Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)),
    Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height))];
}
function referencePointerDown(e: PointerEvent) {
  if (e.button !== 0) return;
  referenceStart = referencePoint(e);
  referenceBox.value = [referenceStart[0], referenceStart[1],
    referenceStart[0], referenceStart[1]];
  (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
}
function referencePointerMove(e: PointerEvent) {
  if (!referenceStart) return;
  const p = referencePoint(e);
  referenceBox.value = [Math.min(referenceStart[0], p[0]),
    Math.min(referenceStart[1], p[1]),
    Math.max(referenceStart[0], p[0]), Math.max(referenceStart[1], p[1])];
}
function referencePointerUp(e: PointerEvent) {
  if (!referenceStart) return;
  referencePointerMove(e);
  referenceStart = null;
  if (referenceBox.value &&
      (referenceBox.value[2] - referenceBox.value[0] < .03 ||
       referenceBox.value[3] - referenceBox.value[1] < .03))
    referenceBox.value = null;
  reidResult.value = null;
}
async function runReid() {
  const a = referenceFile.value, b = file.value, box = referenceBox.value;
  if (!a || !b || !box || !reidDetectorId.value || reidBusy.value) return;
  const ticket = ++reidRequest, fileGeneration = generation;
  reidBusy.value = true; reidError.value = ""; reidResult.value = null;
  try {
    const response = await mainApi.testM6PersonReid(
      a, b, box, reidDetectorId.value, threshold.value, {
        highConf: reidHighConf.value, minHeightPx: reidMinHeight.value,
        dedupeIou: reidDedupeIou.value, similarityThreshold: reidSimilarityThreshold.value,
      });
    if (ticket === reidRequest && fileGeneration === generation &&
        referenceFile.value === a && file.value === b) reidResult.value = response;
  } catch (err) {
    if (ticket === reidRequest && fileGeneration === generation)
      reidError.value = err instanceof Error ? err.message : String(err);
  } finally { reidBusy.value = false; }
}
function pickReidResult(box: [number, number, number, number]) {
  selectedBox.value = [...box] as [number, number, number, number];
  objectClass.value = "person";
  roi.value = null;
}

</script>

<template>
  <main class="lab">
    <header class="lab-head">
      <div>
        <p class="eyebrow">M5 / EXPERIMENTAL WORKSPACE</p>
        <h1>模型测试</h1>
        <p>统一测试 Rebucca 小模型、本地与远程视觉大模型；实验不写入案件或目标轨迹。</p>
      </div>
      <button class="minor-btn" type="button" :disabled="loadingCatalog" @click="loadCatalog">
        <AppIcon name="refresh" :size="14" /> {{ loadingCatalog ? "读取中…" : "刷新模型目录" }}
      </button>
    </header>
    <p v-if="catalogError" class="notice">{{ catalogError }}</p>
    <section class="config-panel">
      <div class="config-column">
        <h2>01 · 测试图片</h2>
        <label class="upload">选择图片
          <input type="file" accept="image/png,image/jpeg,image/webp" @change="loadFile" />
        </label>
        <p class="hint">{{ file ? file.name + " · " + (file.size / 1024).toFixed(0) + " KB" :
          "本地图片仅随本次请求进入模型测试服务，不入案件。" }}</p>
        <label class="hint">检测置信度 {{ threshold.toFixed(2) }}
          <input v-model.number="threshold" type="range" min=".2" max=".8" step=".05" />
        </label>
      </div>
      <div class="config-column">
        <h2>02 · 小模型（最多 5 个）</h2>
        <label v-for="m in smallModels" :key="m.id" class="model-pick">
          <input type="checkbox" :checked="smallSelected.includes(m.id)" @change="toggleSmall(m.id)" />
          <span><b>{{ m.name }}</b><small>{{ m.algorithm_type || "detect" }} · {{ m.engine }} · {{ m.device || "cpu" }}</small></span>
        </label>
        <p v-if="!smallModels.length" class="hint">Rebucca 模型服务尚未连接或没有已注册模型。</p>
      </div>
      <div class="config-column">
        <h2>03 · 视觉大模型（最多 3 个）</h2>
        <label v-for="m in visionModels" :key="m.id" class="model-pick">
          <input type="checkbox" :checked="visionSelected.includes(m.id)"
            :disabled="!m.enabled" @change="toggleVision(m.id)" />
          <span><b>{{ m.name }}</b><small>{{ m.location === "remote" ? "远程 API" : "本地" }}
            · {{ m.provider }} · {{ m.model_id }}</small></span>
        </label>
        <p v-if="!visionModels.length" class="hint">请先在大模型管理配置本地或远程视觉模型。</p>
      </div>
      <div class="config-submit">
        <button type="button" class="primary-btn" :disabled="busy || !file || !hasModels" @click="detect">
          {{ busy ? `正在逐模型检测… ${elapsed}s` : "开始同图对比" }}</button>
        <p class="hint">小模型按 Rebucca 引擎推理，大模型整图识别仅作为对照；未自动合并相似候选。</p>
      </div>
    </section>
    <p v-if="error" class="notice error">{{ error }}</p>
    <section class="result-layout">
      <div class="canvas-card">
        <div class="card-head"><h2>检测画布</h2><span>{{ detection ? detection.candidate_count + " 个候选" : "等待图片分析" }}</span></div>
        <div class="canvas-holder">
          <div v-if="preview" class="image-canvas">
            <img :src="preview" alt="本地实验图片" />
            <div class="image-overlay" @pointerdown.prevent="drawStart" @pointermove.prevent="drawMove"
              @pointerup.prevent="drawEnd" @pointercancel="drawEnd">
              <button v-for="(c, i) in boxes"
                :key="c.model_id + '-' + i" type="button" class="candidate"
                :title="(modelName.get(c.model_id || '') || c.model_id || '') + ' · ' + c.detector_label"
                :style="boxStyle(c.bbox_normalized)" @pointerdown.stop.prevent="choose(c)" />
              <div v-if="selectedBox" class="selected" :style="boxStyle(selectedBox)" />
            </div>
          </div>
          <div v-else class="empty">选择测试图片后，模型候选框将在这里叠加显示。也可以手动拖拽框选 ROI。</div>
        </div>
        <p class="hint">点击彩色候选框或在图上拖拽选取目标；结果均未核实。</p>
      </div>
      <div class="details">
        <div class="card-head"><h2>模型对比</h2><span v-if="detection">{{ detection.elapsed_ms }}ms · {{ detection.status }}</span></div>
        <p v-if="!detection" class="empty">运行模型后显示每个模型的状态、候选数量和耗时。</p>
        <template v-else>
          <p class="hint">图像 SHA-256 {{ detection.source_sha256.slice(0, 20) }}… · 不写案件库</p>
          <div v-for="run in detection.model_runs" :key="run.kind + run.model_id" class="run">
            <div class="run-head">
              <b>{{ modelName.get(run.model_id) || run.model_id }}</b>
              <span :class="{ fail: run.status === 'inference_error' }">{{ run.status === "inference_error" ? "失败" : "已返回" }}</span>
            </div>
            <small>{{ run.kind === "vision" ? "视觉大模型" : "小模型" }} · {{ run.candidate_count }} 个候选
              · {{ run.elapsed_ms }}ms
              <template v-if="run.error_code"> · {{ run.error_code }}</template></small>
          </div>
          <div class="candidates">
            <button v-for="(c, i) in boxes" :key="i" class="candidate-tag"
              type="button" @click="choose(c)">
              {{ modelName.get(c.model_id || '') || c.model_id || "检测器" }} · {{ c.detector_label }}
              <template v-if="c.confidence !== null"> · {{ (c.confidence * 100).toFixed(0) }}%</template>
            </button>
          </div>
        </template>
      </div>
    </section>

    <section class="roi-card">
      <div class="card-head"><h2>04 · ROI 大模型属性对比</h2>
        <span>选中目标 → 原图裁剪 → 本地/远程视觉模型</span></div>
      <div class="roi-options">
        <label>目标类型
          <select v-model="objectClass"><option value="person">人员</option>
            <option value="vehicle">车辆</option><option value="other">其他</option></select>
        </label>
        <span class="hint">{{ selectedBox ? "已框选：" + boxText :
          "请先在上方图中选择一个候选或手动框选。" }}</span>
      </div>
      <label class="prompt-label">本次提示词（留空即按人员/车辆类别使用内置模板）
        <textarea v-model="prompt" maxlength="2400" rows="4"
          placeholder="例如：只描述车身颜色、轮数、货厢和可见遮挡；看不清就写未知。JSON 输出 features 数组。" />
      </label>
      <button class="primary-btn" type="button" :disabled="roiBusy || !file || !selectedBox ||
        !visionSelected.length" @click="analyzeRoi">
        {{ roiBusy ? "逐模型分析中…" : "对比选中区域" }}
      </button>
      <div v-if="roi" class="roi-results">
        <article v-for="result in roi.results" :key="result.model_id" class="roi-result">
          <b>{{ result.model_name }}</b><span>{{ result.elapsed_ms }}ms · {{ result.status }}</span>
          <p>{{ result.features_unverified.length ? result.features_unverified.join("、") :
            "未返回可用外观信息：" + (result.error_code || "未知") }}</p>
          <small>Prompt SHA-256 {{ result.prompt_sha256.slice(0, 16) }}…</small>
        </article>
      </div>
      <p class="hint">所有大模型输出仅供比较，不能仅凭颜色、外观或模型一致意见认定跨镜头身份。</p>
    </section>
    <section class="roi-card reid-lab">
      <div class="card-head"><h2>05 · 指定目标相似检索（M6 / Person ReID 实验）</h2>
        <span>参考 A 人员框 → 图片 B 检测人 → OSNet 相似度排序</span></div>
      <p class="hint">
        此阶段只支持人员 OSNet；骑车人需要在 A 中框选<b>骑手本人</b>，不是整辆车。
        YOLO / D-FINE 只负责找人员，外观相似度不代表同一身份。
      </p>
      <div class="reid-grid">
        <div class="reid-left">
          <h2>A · 参考人员图片</h2>
          <label class="upload">选择参考图片 A
            <input type="file" accept="image/png,image/jpeg,image/webp" @change="loadReference" />
          </label>
          <div class="canvas-holder reid-reference-canvas">
            <div v-if="referencePreview" class="image-canvas">
              <img :src="referencePreview" alt="参考人员图片 A" />
              <div class="image-overlay" @pointerdown.prevent="referencePointerDown"
                @pointermove.prevent="referencePointerMove"
                @pointerup.prevent="referencePointerUp" @pointercancel="referencePointerUp">
                <div v-if="referenceBox" class="selected" :style="boxStyle(referenceBox)" />
              </div>
            </div>
            <div v-else class="empty">上传 A 后在图片上拖拽，框选参考人员。</div>
          </div>
          <p class="hint">{{ referenceBox ? "已选人员 ROI：" +
            referenceBox.map((x: number) => x.toFixed(3)).join(", ") : "请框选 A 中的人员；不要包含整幅背景。" }}</p>
        </div>
        <div class="reid-right">
          <h2>B · 搜索图片（上方已选图片）</h2>
          <p class="hint">{{ file ? file.name : "请先在上方「01 · 测试图片」上传包含多人的图片 B。" }}</p>
          <label class="reid-selector">检测人员的小模型
            <select v-model="reidDetectorId">
              <option value="" disabled>请选择检测器</option>
              <option v-for="model in smallModels" :key="model.id" :value="model.id">
                {{ model.name }} · {{ model.engine }}
              </option>
            </select>
          </label>
          <details class="reid-params">
            <summary>实验参数 · 候选池与拒识（P1-A-5）</summary>
            <div class="reid-param-grid">
              <label>高质量门限
                <input v-model.number="reidHighConf" type="number" min="0.2" max="1" step="0.05" />
              </label>
              <label>最小人体高度 px
                <input v-model.number="reidMinHeight" type="number" min="24" max="1000" step="5" />
              </label>
              <label>去重 IoU
                <input v-model.number="reidDedupeIou" type="number" min="0.1" max="0.95" step="0.05" />
              </label>
              <label>相似度阈值
                <input v-model.number="reidSimilarityThreshold" type="number" min="-1" max="1" step="0.01" />
              </label>
            </div>
            <p class="hint">默认值来自单摄像头小样本（design/43 §11），是实验旋钮不是业务规则；改完重新检索。</p>
          </details>
          <button class="primary-btn" type="button"
            :disabled="reidBusy || !file || !referenceFile || !referenceBox || !reidDetectorId"
            @click="runReid">{{ reidBusy ? "计算外观相似度中…" : "开始指定人员检索" }}</button>
          <p v-if="reidError" class="notice error">{{ reidError }}</p>
          <template v-if="reidResult">
            <div class="reid-decision" :class="reidResult.decision.state">
              <b>{{ reidDecisionText }}</b>
              <span>{{ reidDecisionReason }}</span>
            </div>
            <p class="hint">人员候选：原始 {{ reidResult.candidate_pool.raw_person_count }} →
              去重后 {{ reidResult.candidate_pool.after_dedupe }}
              （移除重复 {{ reidResult.candidate_pool.duplicates_removed }}）·
              高质量 {{ reidResult.candidate_pool.high_count }} ·
              备用 {{ reidResult.candidate_pool.low_count }}</p>
            <p class="hint">最高相似度 {{ reidBestText }} · 实验阈值
              {{ reidResult.decision.threshold.toFixed(2) }} · 与次名差距 {{ reidMarginText }} ·
              {{ reidResult.elapsed_ms }}ms</p>
            <div v-for="item in reidResult.ranking" :key="item.proposal_index" class="reid-rank">
              <div class="run-head">
                <b>#{{ item.rank }} · {{ item.detector_label }} · {{ item.pixel_height }}px ·
                  {{ item.detector_confidence?.toFixed(2) }}</b>
                <b>cosine {{ item.cosine_similarity.toFixed(4) }}</b>
              </div>
              <button type="button" class="candidate-tag" @click="pickReidResult(item.bbox_normalized)">
                选中 B 中此候选框（待核实）
              </button>
            </div>
            <p v-if="reidResult.ranking.length && reidResult.decision.narrow_margin" class="hint">
              前两名差距很小：去重后仍可能是同一目标的两次检测，请结合相邻帧人工核对。
            </p>
            <details v-if="reidResult.backup.length" class="reid-backup">
              <summary>备用候选 {{ reidResult.backup.length }} 个（低置信度或过小，未进入排序）</summary>
              <div v-for="item in reidResult.backup" :key="'b' + item.proposal_index" class="reid-rank">
                <div class="run-head">
                  <b>备用 #{{ item.backup_rank }} · {{ item.pixel_height }}px ·
                    {{ item.detector_confidence?.toFixed(2) }}</b>
                  <b>cosine {{ item.cosine_similarity.toFixed(4) }}</b>
                </div>
                <button type="button" class="candidate-tag" @click="pickReidResult(item.bbox_normalized)">
                  选中 B 中此候选框（待核实）
                </button>
              </div>
            </details>
            <p v-if="!reidResult.ranking.length && !reidResult.backup.length" class="hint">
              检测层没有给出人员候选；这不等于目标不在图片 B 中，可降低检测置信度后重试。
            </p>
            <p class="hint">OSNet 权重 SHA-256：{{ reidResult.reid_model_sha256.slice(0, 20) }}…</p>
            <p class="hint">参考/搜索图片校验码：
              {{ reidResult.reference_source_sha256.slice(0, 10) }}… /
              {{ reidResult.search_source_sha256.slice(0, 10) }}…
            </p>
          </template>
        </div>
      </div>
      <p class="hint">不保存原图或 embedding；未安装 OSNet 权重时明确提示不可用。
        本阶段不做车辆 ReID、跨镜头身份确认或自动更新目标参考库。</p>
    </section>
  </main>
</template>

<style scoped>
.lab { flex: 1; min-width: 0; min-height: 0; padding: 26px 30px 60px; overflow: auto;
  color: var(--text-1); background: var(--bg-0); }
.lab-head { display: flex; justify-content: space-between; gap: 15px; align-items: flex-start;
  margin-bottom: 18px; }
.eyebrow { font-size: 10px; color: #80bcd7; letter-spacing: .16em; margin: 0 0 7px; }
h1 { font-size: 24px; color: var(--text-0); margin: 0 0 7px; }
.lab-head p:not(.eyebrow) { color: var(--text-3); margin: 0; font-size: 12px; }
h2 { font-size: 13px; color: var(--text-0); margin: 0 0 12px; }
.config-panel { display: grid; grid-template-columns: repeat(3,minmax(0,1fr));
  gap: 14px; margin-bottom: 16px; }
.config-column, .canvas-card, .details, .roi-card { border: 1px solid var(--line-2);
  background: var(--bg-1); border-radius: 9px; padding: 15px; }
.config-column { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.model-pick { display: flex; align-items: center; gap: 10px; padding: 7px 6px;
  background: var(--bg-2); border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }
.model-pick input { accent-color: #57bbdf; }
.model-pick span { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.model-pick b { font-size: 11px; font-weight: 620; }
.model-pick small, .hint { font-size: 10.5px; color: var(--text-3); line-height: 1.6; }
.model-pick small { overflow-wrap: anywhere; }
.config-submit { grid-column: 1 / -1; display: flex; align-items: center; gap: 14px; }
.upload, .primary-btn, .minor-btn { display: inline-flex; gap: 7px; align-items: center;
  border-radius: 6px; font-size: 11px; padding: 9px 13px; cursor: pointer;
  align-self: flex-start; }
.upload { border: 1px dashed #36769a; color: #a3dcee; }
.upload input { display: none; }
.primary-btn { background: #176e96; color: white; border: 1px solid #328ab1; }
.minor-btn { background: var(--bg-2); border: 1px solid var(--line-2); color: var(--text-1); }
button:disabled { opacity: .45; cursor: not-allowed; }
.result-layout { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(290px, 1fr);
  gap: 15px; margin-bottom: 16px; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px;
  border-bottom: 1px solid var(--line); padding-bottom: 9px; margin-bottom: 12px; }
.card-head h2 { margin: 0; }.card-head span { font-size: 10px; color: var(--text-3); }
.canvas-holder { display: flex; align-items: center; justify-content: center; min-height: 235px;
  background: #050a12; border-radius: 6px; overflow: hidden; }
.image-canvas { display: inline-block; position: relative; max-width: 100%; line-height: 0; }
.image-canvas img { display: block; max-width: 100%; max-height: 510px; width: auto; }
.image-overlay { position: absolute; inset: 0; cursor: crosshair; touch-action: none; }
.candidate { position: absolute; border: 2px solid #53d4ad; background: rgba(46,188,137,.1);
  cursor: pointer; padding: 0; }
.candidate:hover { border-color: #ffe082; background: rgba(255,224,130,.18); }
.selected { position: absolute; border: 2px dashed #ffcd52; pointer-events: none;
  background: rgba(255,205,82,.1); }
.empty { color: var(--text-4); font-size: 11px; padding: 25px; line-height: 1.7; }
.run { border-bottom: 1px solid var(--line); padding: 9px 0; }
.run-head { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; }
.run-head span { font-size: 10px; color: #85d6b8; }
.run-head span.fail { color: #ffab9f; }.run small { color: var(--text-3); }
.candidates { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 15px; }
.candidate-tag { padding: 5px 9px; background: var(--bg-2); border: 1px solid var(--line-2);
  color: var(--text-2); border-radius: 5px; cursor: pointer; font-size: 10px; }
.candidate-tag:hover { border-color: #66b7dc; }
.notice { border-radius: 5px; border: 1px solid #406882; color: #a9cadd;
  background: #132637; padding: 10px; font-size: 11px; }
.notice.error { color: #ffc5bb; border-color: #8f4b4b; background: #34232a; }
.roi-card { display: grid; gap: 12px; }
.roi-options { display: flex; flex-wrap: wrap; align-items: center; gap: 15px; }
.roi-options label { font-size: 11px; display: flex; gap: 7px; align-items: center; }
select, textarea { background: var(--bg-2); border: 1px solid var(--line-2);
  border-radius: 5px; color: var(--text-1); font: inherit; padding: 7px; }
.prompt-label { display: grid; gap: 7px; font-size: 11px; }
textarea { resize: vertical; width: 100%; box-sizing: border-box; }
.roi-results { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 9px; }
.roi-result { border: 1px solid var(--line-2); background: var(--bg-2); padding: 11px; border-radius: 6px; }
.roi-result b { font-size: 12px; }.roi-result span { display: block; color: var(--text-3); font-size: 10px; margin-top: 4px; }
.roi-result p { line-height: 1.6; font-size: 11px; }.roi-result small { font-size: 10px; color: var(--text-4); }
.reid-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 1fr); gap: 18px; }
.reid-left, .reid-right { display: flex; min-width: 0; flex-direction: column; gap: 10px; }
.reid-reference-canvas { min-height: 170px; }
.reid-reference-canvas img { max-height: 320px; }
.reid-selector { font-size: 11px; display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.reid-selector select { max-width: 100%; }
.reid-rank { border-bottom: 1px solid var(--line); padding: 9px 0; display: grid; gap: 5px; }
.reid-params, .reid-backup { border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; }
.reid-params summary, .reid-backup summary { cursor: pointer; font-size: 11px; color: var(--text-2); }
.reid-param-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 12px;
  margin-top: 8px; }
.reid-param-grid label { display: flex; align-items: center; justify-content: space-between;
  gap: 8px; font-size: 11px; color: var(--text-2); }
.reid-param-grid input { width: 74px; }
.reid-decision { display: grid; gap: 3px; border-left: 3px solid var(--line);
  padding: 6px 10px; background: var(--bg-1); border-radius: 4px; }
.reid-decision b { font-size: 12px; }
.reid-decision span { font-size: 11px; color: var(--text-2); }
.reid-decision.candidate_for_review { border-left-color: #3f9d63; }
.reid-decision.insufficient_evidence { border-left-color: #c08a2e; }
.reid-decision.no_person_candidate { border-left-color: #a04545; }
@media(max-width: 1080px) { .reid-grid { grid-template-columns: 1fr; }
  .config-panel { grid-template-columns: 1fr; }
  .result-layout { grid-template-columns: 1fr; } }
</style>
