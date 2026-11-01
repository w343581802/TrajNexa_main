<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { mainApi, type M5ImageModel, type M5VisionModel } from "../services/mainApi";
import { modelChoices, setSmallModelIds, setVisionModelId } from "../store/modelChoices";

/**
 * 小模型 / 大模型页：数据来自 TrajNexa 自己的 /ai/* 目录接口，
 * 但版式沿用 Rebucca 管理页的显示方式——
 * 卡片头（标题 + 圆点 + 工具条）→ 引擎状态条 → 表格（状态标签 + 行内操作）→ 底部汇总。
 * 模型的新增/编辑/上传仍在 Rebucca 里做，这里只给「在 Rebucca 中管理」的入口。
 */
const props = defineProps<{ kind: "small" | "vision" }>();
const emit = defineEmits<{ (event: "test-image"): void }>();

const smallModels = ref<M5ImageModel[]>([]);
const visionModels = ref<M5VisionModel[]>([]);
const enabled = ref(false);
const busy = ref(false);
const error = ref("");
const engineFilter = ref("");
const taskFilter = ref("");
let latest = 0;

const isSmall = computed(() => props.kind === "small");
const filteredSmall = computed(() => smallModels.value.filter(m =>
  (!engineFilter.value || m.engine === engineFilter.value) &&
  (!taskFilter.value || (m.task_type || "detect") === taskFilter.value)));
const engines = computed(() => [...new Set(smallModels.value.map(m => m.engine))]);
const tasks = computed(() => [...new Set(smallModels.value.map(m => m.task_type || "detect"))]);
/** Rebucca 的引擎状态条：按推理引擎汇总本批已加载的模型数 */
const engineChips = computed(() => engines.value.map(name => ({
  name,
  count: smallModels.value.filter(m => m.engine === name).length,
  on: enabled.value,
})));
const modelsCount = computed(() => isSmall.value ? smallModels.value.length : visionModels.value.length);
const selectedCount = computed(() => isSmall.value ? modelChoices.smallModelIds.length :
  (modelChoices.visionModelId ? 1 : 0));

function rebuccaBase(): string {
  const fromQuery = typeof window !== "undefined"
    ? new URLSearchParams(window.location.search).get("rebucca") : null;
  const fromEnv = (import.meta.env.VITE_REBUCCA_WEB_URL as string | undefined)?.trim();
  return (fromQuery || fromEnv || "http://127.0.0.1:10001").replace(/\/+$/, "");
}
const rebuccaUrl = computed(() => rebuccaBase() + (isSmall.value ? "/smallmodel/index" : "/llm/index"));

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
      // 首次进入只预选第一个检测器，避免整批被静默选中
      if (catalog.enabled) setSmallModelIds(selected.length ? selected :
        catalog.models[0] ? [catalog.models[0].id] : []);
    } else {
      const catalog = await mainApi.listM5VisionModels();
      if (ticket !== latest) return;
      enabled.value = catalog.enabled;
      visionModels.value = catalog.models;
      const allowed = new Set(catalog.models.filter(item => item.enabled).map(model => model.id));
      if (!allowed.has(modelChoices.visionModelId)) {
        setVisionModelId(catalog.models.find(item => item.enabled && item.default_for_roi)?.id ||
          catalog.models.find(item => item.enabled)?.id || "");
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
    <div class="model-card">
      <div class="model-head">
        <h3><span class="dot" />{{ isSmall ? "小模型管理" : "大模型管理" }}</h3>
        <div class="model-toolbar">
          <template v-if="isSmall">
            <select v-model="engineFilter" aria-label="按推理引擎筛选">
              <option value="">全部引擎</option>
              <option v-for="engine in engines" :key="engine" :value="engine">{{ engine }}</option>
            </select>
            <select v-model="taskFilter" aria-label="按任务筛选">
              <option value="">全部任务</option>
              <option v-for="task in tasks" :key="task" :value="task">{{ task }}</option>
            </select>
          </template>
          <button type="button" class="btn" :disabled="busy" @click="loadModels">
            {{ busy ? "读取中…" : "↻ 刷新" }}</button>
          <a class="btn" :href="rebuccaUrl" target="_blank" rel="noopener noreferrer">
            在 Rebucca 中管理</a>
          <button type="button" class="btn btn-primary" :disabled="selectedCount === 0"
            @click="emit('test-image')">进入模型测试</button>
        </div>
      </div>

      <div v-if="isSmall" class="engine-row">
        <span class="engine-lbl">推理引擎</span>
        <span v-if="!engineChips.length" class="engine-lbl">暂无已加载引擎</span>
        <span v-for="chip in engineChips" :key="chip.name" class="engine-chip"
          :class="chip.on ? 'on' : 'off'">
          <span class="ic" />{{ chip.name }}<span class="ver">{{ chip.count }} 个模型</span>
        </span>
      </div>

      <div v-if="error" class="model-notice error">{{ error }}</div>

      <table v-if="!busy && modelsCount > 0" class="model-table">
        <thead>
          <tr>
            <th class="col-id">ID</th>
            <th>模型名称</th>
            <template v-if="isSmall">
              <th>类型 / 任务</th>
              <th>引擎 / 设备</th>
              <th>分辨率 / 权重</th>
            </template>
            <template v-else>
              <th>供应商</th>
              <th>位置</th>
              <th>模型 ID</th>
            </template>
            <th class="col-state">状态</th>
            <th class="col-act">操作</th>
          </tr>
        </thead>
        <tbody v-if="isSmall">
          <tr v-for="model in filteredSmall" :key="model.id"
            :class="{ 'is-chosen': modelChoices.smallModelIds.includes(model.id) }">
            <td class="cell-code">{{ model.id }}</td>
            <td class="cell-name">{{ model.name }}</td>
            <td>{{ model.algorithm_type || "检测" }} / {{ model.task_type || "detect" }}</td>
            <td>{{ model.engine }} / {{ model.device || "cpu" }}</td>
            <td class="cell-code">{{ (model.input_size || []).join("×") || "—" }} ·
              {{ model.model_sha256.slice(0, 8) }}…</td>
            <td class="col-state">
              <span class="tag" :class="modelChoices.smallModelIds.includes(model.id) ? 'tag-on' : 'tag-off'">
                {{ modelChoices.smallModelIds.includes(model.id) ? "已选择" : "可选" }}</span>
            </td>
            <td class="col-act">
              <button type="button" class="btn btn-sm" :disabled="!enabled"
                @click="toggleSmall(model.id)">
                {{ modelChoices.smallModelIds.includes(model.id) ? "取消选择" : "选用" }}</button>
            </td>
          </tr>
        </tbody>
        <tbody v-else>
          <tr v-for="model in visionModels" :key="model.id"
            :class="{ 'is-chosen': modelChoices.visionModelId === model.id }">
            <td class="cell-code">{{ model.id }}</td>
            <td class="cell-name">{{ model.name }}</td>
            <td>{{ model.provider }}</td>
            <td>{{ model.location === "remote" ? "远程 API" : "本地" }}</td>
            <td class="cell-code">{{ model.model_id }}</td>
            <td class="col-state">
              <span class="tag" :class="model.enabled ? 'tag-on' : 'tag-off'">
                {{ model.enabled ? "已启用" : "未启用" }}</span>
              <span v-if="modelChoices.visionModelId === model.id" class="tag tag-mine">实验首选</span>
            </td>
            <td class="col-act">
              <button type="button" class="btn btn-sm"
                :disabled="!model.enabled || modelChoices.visionModelId === model.id"
                @click="setVisionModelId(model.id)">设为首选</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-else-if="busy" class="model-empty">正在读取模型目录…</div>
      <div v-else class="model-empty">
        <strong>暂无可选模型</strong>
        <span v-if="isSmall">需要先启动 Rebucca 图片模型服务并在配置中注册检测器。</span>
        <span v-else>请先配置 VISION_ENABLED 与本机 Ollama，再刷新列表。</span>
      </div>

      <div class="model-foot">
        <span>共 {{ modelsCount }} 项 · 实验预选 {{ selectedCount }} 项</span>
        <span>{{ isSmall
          ? "检测器：" + (selectedSmallNames || "尚未选择")
          : "外观描述模型：" + selectedVisionName }}</span>
        <span class="dim">选择只用于模型测试，不改变研判工作台的默认模型，也不确认目标身份。</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.model-hub { flex: 1; min-height: 0; overflow: auto; padding: 26px 30px 48px;
  background: var(--bg-0); color: var(--text-1); }
.model-card { max-width: 1200px; margin: 0 auto; border: 1px solid var(--line-2);
  border-radius: 10px; background: var(--bg-1); overflow: hidden; }
.model-head { display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-wrap: wrap; padding: 13px 20px;
  background: linear-gradient(135deg, rgba(22,159,133,.10), rgba(22,159,133,.03));
  border-bottom: 1px solid var(--line); }
.model-head h3 { margin: 0; display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600; color: var(--text-0); }
.model-head h3 .dot { width: 8px; height: 8px; border-radius: 50%; background: #169f85;
  box-shadow: 0 0 0 3px rgba(22,159,133,.15); }
.model-toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.model-toolbar select { padding: 6px 10px; font-size: 12px; border-radius: 6px;
  border: 1px solid var(--line-2); background: var(--bg-2); color: var(--text-1); }
.btn { padding: 7px 14px; font-size: 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line-3); background: var(--bg-2); color: var(--text-1);
  text-decoration: none; display: inline-flex; align-items: center; }
.btn:hover { color: #35c9a6; border-color: #169f85; }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.btn-primary { background: linear-gradient(135deg, #14b89a, #169f85); color: #fff;
  border-color: transparent; font-weight: 600; }
.btn-primary:hover { filter: brightness(1.08); color: #fff; }
.btn-sm { padding: 4px 10px; font-size: 11px; }
.engine-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 10px 20px; background: var(--bg-2); border-bottom: 1px solid var(--line); }
.engine-lbl { font-size: 11px; color: var(--text-3); font-weight: 600; margin-right: 4px; }
.engine-chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;
  border-radius: 14px; font-size: 11px; font-weight: 600;
  border: 1px solid var(--line-2); background: var(--bg-1); color: var(--text-3); }
.engine-chip .ic { width: 7px; height: 7px; border-radius: 50%; background: #9ca3af; }
.engine-chip.on { background: rgba(22,159,133,.14); color: #4fd1b0; border-color: rgba(22,159,133,.45); }
.engine-chip.on .ic { background: #10b981; }
.engine-chip.off { background: rgba(239,68,68,.10); color: #e88b8b; border-color: rgba(239,68,68,.35); }
.engine-chip.off .ic { background: #ef4444; }
.engine-chip .ver { font-size: 10px; opacity: .72; font-weight: 500; }
.model-notice { margin: 12px 20px; padding: 10px 12px; border-radius: 7px; font-size: 11px; }
.model-notice.error { background: rgba(129,75,82,.25); border: 1px solid #814b52; color: #efc0b7; }
.model-table { width: 100%; border-collapse: collapse; }
.model-table th, .model-table td { padding: 10px 14px; font-size: 13px; text-align: left;
  border-bottom: 1px solid var(--line); vertical-align: middle; }
.model-table th { background: var(--bg-2); color: var(--text-3); font-size: 12px;
  font-weight: 600; white-space: nowrap; }
.model-table tbody tr:hover td { background: rgba(127,127,127,.06); }
.model-table tr.is-chosen td { background: rgba(22,159,133,.07); }
.col-id { width: 120px; }
.col-state { width: 158px; }
.col-act { width: 108px; }
.cell-code { font-family: Consolas, Monaco, monospace; font-size: 12px; color: var(--text-2); }
.cell-name { color: var(--text-0); font-weight: 620; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px;
  font-weight: 600; white-space: nowrap; vertical-align: middle; }
.tag + .tag { margin-left: 6px; }
.tag-on { background: rgba(16,185,129,.16); color: #4fd1b0; }
.tag-off { background: rgba(127,127,127,.14); color: var(--text-3); }
.tag-mine { background: rgba(23,101,139,.28); color: #8ecbe6; }
.model-empty { padding: 48px 20px; text-align: center; color: var(--text-4); font-size: 13px;
  display: flex; flex-direction: column; gap: 8px; align-items: center; }
.model-empty strong { color: var(--text-2); font-size: 14px; }
.model-foot { display: flex; gap: 18px; flex-wrap: wrap; padding: 12px 20px;
  border-top: 1px solid var(--line); background: var(--bg-2); font-size: 11px;
  color: var(--text-3); }
.model-foot .dim { color: var(--text-4); }
@media (max-width: 860px) {
  .model-hub { padding: 18px 14px 36px; }
  .model-table th, .model-table td { padding: 8px 10px; font-size: 12px; }
}
</style>
