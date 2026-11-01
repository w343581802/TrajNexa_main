<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppIcon from "./ui/AppIcon.vue";
import { mainApi, type M5ImageModel, type M5VisionModel } from "../services/mainApi";
import { modelChoices, setSmallModelIds, setVisionModelId } from "../store/modelChoices";

const props = defineProps<{ kind: "small" | "vision" }>();
const emit = defineEmits<{ (event: "test-image"): void }>();

const smallModels = ref<M5ImageModel[]>([]);
const visionModels = ref<M5VisionModel[]>([]);
const enabled = ref(false);
const busy = ref(false);
const error = ref("");
let latest = 0;
const isSmall = computed(() => props.kind === "small");
const modelsCount = computed(() => isSmall.value ? smallModels.value.length : visionModels.value.length);
const selectedCount = computed(() => isSmall.value ? modelChoices.smallModelIds.length :
  (modelChoices.visionModelId ? 1 : 0));

async function loadModels() {
  const ticket = ++latest;
  busy.value = true;
  error.value = "";
  try {
    if (isSmall.value) {
      const catalog = await mainApi.listM5ImageModels();
      if (ticket !== latest) return;
      enabled.value = catalog.enabled;
      smallModels.value = catalog.models;
      const allowed = new Set(catalog.models.map(model => model.id));
      const selected = modelChoices.smallModelIds.filter(id => allowed.has(id));
      // First visit: select only the first detector, not every model by surprise.
      if (catalog.enabled) setSmallModelIds(selected.length ? selected :
        catalog.models[0] ? [catalog.models[0].id] : []);
    } else {
      const catalog = await mainApi.listM5VisionModels();
      if (ticket !== latest) return;
      enabled.value = catalog.enabled;
      visionModels.value = catalog.models;
      const allowed = new Set(catalog.models.map(model => model.id));
      if (!allowed.has(modelChoices.visionModelId)) {
        setVisionModelId(catalog.models[0]?.id || "");
      }
    }
  } catch (err) {
    if (ticket !== latest) return;
    enabled.value = false;
    if (isSmall.value) smallModels.value = [];
    else visionModels.value = [];
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    if (ticket === latest) busy.value = false;
  }
}
watch(() => props.kind, () => { void loadModels(); }, { immediate: true });

function toggleSmall(id: string) {
  const ids = modelChoices.smallModelIds;
  if (ids.includes(id)) setSmallModelIds(ids.filter(item => item !== id));
  else if (ids.length < 5) setSmallModelIds([...ids, id]);
  else error.value = "一次最多选择 5 个检测模型。";
}

const selectedSmallNames = computed(() => smallModels.value.filter(m =>
  modelChoices.smallModelIds.includes(m.id)).map(m => m.name).join("、"));
const selectedVisionName = computed(() => visionModels.value.find(m =>
  m.id === modelChoices.visionModelId)?.name || "未选择");
</script>

<template>
  <section class="model-hub">
    <div class="hub-titlebar">
      <div>
        <p class="hub-eyebrow">AI MODEL CENTER / M5</p>
        <h1>{{ isSmall ? "小模型管理" : "大模型管理" }}</h1>
        <p class="hub-intro">
          {{ isSmall
            ? "复用 Rebucca 已配置的目标检测模型。可以多选，并在同一张监控截图中对照结果。"
            : "选择视觉理解模型，用于检测框选中后的人员、车辆外观分析。支持添加本机已配置的其他 Ollama 模型。" }}
        </p>
      </div>
      <div class="hub-actions">
        <button type="button" class="button-outline" :disabled="busy" @click="loadModels">
          <AppIcon name="refresh" :size="14" /> {{ busy ? "读取中…" : "刷新模型" }}
        </button>
        <button type="button" class="button-primary"
          :disabled="!enabled || selectedCount === 0"
          @click="emit('test-image')">
          <AppIcon name="scan" :size="14" /> 进入图片测试
        </button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-tile">
        <span>已配置模型</span><strong>{{ modelsCount }}</strong>
        <small>{{ isSmall ? "来自 Rebucca 独立模型服务" : "来自 TrajNexa 视觉模型配置" }}</small>
      </div>
      <div class="summary-tile">
        <span>当前选择</span><strong>{{ selectedCount }}</strong>
        <small>{{ isSmall ? "同图支持多模型" : "图片 ROI 默认使用一个模型" }}</small>
      </div>
      <div class="summary-tile">
        <span>服务状态</span>
        <strong :class="enabled ? 'status-ok' : 'status-muted'">{{ enabled ? "已配置" : "不可用" }}</strong>
        <small>{{ isSmall ? "以服务实际注册列表为准" : "仅表示配置启用，不代表权重已加载" }}</small>
      </div>
    </div>

    <div v-if="error" class="hub-notice error">
      <AppIcon name="warning" :size="16" /><span>{{ error }}</span>
    </div>
    <div v-if="!busy && !enabled && !error" class="hub-notice">
      <AppIcon name="info" :size="16" />
      <span v-if="isSmall">Rebucca 图片模型服务未启用或尚未部署。请参考 M5 图片部署文档启用服务；旧 D-FINE、Qwen 和手动框选不受影响。</span>
      <span v-else>视觉模型功能未启用。请先配置 VISION_ENABLED 与本机 Ollama；模型条目仅代表已配置，不代表已经下载成功。</span>
    </div>

    <div class="hub-panel">
      <div class="panel-header">
        <div>
          <h2>{{ isSmall ? "检测模型列表" : "视觉模型列表" }}</h2>
          <p>{{ isSmall ? "勾选多个模型，对同一张图片分别推理；各检测结果保留来源。" :
            "选择一个默认大模型，图片分析时应用于选中目标的外观描述。" }}</p>
        </div>
        <span class="panel-count">{{ modelsCount }} 项</span>
      </div>
      <div v-if="busy" class="empty-state">正在读取模型目录…</div>
      <div v-else-if="modelsCount === 0" class="empty-state">
        <AppIcon name="layers" :size="24" />
        <strong>暂无可选模型</strong>
        <span>{{ isSmall ? "需要先启动 Rebucca 图片模型服务并在配置中注册检测器。" :
          "在本地配置 Ollama 视觉模型后刷新列表。" }}</span>
      </div>
      <div v-else-if="isSmall" class="model-list">
        <label v-for="model in smallModels" :key="model.id" class="model-row"
          :class="{ chosen: modelChoices.smallModelIds.includes(model.id) }">
          <input type="checkbox" :checked="modelChoices.smallModelIds.includes(model.id)"
            :disabled="!enabled" @change="toggleSmall(model.id)" />
          <span class="model-glyph"><AppIcon name="layers" :size="19" /></span>
          <span class="model-detail">
            <strong>{{ model.name }}</strong>
            <small>{{ model.id }} · {{ model.engine }} · 权重 {{ model.model_sha256.slice(0, 10) }}…</small>
          </span>
          <span class="model-status">{{ modelChoices.smallModelIds.includes(model.id) ? "已选择" : "可选" }}</span>
        </label>
      </div>
      <div v-else class="model-list">
        <label v-for="model in visionModels" :key="model.id" class="model-row"
          :class="{ chosen: modelChoices.visionModelId === model.id }">
          <input type="radio" name="m5-vision-model" :value="model.id"
            :checked="modelChoices.visionModelId === model.id"
            :disabled="!model.enabled" @change="setVisionModelId(model.id)" />
          <span class="model-glyph"><AppIcon name="scan" :size="19" /></span>
          <span class="model-detail">
            <strong>{{ model.name }}</strong>
            <small>{{ model.model_id }} · {{ model.provider }} · 本地已配置</small>
          </span>
          <span class="model-status">{{ modelChoices.visionModelId === model.id ? "默认模型" : "可选" }}</span>
        </label>
      </div>
    </div>

    <div class="current-selection">
      <div class="current-heading"><AppIcon name="check" :size="16" /><b>当前图片分析配置</b></div>
      <p v-if="isSmall">检测器：{{ selectedSmallNames || "尚未选择检测模型" }}。
        检测结果只形成候选，用户点框后交给大模型描述。</p>
      <p v-else>外观描述模型：{{ selectedVisionName }}。
        目标裁剪优先采用原始图片像素；提示词可在“添加目标要素”里临时编辑。</p>
      <p class="disclaimer">当前为 Demo 阶段的本地选择，不自动开启摄像头视频分析，也不确认目标身份。</p>
    </div>
  </section>
</template>

<style scoped>
.model-hub { flex: 1; min-height: 0; overflow: auto; padding: 31px 36px 56px;
  background: var(--bg-0); color: var(--text-1); }
.hub-titlebar { display: flex; justify-content: space-between; gap: 22px; align-items: flex-start; }
.hub-eyebrow { margin: 0 0 9px; color: #69aece; font-size: 10px; font-weight: 750;
  letter-spacing: .16em; }
h1 { font-size: 25px; font-weight: 690; margin: 0 0 10px; color: var(--text-0); }
.hub-intro { margin: 0; color: var(--text-3); font-size: 12px; max-width: 720px; line-height: 1.7; }
.hub-actions { display: flex; gap: 8px; flex: none; padding-top: 12px; }
.button-outline, .button-primary { display: inline-flex; align-items: center; gap: 7px;
  min-height: 35px; padding: 0 14px; border-radius: 7px; cursor: pointer;
  font-size: 12px; font-weight: 620; }
.button-outline { border: 1px solid var(--line-3); color: var(--text-1); background: var(--bg-2); }
.button-primary { border: 1px solid #278fb6; color: #e9f7ff; background: #17658b; }
.button-outline:hover, .button-primary:hover { filter: brightness(1.16); }
button:disabled { opacity: .45; cursor: not-allowed; }
.summary-grid { margin: 27px 0 20px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.summary-tile { min-height: 99px; border: 1px solid var(--line-2);
  background: linear-gradient(145deg, #101c2a, #0d141e); border-radius: 9px;
  padding: 15px 18px; display: flex; flex-direction: column; gap: 6px; }
.summary-tile span { font-size: 11px; color: var(--text-3); }
.summary-tile strong { color: var(--text-0); font-size: 24px; }
.summary-tile strong.status-ok { color: #5ad5aa; font-size: 17px; }
.summary-tile strong.status-muted { color: #e9b876; font-size: 17px; }
.summary-tile small { font-size: 10px; color: var(--text-4); }
.hub-notice { display: flex; align-items: center; gap: 10px; margin: 12px 0;
  padding: 12px 14px; border: 1px solid #3d5b76; border-radius: 7px;
  background: #13263a; color: #b7d5e9; font-size: 11px; }
.hub-notice.error { color: #efc0b7; background: #311f24; border-color: #814b52; }
.hub-notice :deep(svg) { flex: none; }
.hub-panel { border: 1px solid var(--line-2); border-radius: 10px;
  background: var(--bg-1); overflow: hidden; }
.panel-header { display: flex; justify-content: space-between; align-items: center;
  gap: 16px; padding: 19px 20px 17px; border-bottom: 1px solid var(--line); }
.panel-header h2 { margin: 0 0 6px; font-size: 15px; color: var(--text-0); }
.panel-header p { margin: 0; font-size: 11px; color: var(--text-3); }
.panel-count { color: var(--text-3); font-size: 11px; white-space: nowrap; }
.model-list { padding: 7px 14px 12px; }
.model-row { display: flex; gap: 14px; align-items: center;
  padding: 13px 14px; margin: 5px 0; border-radius: 7px; border: 1px solid transparent;
  cursor: pointer; }
.model-row:hover { background: var(--bg-2); }
.model-row.chosen { background: rgba(37, 123, 167, .1); border-color: rgba(62, 132, 181, .35); }
.model-row input { width: 15px; height: 15px; accent-color: #43aad3; flex: none; }
.model-glyph { display: grid; place-items: center; color: #7abbd9; width: 34px; height: 34px;
  border: 1px solid var(--line-2); background: var(--bg-2); border-radius: 7px; flex: none; }
.model-detail { flex: 1; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.model-detail strong { color: var(--text-0); font-size: 12px; font-weight: 650; }
.model-detail small { color: var(--text-3); font-size: 10px; overflow-wrap: anywhere; }
.model-status { border: 1px solid var(--line-2); border-radius: 5px; padding: 4px 8px;
  color: var(--text-3); font-size: 10px; white-space: nowrap; }
.chosen .model-status { color: #78d4ab; border-color: #2d785d; }
.empty-state { min-height: 172px; display: flex; flex-direction: column; gap: 12px;
  justify-content: center; align-items: center; text-align: center;
  color: var(--text-4); font-size: 11px; padding: 24px; }
.empty-state strong { color: var(--text-2); font-size: 13px; }
.current-selection { margin-top: 20px; padding: 20px; border: 1px solid var(--line-2);
  border-radius: 9px; background: var(--bg-1); font-size: 11px;
  line-height: 1.8; color: var(--text-2); }
.current-heading { display: flex; gap: 8px; align-items: center; color: #83d5bb; font-size: 13px; }
.current-selection p { margin: 8px 0 0; }
.current-selection .disclaimer { color: var(--text-4); }
@media (max-width: 960px) {
  .model-hub { padding: 22px 20px 38px; }
  .hub-titlebar { flex-direction: column; }
  .hub-actions { padding-top: 0; }
}
@media (max-width: 680px) { .summary-grid { grid-template-columns: 1fr; } }
</style>
