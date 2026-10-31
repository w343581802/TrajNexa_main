<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import { mainApi, type CameraCatalogEntry, type RecordingClip } from "../../services/mainApi";

const props = defineProps<{ open: boolean; clip: RecordingClip | null }>();
const emit = defineEmits<{
  (event: "close"): void;
  (event: "save", name: string, note: string): void;
}>();

const name = ref("");
const note = ref("");
const frameOk = ref(true);
const cameras = ref<CameraCatalogEntry[]>([]);
const catalogNote = ref<string | null>(null);

const frameUrl = computed(() => (props.clip ? mainApi.recordingClipFrameUrl(props.clip.id) : ""));

/** 台账名称比较口径：去掉首尾空格、合并内部空格、统一小写 */
function norm(text: string) {
  return text.trim().replace(/\s+/g, " ").toLowerCase();
}

/** 打开时把当前识别名称填进来，名称来源顺便说清楚 */
watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    name.value = props.clip?.camera_name ?? "";
    note.value = "";
    frameOk.value = true;
    if (cameras.value.length) return;
    try {
      const catalog = await mainApi.listCameras();
      cameras.value = catalog.cameras.filter((camera) => camera.active !== false);
      const ok = ["ok", "available"].includes(catalog.status) && cameras.value.length > 0;
      catalogNote.value = ok ? null : "摄像头台账当前不可用，保存后只会记录名称";
    } catch {
      catalogNote.value = "摄像头台账读取失败，保存后只会记录名称";
    }
  },
);

/** 边改名称边在本地预览会命中哪台摄像头；最终以后端匹配为准 */
const preview = computed(() => {
  const value = norm(name.value);
  if (!value) return { kind: "empty" as const, list: [] as CameraCatalogEntry[] };
  const exact = cameras.value.filter(
    (camera) => norm(camera.display_name) === value || camera.aliases.some((alias) => norm(alias) === value),
  );
  if (exact.length === 1) return { kind: "exact" as const, list: exact };
  if (exact.length > 1) return { kind: "multi" as const, list: exact };
  const close = cameras.value
    .filter((camera) => {
      const display = norm(camera.display_name);
      return display.length >= 2 && (display.includes(value) || value.includes(display));
    })
    .slice(0, 5);
  if (close.length) return { kind: "suggest" as const, list: close };
  return { kind: "none" as const, list: [] as CameraCatalogEntry[] };
});

const multiNames = computed(() => preview.value.list.map((item) => item.display_name).join("、"));

const sourceText = computed(() => {
  const value = props.clip?.name_source;
  if (value === "ocr") return "画面角标";
  if (value === "filename") return "文件名兜底，未经角标确认";
  if (value === "manual") return "人工填写";
  return "未识别";
});

function pick(camera: CameraCatalogEntry) {
  name.value = camera.display_name;
}

function submit() {
  emit("save", name.value, note.value);
}
</script>

<template>
  <BaseModal
    :open="open"
    title="核对识别名称"
    subtitle="左边是这段录像的画面截图；右边改成真实摄像头名称，保存后立即按台账重新匹配"
    width="lg"
    height="auto"
    @close="emit('close')"
  >
    <div v-if="clip" class="rcn">
      <div class="rcn-shot-wrap">
        <img
          v-if="frameOk"
          class="rcn-shot"
          :src="frameUrl"
          :alt="'录像开头截图：' + clip.file_name"
          @error="frameOk = false"
        />
        <div v-else class="rcn-noshot">
          <AppIcon name="video" :size="22" />
          <strong>取不到这段录像的画面</strong>
          <span>文件可能已移动或编码无法解码；仍可填写名称，保存后按台账匹配。</span>
        </div>
        <span class="rcn-cap">画面截图（自动跳过花屏帧）</span>
      </div>

      <div class="rcn-form">
        <label class="rcn-field">
          <span>文件名</span>
          <strong class="mono">{{ clip.file_name }}</strong>
        </label>
        <label class="rcn-field">
          <span>所在目录</span>
          <strong class="mono rcn-dir" :title="clip.relpath">{{ clip.relpath }}</strong>
        </label>
        <label class="rcn-field">
          <span>识别名称<span class="rcn-src">（{{ sourceText }}）</span></span>
          <input
            v-model="name"
            class="rcn-input"
            spellcheck="false"
            placeholder="例如 3-DX-中仁路孔明园E40栋-向北"
            @keyup.enter="submit"
          />
        </label>

        <p v-if="preview.kind === 'exact'" class="rcn-match rcn-match--ok">
          <AppIcon name="check" :size="12" />
          <span>将绑定摄像头：<strong>{{ preview.list[0].display_name }}</strong></span>
        </p>
        <p v-else-if="preview.kind === 'multi'" class="rcn-match rcn-match--warn">
          <span>台账里有 {{ preview.list.length }} 台同名摄像头，保存后记为待确认：{{ multiNames }}</span>
        </p>
        <div v-else-if="preview.kind === 'suggest'" class="rcn-suggest">
          <span class="rcn-suggest-title">台账里相近的摄像头（点一下套用名称）</span>
          <span class="rcn-chips">
            <button v-for="camera in preview.list" :key="camera.camera_uuid" type="button" class="rcn-chip" @click="pick(camera)">
              {{ camera.display_name }}
            </button>
          </span>
        </div>
        <p v-else-if="preview.kind === 'none'" class="rcn-match rcn-match--warn">
          <span>台账里没有相近的摄像头，保存后记为未匹配，可用「重新匹配摄像头」再试。</span>
        </p>

        <label class="rcn-field">
          <span>核对备注（可选）</span>
          <input v-model="note" class="rcn-input" placeholder="例如 与文件名一致，画面角标可辨" />
        </label>
        <p v-if="catalogNote" class="rcn-hint">{{ catalogNote }}</p>
      </div>
    </div>

    <template #footer>
      <span class="rcn-foot">只改扫描结果，不动录像文件与文件名。</span>
      <button class="btn" type="button" @click="emit('close')">取消</button>
      <button class="btn btn--primary" type="button" @click="submit">
        <AppIcon name="check" :size="12" />保存并匹配
      </button>
    </template>
  </BaseModal>
</template>

<style scoped>
.rcn {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  padding: 16px 18px;
  overflow-y: auto;
}

.rcn-shot-wrap {
  position: relative;
  display: grid;
  place-items: center;
  aspect-ratio: 16 / 10;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: var(--radius);
  background: #04070b;
}

.rcn-shot {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.rcn-noshot {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 0 28px;
  color: var(--text-3);
  text-align: center;
}

.rcn-noshot strong {
  font-size: var(--fs-strong);
  color: var(--text-1);
}

.rcn-noshot span {
  font-size: var(--fs-note);
  line-height: 1.6;
  color: var(--text-4);
}

.rcn-cap {
  position: absolute;
  left: 8px;
  bottom: 8px;
  padding: 2px 7px;
  border-radius: 6px;
  background: rgba(4, 7, 11, 0.72);
  font-size: var(--fs-note);
  color: var(--text-2);
}

.rcn-form {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.rcn-field {
  display: grid;
  gap: 4px;
  font-size: var(--fs-note);
  color: var(--text-3);
}

.rcn-field strong {
  font-size: var(--fs-strong);
  color: var(--text-0);
  overflow-wrap: anywhere;
}

.rcn-dir {
  color: var(--text-2) !important;
  font-weight: 400;
}

.rcn-src {
  color: var(--text-4);
}

.rcn-input {
  width: 100%;
  padding: 7px 9px;
  border: 1px solid var(--line-2);
  border-radius: 7px;
  background: var(--bg-1);
  color: var(--text-0);
  font-size: var(--fs-strong);
}

.rcn-input:focus {
  outline: none;
  border-color: var(--accent);
}

.rcn-match {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 9px;
  border-radius: 7px;
  font-size: var(--fs-note);
  line-height: 1.5;
}

.rcn-match--ok {
  border: 1px solid var(--ok-line, #2c6b3f);
  background: var(--ok-soft, rgba(44, 107, 63, 0.14));
  color: var(--text-1);
}

.rcn-match--warn {
  border: 1px solid var(--line-2);
  background: var(--bg-1);
  color: var(--text-2);
}

.rcn-suggest {
  display: grid;
  gap: 5px;
}

.rcn-suggest-title {
  font-size: var(--fs-note);
  color: var(--text-4);
}

.rcn-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.rcn-chip {
  padding: 3px 8px;
  border: 1px solid var(--line-2);
  border-radius: 999px;
  background: transparent;
  color: var(--text-1);
  font-size: var(--fs-note);
  cursor: pointer;
}

.rcn-chip:hover {
  border-color: var(--accent);
}

.rcn-hint {
  margin: 0;
  font-size: var(--fs-note);
  color: var(--text-4);
}

.rcn-foot {
  margin-right: auto;
  font-size: var(--fs-note);
  color: var(--text-4);
}

@media (max-width: 900px) {
  .rcn {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
