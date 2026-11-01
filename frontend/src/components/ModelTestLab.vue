<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { mainApi, type M4DetectCandidate, type M5ImageModel, type M5VisionModel,
  type M5TestDetectionResult, type M5TestRoiResult } from "../services/mainApi";
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
const roiBusy = ref(false);
const error = ref("");
const catalogError = ref("");
const threshold = ref(.35);
const detection = ref<M5TestDetectionResult | null>(null);
const roi = ref<M5TestRoiResult | null>(null);
const selectedBox = ref<[number, number, number, number] | null>(null);
const objectClass = ref<ObjectClass>("vehicle");
const prompt = ref("");
let selectionStart: [number, number] | null = null;
let generation = 0;
let catalogEpoch = 0;
const modelName = computed(() => new Map([
  ...smallModels.value.map(m => [m.id, m.name] as const),
  ...visionModels.value.map(m => [m.id, m.name] as const),
]));
const hasModels = computed(() => smallSelected.value.length + visionSelected.value.length > 0);
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
  if (small.status === "rejected" || vision.status === "rejected") {
    catalogError.value = "部分模型服务不可用。已列出可连接的模型；也可以只测另一类模型。";
  }
  loadingCatalog.value = false;
}
onMounted(() => { void loadCatalog(); });
onUnmounted(() => {
  generation++;
  if (preview.value) URL.revokeObjectURL(preview.value);
});
function loadFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const next = input.files?.[0] ?? null;
  if (preview.value) URL.revokeObjectURL(preview.value);
  generation++;
  file.value = null;
  preview.value = "";
  detection.value = null; roi.value = null; selectedBox.value = null; error.value = "";
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
  busy.value = true; error.value = "";
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
  } finally { if (ticket === generation) busy.value = false; }
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
  roiBusy.value = true; roi.value = null; error.value = "";
  try {
    const data = await mainApi.testM5Roi(chosen, b, objectClass.value,
      [...visionSelected.value], prompt.value);
    if (ticket === generation && file.value === chosen) roi.value = data;
  } catch (err) {
    if (ticket === generation) error.value = err instanceof Error ? err.message : String(err);
  } finally { if (ticket === generation) roiBusy.value = false; }
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
          {{ busy ? "正在逐模型检测…" : "开始同图对比" }}</button>
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
              <button v-for="(c, i) in detection?.candidates || []"
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
            <button v-for="(c, i) in detection.candidates" :key="i" class="candidate-tag"
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
@media(max-width: 1080px) { .config-panel { grid-template-columns: 1fr; }
  .result-layout { grid-template-columns: 1fr; } }
</style>
