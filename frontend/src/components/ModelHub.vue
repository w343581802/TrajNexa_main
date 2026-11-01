<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { mainApi, type M5ImageModel, type M5VisionModel } from "../services/mainApi";
import { modelChoices, setSmallModelIds, setVisionModelId } from "../store/modelChoices";

/**
 * 小模型 / 大模型页：版式完全照 Rebucca 的管理页——
 * 浅色卡片 + 「模型检查结果」条 + 引擎可用性条 + 七列表格（双行单元格、状态胶囊、
 * 编辑/设默认/测试/删除 四个行内操作）+ 底部分页；编辑弹窗也照 Rebucca 的字段顺序。
 * 数据仍来自 TrajNexa 自己的 /ai/* 目录接口；权重的增删改上传仍由 Rebucca 完成。
 */
const props = defineProps<{ kind: "small" | "vision" }>();
const emit = defineEmits<{ (event: "test-image"): void }>();

const PAGE_SIZE = 10;
const smallModels = ref<M5ImageModel[]>([]);
const visionModels = ref<M5VisionModel[]>([]);
const enabled = ref(false);
const busy = ref(false);
const error = ref("");
const engineFilter = ref("");
const page = ref(1);
const editing = ref<M5ImageModel | M5VisionModel | null>(null);
let latest = 0;

const isSmall = computed(() => props.kind === "small");
const filteredSmall = computed(() => smallModels.value.filter(m =>
  !engineFilter.value || m.engine === engineFilter.value));
const engines = computed(() => [...new Set(smallModels.value.map(m => m.engine))]);
/*** Rebucca 的「引擎可用性」条：按推理引擎汇总已加载模型数 */
const engineChips = computed(() => engines.value.map(name => ({
  name,
  count: smallModels.value.filter(m => m.engine === name).length,
  ok: enabled.value,
})));
const rowsCount = computed(() => isSmall.value ? filteredSmall.value.length : visionModels.value.length);
const totalPages = computed(() => Math.max(1, Math.ceil(rowsCount.value / PAGE_SIZE)));
const selectedSmallIds = computed(() => modelChoices.smallModelIds);

const ALGO_LABEL: Record<string, string> = {
  dfine: "D-FINE", yolo8: "YOLOv8", yolo11: "YOLOv11", yolov8: "YOLOv8",
};
const ENGINE_LABEL: Record<string, string> = {
  onnxruntime: "OnnxRuntime", openvino: "OpenVINO", pytorch: "PyTorch",
  yolo_pytorch: "Yolo-PyTorch", torch: "PyTorch",
};
function algoLabel(value?: string) {
  const key = (value || "detect").toLowerCase();
  return ALGO_LABEL[key] || key.toUpperCase();
}
function engineLabel(value?: string) {
  const key = (value || "cpu").toLowerCase();
  return ENGINE_LABEL[key] || value || key;
}

/** ?edit=<模型 id> 直接用该模型的编辑弹窗打开页面（演示与自测用） */
function openFromQuery<T extends { id: string }>(models: T[]) {
  const wanted = new URLSearchParams(window.location.search).get("edit");
  const hit = wanted ? models.find(item => item.id === wanted) : undefined;
  if (hit) openEdit(hit as unknown as M5ImageModel | M5VisionModel);
}

type ImageForm = {
  id: string; name: string; algorithm_type: string; task_type: string;
  engine: string; device: string; model_file: string; labels_file: string;
  input_width: number; input_height: number;
  conf_threshold: number; iou_threshold: number;
};
function blankForm(): ImageForm {
  return { id: "", name: "", algorithm_type: "yolo8", task_type: "detect",
    engine: "onnxruntime", device: "cpu", model_file: "",
    labels_file: "config/labels/coco80_zh.txt", input_width: 640, input_height: 640,
    conf_threshold: 0.35, iou_threshold: 0.5 };
}
const form = ref<ImageForm>(blankForm());
const creating = ref(false);
const saving = ref(false);
const formError = ref("");
const weights = ref<string[]>([]);
const editSha = ref("");

/** 服务端返回的目录直接覆盖本地状态，省一次往返 */
function applyCatalog(catalog: { enabled: boolean; models: M5ImageModel[] }) {
  enabled.value = catalog.enabled;
  smallModels.value = catalog.models;
}
function openEdit(model: M5ImageModel | M5VisionModel) {
  editing.value = model;
  formError.value = "";
  if (!isSmall.value) return;
  const row = model as M5ImageModel;
  form.value = { id: row.id, name: row.name,
    algorithm_type: row.algorithm_type || "yolo8",
    task_type: row.task_type || "detect", engine: row.engine,
    device: row.device || "cpu", model_file: "", labels_file: "",
    input_width: (row.input_size || [])[0] || 640,
    input_height: (row.input_size || [])[1] || 640,
    conf_threshold: row.conf_threshold ?? 0.35,
    iou_threshold: row.iou_threshold ?? 0.5 };
  editSha.value = row.model_sha256 || "";
}
async function openCreate() {
  creating.value = true;
  formError.value = "";
  form.value = blankForm();
  try {
    weights.value = (await mainApi.listImageModelWeights()).weights;
    if (!form.value.model_file && weights.value.length) form.value.model_file = weights.value[0];
  } catch (err) {
    weights.value = [];
    formError.value = err instanceof Error ? err.message : String(err);
  }
}
async function saveEdit() {
  saving.value = true;
  formError.value = "";
  try {
    const catalog = await mainApi.updateImageModel(form.value.id, {
      name: form.value.name,
      device: form.value.device,
      conf_threshold: Number(form.value.conf_threshold),
      iou_threshold: Number(form.value.iou_threshold),
    });
    applyCatalog(catalog);
    editing.value = null;
  } catch (err) {
    formError.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}
async function saveCreate() {
  saving.value = true;
  formError.value = "";
  try {
    const payload: Record<string, unknown> = {
      id: form.value.id, name: form.value.name,
      algorithm_type: form.value.algorithm_type, task_type: form.value.task_type,
      engine: form.value.engine, device: form.value.device,
      model_file: form.value.model_file,
      input_width: Number(form.value.input_width),
      input_height: Number(form.value.input_height),
      conf_threshold: Number(form.value.conf_threshold),
      iou_threshold: Number(form.value.iou_threshold),
    };
    if (form.value.labels_file.trim()) payload.labels_file = form.value.labels_file.trim();
    const catalog = await mainApi.createImageModel(payload);
    applyCatalog(catalog);
    creating.value = false;
  } catch (err) {
    formError.value = err instanceof Error ? err.message : String(err);
  } finally {
    saving.value = false;
  }
}
async function removeModel(model: M5ImageModel | M5VisionModel) {
  if (!window.confirm(`删除检测器「${model.name}」？只从配置里移除，权重文件不会删除。`)) return;
  try {
    applyCatalog(await mainApi.deleteImageModel(model.id));
    if (editing.value?.id === model.id) editing.value = null;
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  }
}
async function reloadConfig() {
  busy.value = true;
  try {
    applyCatalog(await mainApi.reloadImageModels());
    error.value = "";
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err);
  } finally {
    busy.value = false;
  }
}

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
      const kept = modelChoices.smallModelIds.filter(id => allowed.has(id));
      if (catalog.enabled) setSmallModelIds(kept.length ? kept :
        catalog.models[0] ? [catalog.models[0].id] : []);
      openFromQuery(catalog.models);
      if (new URLSearchParams(window.location.search).get("new")) void openCreate();
    } else {
      const catalog = await mainApi.listM5VisionModels();
      if (ticket !== latest) return;
      enabled.value = catalog.enabled;
      visionModels.value = catalog.models;
      const allowed = new Set(catalog.models.filter(item => item.enabled).map(model => model.id));
      openFromQuery(catalog.models);
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
watch(() => props.kind, () => { page.value = 1; engineFilter.value = ""; void loadModels(); },
  { immediate: true });
watch(rowsCount, () => { if (page.value > totalPages.value) page.value = totalPages.value; });

function toggleSmall(id: string) {
  const ids = modelChoices.smallModelIds;
  if (ids.includes(id)) setSmallModelIds(ids.filter(item => item !== id));
  else if (ids.length < 5) setSmallModelIds([...ids, id]);
  else error.value = "一次最多选择 5 个检测模型。";
}
function makeDefault(id: string) {
  if (isSmall.value) setSmallModelIds([id]);
  else setVisionModelId(id);
}
function runTest(id: string) {
  makeDefault(id);
  emit("test-image");
}
const selectedNames = computed(() => isSmall.value
  ? smallModels.value.filter(m => selectedSmallIds.value.includes(m.id)).map(m => m.name).join("、")
  : visionModels.value.find(m => m.id === modelChoices.visionModelId)?.name || "");
</script>

<template>
  <section class="rb-page">
    <div class="rb-result" :class="error ? 'is-bad' : 'is-ok'">
      <span class="rb-result-ic">{{ error ? "!" : "✓" }}</span>
      <strong>{{ isSmall ? "模型检查结果" : "大模型检查结果" }}</strong>
      <span class="rb-result-text">
        <template v-if="error">目录读取失败：{{ error }}</template>
        <template v-else-if="!isSmall">全部就绪 · 共 {{ visionModels.length }} 个已注册模型，已启用
          {{ visionModels.filter(m => m.enabled).length }} 个</template>
        <template v-else-if="enabled">全部就绪 · 共 {{ smallModels.length }} 个检测器
          {{ busy ? "（检查中）" : "：" + smallModels.length + " 个成功，失败 0 个" }}</template>
        <template v-else>未找到检测器配置：检查 backend/config/detectors.json</template>
      </span>
    </div>

    <div class="rb-card">
      <div class="rb-head">
        <h3><span class="dot" />{{ isSmall ? "小模型管理" : "大模型管理" }}</h3>
        <div class="rb-tools">
          <button v-if="isSmall" type="button" class="rb-btn rb-btn-primary"
            @click="openCreate">+ 新增小模型</button>
          <select v-if="isSmall" v-model="engineFilter" aria-label="按推理引擎筛选">
            <option value="">全部引擎</option>
            <option v-for="engine in engines" :key="engine" :value="engine">{{ engine }}</option>
          </select>
          <button type="button" class="rb-btn" :disabled="busy" @click="reloadConfig">↻ 重载配置</button>
          <button type="button" class="rb-btn" :disabled="busy" @click="loadModels">刷新列表</button>
        </div>
      </div>

      <div v-if="isSmall" class="rb-engines">
        <span class="rb-engines-lbl">引擎可用性：</span>
        <span v-if="!engineChips.length" class="rb-chip off"><i />无已加载引擎</span>
        <span v-for="chip in engineChips" :key="chip.name" class="rb-chip"
          :class="chip.ok ? 'on' : 'off'"><i />{{ engineLabel(chip.name) }} · {{ chip.count }} 个模型</span>
      </div>

      <table class="rb-table">
        <thead>
          <tr>
            <th class="c-id">ID</th>
            <th>{{ isSmall ? "小模型名称" : "模型名称" }}</th>
            <template v-if="isSmall">
              <th class="c-pair">模型类型<br /><span>任务类型</span></th>
              <th class="c-pair">推理引擎<br /><span>推理设备</span></th>
              <th class="c-pair">分辨率<br /><span>阈值</span></th>
            </template>
            <template v-else>
              <th class="c-pair">供应商<br /><span>位置</span></th>
              <th>模型 ID</th>
            </template>
            <th class="c-state">状态</th>
            <th class="c-act">操作</th>
          </tr>
        </thead>
        <tbody v-if="isSmall">
          <tr v-for="model in smallModels.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)"
            :key="model.id">
            <td class="c-id">{{ model.id }}</td>
            <td class="c-name">{{ model.name }}</td>
            <td class="c-pair"><b>{{ algoLabel(model.algorithm_type) }}</b><br />
              <span>{{ model.task_type || "detect" }}</span></td>
            <td class="c-pair"><b>{{ engineLabel(model.engine) }}</b><br />
              <span>{{ model.device || "cpu" }}</span></td>
            <td class="c-pair"><b>{{ (model.input_size || []).join("×") || "—" }}</b><br />
              <span>conf {{ (model.conf_threshold ?? 0.4).toFixed(2) }}</span></td>
            <td class="c-state">
              <span class="rb-pill on">{{ enabled ? "启用" : "未就绪" }}</span>
              <span v-if="selectedSmallIds[0] === model.id" class="rb-pill def">默认</span>
            </td>
            <td class="c-act">
              <button type="button" class="rb-link" @click="openEdit(model)">编辑</button>
              <button type="button" class="rb-link" :disabled="selectedSmallIds[0] === model.id"
                @click="makeDefault(model.id)">设默认</button>
              <button type="button" class="rb-link test" @click="runTest(model.id)">测试</button>
              <button type="button" class="rb-link" :disabled="!isSmall" :title="isSmall ? '从 detectors.json 移除，权重保留' : '大模型由服务端配置'" @click="removeModel(model)">删除</button>
            </td>
          </tr>
        </tbody>
        <tbody v-else>
          <tr v-for="model in visionModels.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)"
            :key="model.id">
            <td class="c-id">{{ model.id }}</td>
            <td class="c-name">{{ model.name }}</td>
            <td class="c-pair"><b>{{ model.provider }}</b><br />
              <span>{{ model.location === "remote" ? "远程 API" : "本地" }}</span></td>
            <td class="c-code">{{ model.model_id }}</td>
            <td class="c-state">
              <span class="rb-pill" :class="model.enabled ? 'on' : 'off'">
                {{ model.enabled ? "启用" : "停用" }}</span>
              <span v-if="modelChoices.visionModelId === model.id" class="rb-pill def">默认</span>
            </td>
            <td class="c-act">
              <button type="button" class="rb-link" @click="openEdit(model)">编辑</button>
              <button type="button" class="rb-link" :disabled="!model.enabled || modelChoices.visionModelId === model.id"
                @click="makeDefault(model.id)">设默认</button>
              <button type="button" class="rb-link test" :disabled="!model.enabled"
                @click="runTest(model.id)">测试</button>
              <button type="button" class="rb-link" :disabled="!isSmall" :title="isSmall ? '从 detectors.json 移除，权重保留' : '大模型由服务端配置'" @click="removeModel(model)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="!rowsCount" class="rb-empty">
        {{ busy ? "正在读取模型目录…" : (isSmall
          ? "暂无可选模型：点右上「+ 新增小模型」注册一个检测器。"
          : "暂无视觉模型：先配置 VISION_ENABLED 与本机 Ollama。") }}
      </div>

      <div class="rb-foot">
        <button type="button" class="rb-page-btn" :disabled="page <= 1" @click="page = 1">1</button>
        <button type="button" class="rb-page-btn" :disabled="page >= totalPages"
          @click="page = totalPages">尾页</button>
        <span class="rb-foot-count">共 {{ rowsCount }} 条</span>
        <span class="rb-foot-note">{{ isSmall ? "检测器" : "外观模型" }}：
          {{ selectedNames || "未选择" }}（选择只用于模型测试，不改研判工作台默认模型）</span>
      </div>
    </div>

    <div v-if="editing" class="rb-mask" @click.self="editing = null">
      <div class="rb-modal" role="dialog" aria-modal="true">
        <div class="rb-modal-head">
          <h4><span class="dot" />编辑{{ isSmall ? "小模型" : "大模型" }}</h4>
          <button type="button" class="rb-modal-x" aria-label="关闭" @click="editing = null">×</button>
        </div>
        <div class="rb-modal-body">
          <template v-if="isSmall">
            <div class="rb-field"><label>模型名称</label>
              <input v-model="form.name" class="rb-input" maxlength="64" />
              <p class="rb-hint">ID 是配置里的标识（{{ form.id }}），创建后不可改。</p></div>
            <div class="rb-row2">
              <div class="rb-field"><label>模型类型</label>
                <div class="rb-input is-ro">{{ algoLabel(form.algorithm_type) }}</div></div>
              <div class="rb-field"><label>任务类型</label>
                <div class="rb-input is-ro">{{ form.task_type }}</div></div>
            </div>
            <div class="rb-row2">
              <div class="rb-field"><label>推理引擎</label>
                <div class="rb-input is-ro">{{ engineLabel(form.engine) }}</div></div>
              <div class="rb-field"><label>推理设备</label>
                <select v-model="form.device" class="rb-input">
                  <option value="cpu">cpu</option>
                  <option value="gpu">gpu</option>
                </select></div>
            </div>
            <div class="rb-field"><label>模型文件</label>
              <div class="rb-input mono is-ro">sha256 {{ editSha.slice(0, 16) }}…</div>
              <p class="rb-hint">权重文件在 backend/models/ 下维护；这里只改配置，不动文件。</p></div>
            <div class="rb-row2">
              <div class="rb-field"><label>输入宽度</label>
                <div class="rb-input is-ro">{{ form.input_width }}</div></div>
              <div class="rb-field"><label>输入高度</label>
                <div class="rb-input is-ro">{{ form.input_height }}</div></div>
            </div>
            <div class="rb-row2">
              <div class="rb-field"><label>置信度阈值</label>
                <input v-model.number="form.conf_threshold" type="number" step="0.01"
                  min="0.01" max="1" class="rb-input" /></div>
              <div class="rb-field"><label>NMS IoU 阈值</label>
                <input v-model.number="form.iou_threshold" type="number" step="0.01"
                  min="0.01" max="1" class="rb-input" /></div>
            </div>
            <div class="rb-row2">
              <div class="rb-field"><label>状态</label>
                <div class="rb-input is-ro">{{ enabled ? "启用" : "未就绪" }}</div></div>
              <div class="rb-field"><label>默认</label>
                <div class="rb-input is-ro">{{ selectedSmallIds[0] === editing.id ? "是" : "否" }}</div></div>
            </div>
            <p v-if="formError" class="rb-error">{{ formError }}</p>
          </template>
          <template v-else>
            <div class="rb-field"><label>模型名称</label>
              <div class="rb-input is-ro">{{ editing.name }}</div></div>
            <div class="rb-row2">
              <div class="rb-field"><label>供应商</label>
                <div class="rb-input is-ro">{{ (editing as M5VisionModel).provider }}</div></div>
              <div class="rb-field"><label>位置</label>
                <div class="rb-input is-ro">{{ (editing as M5VisionModel).location === "remote" ? "远程 API" : "本地" }}</div></div>
            </div>
            <div class="rb-field"><label>模型 ID</label>
              <div class="rb-input mono is-ro">{{ (editing as M5VisionModel).model_id }}</div></div>
            <div class="rb-row2">
              <div class="rb-field"><label>状态</label>
                <div class="rb-input is-ro">{{ (editing as M5VisionModel).enabled ? "启用" : "停用" }}</div></div>
              <div class="rb-field"><label>默认</label>
                <div class="rb-input is-ro">{{ modelChoices.visionModelId === editing.id ? "是" : "否" }}</div></div>
            </div>
            <p class="rb-hint">大模型来自服务端 M5_VISION_MODELS_JSON / Ollama 配置，暂不支持在页面上增删。</p>
          </template>
        </div>
        <div class="rb-modal-foot">
          <button type="button" class="rb-btn" @click="editing = null">取消</button>
          <button v-if="isSmall" type="button" class="rb-btn rb-btn-primary"
            :disabled="saving" @click="saveEdit">{{ saving ? "保存中…" : "保存" }}</button>
        </div>
      </div>
    </div>

    <div v-if="creating" class="rb-mask" @click.self="creating = false">
      <div class="rb-modal" role="dialog" aria-modal="true">
        <div class="rb-modal-head">
          <h4><span class="dot" />新增小模型</h4>
          <button type="button" class="rb-modal-x" aria-label="关闭" @click="creating = false">×</button>
        </div>
        <div class="rb-modal-body">
          <div class="rb-row2">
            <div class="rb-field"><label>模型 ID</label>
              <input v-model="form.id" class="rb-input mono" placeholder="如 yolo11s" maxlength="32" />
              <p class="rb-hint">小写字母/数字/-/_，2–32 位，创建后不可改。</p></div>
            <div class="rb-field"><label>模型名称</label>
              <input v-model="form.name" class="rb-input" maxlength="64" placeholder="如 园区 YOLOv8s" /></div>
          </div>
          <div class="rb-row2">
            <div class="rb-field"><label>模型类型</label>
              <select v-model="form.algorithm_type" class="rb-input">
                <option value="dfine">D-FINE</option>
                <option value="yolo8">YOLOv8</option>
                <option value="yolo11">YOLOv11</option>
              </select></div>
            <div class="rb-field"><label>任务类型</label>
              <select v-model="form.task_type" class="rb-input">
                <option value="detect">detect</option>
                <option value="segment">segment</option>
              </select></div>
          </div>
          <div class="rb-row2">
            <div class="rb-field"><label>推理引擎</label>
              <select v-model="form.engine" class="rb-input">
                <option value="onnxruntime">OnnxRuntime</option>
                <option value="openvino">OpenVINO</option>
                <option value="pytorch">PyTorch</option>
              </select></div>
            <div class="rb-field"><label>推理设备</label>
              <select v-model="form.device" class="rb-input">
                <option value="cpu">cpu</option>
                <option value="gpu">gpu</option>
              </select></div>
          </div>
          <div class="rb-field"><label>权重文件</label>
            <select v-model="form.model_file" class="rb-input mono">
              <option value="">（models/ 下没有可用权重）</option>
              <option v-for="file in weights" :key="file" :value="file">{{ file }}</option>
            </select>
            <p class="rb-hint">把 .onnx/.pt 放进 backend/models/ 后刷新列表即可选；也可用 POST /ai/image-models/upload 上传。</p></div>
          <div class="rb-field"><label>标签文件（D-FINE 必填）</label>
            <input v-model="form.labels_file" class="rb-input mono"
              placeholder="config/labels/coco80_zh.txt" /></div>
          <div class="rb-row2">
            <div class="rb-field"><label>输入宽度</label>
              <input v-model.number="form.input_width" type="number" min="32" max="4096" class="rb-input" /></div>
            <div class="rb-field"><label>输入高度</label>
              <input v-model.number="form.input_height" type="number" min="32" max="4096" class="rb-input" /></div>
          </div>
          <div class="rb-row2">
            <div class="rb-field"><label>置信度阈值</label>
              <input v-model.number="form.conf_threshold" type="number" step="0.01"
                min="0.01" max="1" class="rb-input" /></div>
            <div class="rb-field"><label>NMS IoU 阈值</label>
              <input v-model.number="form.iou_threshold" type="number" step="0.01"
                min="0.01" max="1" class="rb-input" /></div>
          </div>
          <p v-if="formError" class="rb-error">{{ formError }}</p>
        </div>
        <div class="rb-modal-foot">
          <button type="button" class="rb-btn" @click="creating = false">取消</button>
          <button type="button" class="rb-btn rb-btn-primary" :disabled="saving"
            @click="saveCreate">{{ saving ? "新增中…" : "新增" }}</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 结构照 Rebucca 管理页（结果条 / 引擎可用性 / 七列表格 / 状态胶囊 / 行内操作 / 分页），
   配色走 TrajNexa 的深色主题变量。 */
.rb-page { flex: 1; min-height: 0; overflow: auto; padding: 18px 22px 40px;
  background: var(--bg-0); color: var(--text-1); }
.rb-result { display: flex; align-items: center; gap: 8px; max-width: 1240px; margin: 0 auto 14px;
  padding: 11px 16px; border-radius: 8px; font-size: 13px; }
.rb-result.is-ok { background: rgba(16,185,129,.10); border: 1px solid rgba(16,185,129,.38); color: #7fe0c0; }
.rb-result.is-bad { background: rgba(239,68,68,.10); border: 1px solid rgba(239,68,68,.42); color: #f0a8a8; }
.rb-result-ic { width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center;
  font-size: 12px; background: #10b981; color: #06231a; }
.rb-result.is-bad .rb-result-ic { background: #ef4444; color: #2a0c0c; }
.rb-result strong { font-weight: 650; color: inherit; }
.rb-result-text { opacity: .85; }
.rb-card { max-width: 1240px; margin: 0 auto; background: var(--bg-1); border: 1px solid var(--line-2);
  border-radius: 10px; overflow: hidden; }
.rb-head { display: flex; align-items: center; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; padding: 14px 20px; border-bottom: 1px solid var(--line);
  background: linear-gradient(135deg, rgba(22,159,133,.12) 0%, rgba(22,159,133,.03) 100%); }
.rb-head h3 { margin: 0; font-size: 15px; font-weight: 600; color: var(--text-0);
  display: flex; align-items: center; gap: 8px; }
.rb-head h3 .dot, .rb-modal-head h4 .dot { width: 8px; height: 8px; border-radius: 50%;
  background: #169f85; box-shadow: 0 0 0 3px rgba(22,159,133,.18); }
.rb-tools { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.rb-tools select { padding: 6px 10px; font-size: 12px; border: 1px solid var(--line-2);
  border-radius: 6px; background: var(--bg-2); color: var(--text-1); }
.rb-btn { padding: 7px 14px; font-size: 12px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line-3); background: var(--bg-2); color: var(--text-1);
  text-decoration: none; display: inline-flex; align-items: center; gap: 4px; }
.rb-btn:hover { color: #35c9a6; border-color: #169f85; background: rgba(22,159,133,.10); }
.rb-btn:disabled { opacity: .45; cursor: not-allowed; }
.rb-btn-primary { background: linear-gradient(135deg, #14b89a 0%, #169f85 100%);
  color: #fff; border-color: transparent; font-weight: 600; }
.rb-btn-primary:hover { color: #fff; filter: brightness(1.08); background: linear-gradient(135deg, #17c3a3, #169f85); }
.rb-engines { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 11px 20px; background: var(--bg-2); border-bottom: 1px solid var(--line); }
.rb-engines-lbl { font-size: 11px; color: var(--text-3); font-weight: 600; }
.rb-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px;
  border-radius: 14px; font-size: 11px; font-weight: 600; border: 1px solid var(--line-2);
  background: var(--bg-1); color: var(--text-3); }
.rb-chip i { width: 7px; height: 7px; border-radius: 50%; background: #9ca3af; }
.rb-chip.on { background: rgba(16,185,129,.14); color: #4fd1b0; border-color: rgba(16,185,129,.38); }
.rb-chip.on i { background: #10b981; }
.rb-chip.off { background: rgba(239,68,68,.12); color: #e88b8b; border-color: rgba(239,68,68,.36); }
.rb-chip.off i { background: #ef4444; }
.rb-table { width: 100%; border-collapse: collapse; }
.rb-table th, .rb-table td { padding: 11px 16px; font-size: 13px; text-align: left;
  border-bottom: 1px solid var(--line); vertical-align: middle; }
.rb-table th { background: var(--bg-2); color: var(--text-3); font-size: 12px; font-weight: 600;
  white-space: nowrap; }
.rb-table th span { font-weight: 500; color: var(--text-4); }
.rb-table tbody tr:hover td { background: rgba(127,127,127,.06); }
.c-id { width: 96px; color: var(--text-3); font-size: 12px; }
.c-name { font-weight: 620; color: var(--text-0); }
.c-pair b { font-weight: 600; color: var(--text-1); font-size: 13px; }
.c-pair span { color: var(--text-4); font-size: 12px; }
.c-code { font-family: Consolas, Monaco, monospace; font-size: 12px; color: var(--text-2); }
.c-state { width: 150px; white-space: nowrap; }
.c-act { width: 250px; white-space: nowrap; }
.rb-pill { display: inline-block; padding: 2px 9px; border-radius: 10px; font-size: 11px;
  font-weight: 600; vertical-align: middle; }
.rb-pill + .rb-pill { margin-left: 6px; }
.rb-pill.on { background: rgba(16,185,129,.16); color: #4fd1b0; }
.rb-pill.off { background: rgba(239,68,68,.16); color: #e88b8b; }
.rb-pill.def { background: rgba(245,158,11,.18); color: #e8b268; }
.rb-link { padding: 4px 10px; margin-right: 6px; font-size: 11px; border-radius: 5px;
  border: 1px solid var(--line-3); background: var(--bg-2); color: var(--text-2); cursor: pointer; }
.rb-link:hover { color: #35c9a6; border-color: #169f85; }
.rb-link.test { color: #4fd1b0; border-color: rgba(22,159,133,.55); background: rgba(22,159,133,.10); }
.rb-link:disabled { opacity: .4; cursor: not-allowed; }
.rb-empty { padding: 48px 20px; text-align: center; color: var(--text-4); font-size: 13px; }
.rb-foot { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 13px 20px; border-top: 1px solid var(--line); background: var(--bg-2); }
.rb-page-btn { min-width: 34px; height: 32px; border-radius: 6px; border: 1px solid var(--line-3);
  background: var(--bg-1); color: var(--text-2); font-size: 13px; cursor: pointer; }
.rb-page-btn:disabled { opacity: .45; cursor: not-allowed; }
.rb-foot-count { font-size: 12px; color: var(--text-3); }
.rb-foot-note { margin-left: auto; font-size: 11px; color: var(--text-4); }
.rb-mask { position: fixed; inset: 0; z-index: 300; background: rgba(3,8,13,.62);
  display: grid; place-items: center; padding: 20px; }
.rb-modal { width: min(560px, 100%); max-height: 88vh; overflow: auto; background: var(--bg-1);
  border: 1px solid var(--line-2); border-radius: 12px; box-shadow: 0 18px 48px rgba(0,0,0,.55); }
.rb-modal-head { display: flex; align-items: center; justify-content: space-between;
  padding: 14px 20px; border-bottom: 1px solid var(--line);
  background: linear-gradient(135deg, rgba(22,159,133,.12) 0%, rgba(22,159,133,.03) 100%); }
.rb-modal-head h4 { margin: 0; font-size: 15px; font-weight: 600; color: var(--text-0);
  display: flex; align-items: center; gap: 8px; }
.rb-modal-x { border: none; background: transparent; font-size: 20px; line-height: 1;
  color: var(--text-3); cursor: pointer; }
.rb-modal-x:hover { color: var(--text-1); }
.rb-modal-body { padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; }
.rb-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.rb-field { display: flex; flex-direction: column; gap: 6px; }
.rb-field label { font-size: 12px; color: var(--text-3); font-weight: 600; }
.rb-input { padding: 8px 11px; font-size: 13px; border: 1px solid var(--line-2); border-radius: 6px;
  background: var(--bg-2); color: var(--text-1); min-height: 36px; display: flex; align-items: center; }
.rb-input.mono { font-family: Consolas, Monaco, monospace; font-size: 12px; overflow-wrap: anywhere; }
.rb-hint { margin: 0; font-size: 11px; color: var(--text-4); }
input.rb-input, select.rb-input { font: inherit; min-height: 36px; }
input.rb-input:focus, select.rb-input:focus { outline: none; border-color: #169f85; }
.rb-input.is-ro { color: var(--text-3); background: rgba(255,255,255,.02); }
.rb-error { margin: 0; font-size: 12px; color: #f0a8a8; background: rgba(239,68,68,.10);
  border: 1px solid rgba(239,68,68,.35); border-radius: 6px; padding: 8px 10px; }
.rb-modal-foot { display: flex; justify-content: flex-end; gap: 10px; padding: 14px 20px;
  border-top: 1px solid var(--line); background: var(--bg-2); }
@media (max-width: 900px) {
  .rb-page { padding: 12px 12px 32px; }
  .rb-table th, .rb-table td { padding: 9px 10px; font-size: 12px; }
  .c-act, .c-state { width: auto; }
}
</style>
