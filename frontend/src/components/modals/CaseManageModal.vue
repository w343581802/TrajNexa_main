<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import StatusChip from "../ui/StatusChip.vue";
import {
  actions,
  caseError,
  caseList,
  caseLoading,
  caseWorking,
  currentCase,
  deleteCase,
  createCase,
  loadCases,
  toast,
  ui,
  wb,
} from "../../store/workbench";
import type { CaseItem } from "../../services/mainApi";

const NAME_MIN = 2;
const NAME_MAX = 120;
const DESC_MAX = 500;

const form = reactive({ name: "", description: "" });
const formError = ref<string | null>(null);
const keyword = ref("");
/** 删除是两步确认：先点删除，再在行内确认，避免误删 */
const confirmId = ref<string | null>(null);

const filtered = computed(() => {
  const text = keyword.value.trim().toLowerCase();
  if (!text) return caseList.value;
  return caseList.value.filter(
    (item) =>
      item.name.toLowerCase().includes(text) ||
      item.code.toLowerCase().includes(text) ||
      item.description.toLowerCase().includes(text),
  );
});

const totalFiles = computed(() => caseList.value.reduce((sum, item) => sum + item.media_count, 0));
const totalBytes = computed(() => caseList.value.reduce((sum, item) => sum + item.media_bytes, 0));

watch(
  () => ui.caseManageOpen,
  (open) => {
    if (!open) {
      confirmId.value = null;
      formError.value = null;
    }
  },
);

const pad = (value: number) => String(value).padStart(2, "0");

function formatBytes(size: number) {
  if (!size) return "0 B";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function formatDate(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function previewCode() {
  const today = new Date();
  const prefix = `CASE-${today.getFullYear()}${pad(today.getMonth() + 1)}${pad(today.getDate())}-`;
  const used = caseList.value.filter((item) => item.code.startsWith(prefix)).length;
  return `${prefix}${pad(used + 1)}`;
}

async function submit() {
  const name = form.name.trim();
  if (name.length < NAME_MIN) {
    formError.value = `案件名称至少 ${NAME_MIN} 个字符`;
    return;
  }
  if (name.length > NAME_MAX) {
    formError.value = `案件名称最多 ${NAME_MAX} 个字符`;
    return;
  }
  if (form.description.length > DESC_MAX) {
    formError.value = `案件说明最多 ${DESC_MAX} 个字符`;
    return;
  }
  formError.value = null;
  const created = await createCase(name, form.description);
  if (!created) return;
  form.name = "";
  form.description = "";
  confirmId.value = null;
}

function choose(item: CaseItem, close = true) {
  if (item.id !== wb.caseId) actions.selectCase(item.id);
  if (close) ui.caseManageOpen = false;
}

async function confirmDelete(item: CaseItem) {
  const result = await deleteCase(item.id);
  confirmId.value = null;
  if (result && !caseList.value.length) toast("已没有案件，工作台回到示例任务", "warn");
}
</script>

<template>
  <BaseModal
    :open="ui.caseManageOpen"
    title="案件管理"
    subtitle="案件是研判工作的入口：选择、新建或删除案件。删除只清理该案件上传的要素文件，录像资源保持不动。"
    width="lg"
    height="84vh"
    @close="ui.caseManageOpen = false"
  >
    <div class="cm">
      <!-- 左：案件列表 -->
      <section class="cm-list">
        <div class="cm-bar">
          <div class="cm-bar-title">
            <span class="cm-title">案件列表</span>
            <span class="cm-count num">{{ caseList.length }}</span>
            <span class="cm-sum dim">
              要素文件 <b class="num">{{ totalFiles }}</b> 个 · {{ formatBytes(totalBytes) }}
            </span>
          </div>
          <label class="cm-search">
            <AppIcon name="search" :size="12" />
            <input v-model="keyword" type="text" placeholder="搜索名称 / 编号" aria-label="搜索案件" />
          </label>
          <button class="btn btn--sm btn--ghost" :disabled="caseLoading" @click="loadCases()">
            <AppIcon name="refresh" :size="12" />刷新
          </button>
        </div>

        <p v-if="caseError" class="cm-error"><AppIcon name="warning" :size="13" />{{ caseError }}</p>

        <div v-if="caseLoading && !caseList.length" class="cm-loading">正在读取案件…</div>

        <div v-else-if="!caseList.length" class="cm-empty">
          <span class="cm-empty-icon"><AppIcon name="folder" :size="20" /></span>
          <p>还没有案件</p>
          <span>在右侧填写名称即可新建第一个案件；上传的要素文件会归属到选中的案件。</span>
        </div>

        <div v-else-if="!filtered.length" class="cm-loading">没有匹配「{{ keyword }}」的案件</div>

        <ul v-else class="cm-rows scroll-y">
          <li v-for="item in filtered" :key="item.id" class="cm-row" :class="{ 'is-current': item.id === wb.caseId }">
            <div class="cm-row-main">
              <button class="cm-pick" :aria-current="item.id === wb.caseId" @click="choose(item)">
                <span class="cm-dot" aria-hidden="true" />
                <span class="cm-info">
                  <span class="cm-name truncate">{{ item.name }}</span>
                  <span class="cm-sub">
                    <span class="mono cm-code">{{ item.code }}</span>
                    <span class="cm-desc truncate">{{ item.description || "未填写案件说明" }}</span>
                  </span>
                </span>
                <span class="cm-meta">
                  <span class="cm-meta-line">要素文件 <b class="num">{{ item.media_count }}</b> 个</span>
                  <span class="cm-meta-line">占用 <b class="num">{{ formatBytes(item.media_bytes) }}</b></span>
                  <span class="cm-meta-line dim">创建 {{ formatDate(item.created_at) }}</span>
                </span>
              </button>

              <div class="cm-actions">
                <StatusChip v-if="item.id === wb.caseId" label="当前案件" tone="ok" dot />
                <button v-else class="btn btn--sm btn--outline" :disabled="caseWorking" @click="choose(item)">
                  设为当前
                </button>
                <button
                  class="btn btn--sm btn--danger"
                  :disabled="caseWorking"
                  :aria-label="`删除案件 ${item.name}`"
                  @click="confirmId = confirmId === item.id ? null : item.id"
                >
                  <AppIcon name="trash" :size="11" />删除
                </button>
              </div>
            </div>

            <div v-if="confirmId === item.id" class="cm-confirm">
              <div class="cm-confirm-text">
                <span class="cm-confirm-title"><AppIcon name="warning" :size="13" />确认删除案件「{{ item.name }}」？</span>
                <span v-if="item.media_count">
                  将清理该案件上传的 {{ item.media_count }} 个要素文件（{{ formatBytes(item.media_bytes) }}）及本系统内的识别记录；
                  <b>录像资源中的视频不会被删除</b>。此操作不可撤销。
                </span>
                <span v-else>
                  该案件没有上传的要素文件，只会删除案件本身；<b>录像资源中的视频不会被删除</b>。此操作不可撤销。
                </span>
              </div>
              <div class="cm-confirm-actions">
                <button class="btn btn--sm btn--ghost" :disabled="caseWorking" @click="confirmId = null">取消</button>
                <button class="btn btn--sm btn--danger" :disabled="caseWorking" @click="confirmDelete(item)">
                  {{ caseWorking ? "删除中…" : "确认删除" }}
                </button>
              </div>
            </div>
          </li>
        </ul>
      </section>

      <!-- 右：新建案件 -->
      <aside class="cm-form scroll-y">
        <div class="cm-form-head">
          <span class="cm-title">新建案件</span>
          <span class="cm-form-hint">自动编号 <span class="mono">{{ previewCode() }}</span></span>
        </div>

        <label class="field">
          <span class="field-label">案件名称 <b class="req">*</b></span>
          <input
            v-model="form.name"
            class="input"
            type="text"
            :maxlength="NAME_MAX"
            placeholder="例如：城中村西片 · 夜间轨迹研判"
            @keyup.enter="submit"
          />
        </label>

        <label class="field">
          <span class="field-label">案件说明（选填）</span>
          <textarea
            v-model="form.description"
            class="textarea"
            rows="3"
            :maxlength="DESC_MAX"
            placeholder="研判范围、案情摘要或授权说明"
          />
          <span class="field-hint">{{ form.description.length }} / {{ DESC_MAX }}</span>
        </label>

        <p v-if="formError" class="cm-error"><AppIcon name="warning" :size="13" />{{ formError }}</p>

        <button class="btn btn--primary" :disabled="caseWorking || form.name.trim().length < NAME_MIN" @click="submit">
          <AppIcon name="plus" :size="13" />{{ caseWorking ? "处理中…" : "新建案件" }}
        </button>

        <div class="cm-boundary">
          <span class="cm-boundary-title"><AppIcon name="shield" :size="13" />删除边界</span>
          <ul>
            <li><b>清理</b>：该案件上传的要素文件（原始画面 / 片段）及本系统内的识别与审核记录。</li>
            <li><b>保留</b>：录像资源中的录像文件与索引属于独立服务，删除案件不会删除任何录像。</li>
            <li><b>不涉及</b>：未归属案件的历史素材、TrajNexa_Map 的空间数据都不受影响。</li>
          </ul>
        </div>

        <p class="cm-form-foot">
          当前：<b>{{ currentCase?.name ?? "未选择案件" }}</b>
          <span v-if="currentCase" class="mono dim"> · {{ currentCase.code }}</span>
        </p>
      </aside>
    </div>

    <template #footer>
      <p class="cm-foot-note">切换案件会同时切换该案件的工作区；目标要素按案件在本地隔离，不会串到其它案件。</p>
      <span class="grow" />
      <button class="btn btn--ghost" @click="ui.caseManageOpen = false">关闭</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.cm {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 348px;
  min-height: 0;
  flex: 1;
}

/* -------- 左：列表 -------- */

.cm-list {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 10px 12px 0;
  border-right: 1px solid var(--line);
}

.cm-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
  padding-bottom: 8px;
}

.cm-bar-title {
  display: flex;
  align-items: baseline;
  gap: 7px;
  min-width: 0;
}

.cm-title {
  font-size: 13px;
  font-weight: 620;
  color: var(--text-0);
  white-space: nowrap;
}

.cm-count {
  min-width: 18px;
  height: 17px;
  padding: 0 5px;
  border-radius: 99px;
  border: 1px solid var(--line-2);
  background: var(--bg-3);
  color: var(--text-2);
  font-size: 10px;
  text-align: center;
  line-height: 15px;
}

.cm-sum {
  font-size: 10.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cm-search {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 90px;
  max-width: 220px;
  height: 26px;
  margin-left: auto;
  padding: 0 8px;
  border: 1px solid var(--line-2);
  border-radius: 6px;
  background: var(--bg-2);
  color: var(--text-3);
}

.cm-search input {
  width: 100%;
  border: 0;
  background: none;
  color: var(--text-1);
  font-size: 11.5px;
  outline: none;
}

.cm-error {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  flex: none;
  margin-bottom: 6px;
  padding: 6px 9px;
  border: 1px solid rgba(242, 112, 106, 0.3);
  border-radius: 6px;
  background: var(--danger-soft);
  color: #ffb0ab;
  font-size: 11px;
  line-height: 1.5;
}

.cm-error :deep(svg) {
  flex: none;
  margin-top: 1px;
}

.cm-loading {
  padding: 26px 4px;
  font-size: 11.5px;
  color: var(--text-3);
  text-align: center;
}

.cm-empty {
  display: grid;
  justify-items: center;
  align-content: center;
  gap: 7px;
  flex: 1;
  min-height: 0;
  padding: 40px 20px;
  text-align: center;
}

.cm-empty-icon {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 11px;
  border: 1px dashed var(--line-3);
  color: var(--text-3);
}

.cm-empty p {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-1);
}

.cm-empty span {
  max-width: 40ch;
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-3);
}

.cm-rows {
  display: grid;
  align-content: start;
  gap: 7px;
  flex: 1;
  min-height: 0;
  padding-bottom: 12px;
}

.cm-row {
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg-2);
  overflow: hidden;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}

.cm-row:hover {
  border-color: var(--line-3);
}

.cm-row.is-current {
  border-color: var(--accent-line);
  background: linear-gradient(180deg, rgba(77, 141, 255, 0.07), var(--bg-2));
}

.cm-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px 8px 6px;
}

.cm-pick {
  display: flex;
  align-items: center;
  gap: 9px;
  flex: 1;
  min-width: 0;
  padding: 2px 4px;
  border-radius: 6px;
  text-align: left;
}

.cm-pick:hover {
  background: rgba(255, 255, 255, 0.025);
}

.cm-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1px solid var(--line-3);
  background: transparent;
}

.cm-row.is-current .cm-dot {
  border-color: var(--ok);
  background: var(--ok);
  box-shadow: 0 0 0 3px rgba(47, 207, 146, 0.14);
}

.cm-info {
  display: grid;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.cm-name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-0);
}

.cm-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.cm-code {
  flex: none;
  font-size: 10px;
  color: var(--text-3);
  letter-spacing: 0.02em;
}

.cm-desc {
  font-size: 10.5px;
  color: var(--text-3);
  min-width: 0;
}

.cm-meta {
  display: grid;
  gap: 1px;
  flex: none;
  text-align: right;
  padding-right: 2px;
}

.cm-meta-line {
  font-size: 10px;
  color: var(--text-2);
  white-space: nowrap;
}

.cm-meta-line b {
  color: var(--text-1);
  font-weight: 600;
}

.cm-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.cm-actions .btn {
  height: 22px;
  padding: 0 7px;
  font-size: 10px;
}

.cm-confirm {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-top: 1px dashed rgba(242, 112, 106, 0.34);
  background: rgba(242, 112, 106, 0.06);
}

.cm-confirm-text {
  display: grid;
  gap: 3px;
  flex: 1;
  min-width: 0;
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-2);
}

.cm-confirm-text b {
  color: #ffb0ab;
  font-weight: 600;
}

.cm-confirm-title {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 600;
  color: #ff9a95;
}

.cm-confirm-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.cm-confirm-actions .btn {
  height: 22px;
  padding: 0 8px;
  font-size: 10px;
}

/* -------- 右：新建 -------- */

.cm-form {
  display: flex;
  flex-direction: column;
  gap: 11px;
  min-height: 0;
  padding: 10px 14px 14px;
  background: rgba(255, 255, 255, 0.008);
}

.cm-form-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.cm-form-hint {
  font-size: 10px;
  color: var(--text-4);
}

.cm-form .input,
.cm-form .textarea {
  font-size: 12px;
}

.req {
  color: var(--danger);
  font-weight: 600;
}

.cm-form .btn--primary {
  height: 32px;
}

.cm-boundary {
  display: grid;
  gap: 5px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--bg-1);
}

.cm-boundary-title {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-1);
}

.cm-boundary-title :deep(svg) {
  color: var(--ok);
}

.cm-boundary ul {
  display: grid;
  gap: 3px;
  font-size: 10px;
  line-height: 1.55;
  color: var(--text-3);
}

.cm-boundary li::before {
  content: "· ";
  color: var(--text-4);
}

.cm-boundary b {
  color: var(--text-2);
  font-weight: 600;
}

.cm-form-foot {
  margin-top: auto;
  font-size: 10.5px;
  color: var(--text-3);
}

.cm-foot-note {
  font-size: 10.5px;
  line-height: 1.5;
  color: var(--text-3);
}

.grow {
  flex: 1;
}

@media (max-width: 1100px) {
  .cm {
    grid-template-columns: minmax(0, 1fr) 300px;
  }

  .cm-sum {
    display: none;
  }
}
</style>
