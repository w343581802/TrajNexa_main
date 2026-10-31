<script setup lang="ts">
import { ref } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import MockFrame from "../ui/MockFrame.vue";
import SideDrawer from "../ui/SideDrawer.vue";
import StatusChip from "../ui/StatusChip.vue";
import { actions, featureStats, selectedFeature, toast, ui, wb } from "../../store/workbench";

const reason = ref("");

const toneOf = (status: string) =>
  status === "verified" ? "ok" : status === "review" ? "warn" : status === "conflict" ? "danger" : status === "proposed" ? "info" : "muted";

const evidence = [
  { time: "20:12:08", camera: "CAM-01 东门入口 01", quality: "夜间 · 清晰" },
  { time: "20:19:02", camera: "CAM-03 南侧道路 03", quality: "夜间 · 部分遮挡" },
  { time: "20:33:12", camera: "CAM-05 地铁 A 口 05", quality: "低照度 · 灰度" },
];

const history = [
  { actor: "系统", action: "提取特征候选", time: "20:12:12", note: "模型 v0.9 · 上限为候选" },
  { actor: "李调查员", action: "确认「深色长款外套」", time: "20:18:40", note: "核对原始裁剪" },
  { actor: "系统", action: "新增冲突候选「浅灰色外套」", time: "20:33:16", note: "来源：夜间灰度画面" },
  { actor: "李调查员", action: "保留原值，冲突待复核", time: "20:35:02", note: "颜色证据不可靠" },
];

function resolve(action: "confirm" | "unknown" | "reject" | "revoke") {
  actions.resolveFeature(action);
  if (action === "confirm" || action === "unknown" || action === "reject") reason.value = "";
}

function jumpToObservation() {
  ui.featureDetailOpen = false;
  wb.timelineFilter = "review";
  toast("已跳转到特征来源观测的待复核列表", "info");
}
</script>

<template>
  <SideDrawer
    :open="ui.featureDetailOpen"
    :title="`特征详情 · ${selectedFeature?.attr ?? '目标特征'}`"
    subtitle="目标 T01 · 特征档案 v7 · 特征不构成身份结论"
    width="472px"
    @close="ui.featureDetailOpen = false"
  >
    <template #head-extra>
      <StatusChip v-if="selectedFeature" :label="selectedFeature.statusLabel" :tone="toneOf(selectedFeature.status)" dot />
    </template>

    <template v-if="selectedFeature">
      <section class="block">
        <h3 class="block-title">来源原始画面</h3>
        <div class="evidence">
          <figure v-for="item in evidence" :key="item.time" class="evidence-item">
            <div class="evidence-frame">
              <MockFrame :time="`2026-10-25 ${item.time}`" :label="item.camera" shape="wide" :chrome="false" :tone="item.quality.includes('灰度') ? 'gray' : 'color'" />
            </div>
            <figcaption>
              <span class="num">{{ item.time }}</span>
              <span class="dim">{{ item.camera }}</span>
              <span class="dim">{{ item.quality }}</span>
            </figcaption>
          </figure>
        </div>
        <p class="hint">仅展示原始关键帧；禁止用模型增强图替代原件。共 {{ featureStats.refs }} 条来源观测。</p>
      </section>

      <section class="block">
        <h3 class="block-title">描述与人工修正</h3>
        <div class="compare">
          <div class="compare-cell">
            <span class="compare-label">系统提议</span>
            <span class="compare-value compare-value--proposed">{{ selectedFeature.proposed ?? selectedFeature.value }}</span>
            <span class="dim">模型 v0.9 · 未采信为结论</span>
          </div>
          <div class="compare-cell compare-cell--human">
            <span class="compare-label">当前有效值</span>
            <span class="compare-value">{{ selectedFeature.value }}</span>
            <span class="dim">人工核验 · 20:18</span>
          </div>
        </div>
        <dl class="facts">
          <div><dt>可见性</dt><dd>上身清晰，下装被遮挡</dd></div>
          <div><dt>适用时间</dt><dd class="num">{{ selectedFeature.window }}</dd></div>
          <div><dt>来源质量</dt><dd>{{ selectedFeature.meta }}</dd></div>
          <div><dt>审核状态</dt><dd>{{ selectedFeature.statusLabel }}</dd></div>
        </dl>
      </section>

      <section v-if="selectedFeature.conflict" class="block block--conflict">
        <h3 class="block-title">
          <AppIcon name="warning" :size="13" />冲突特征（两值并列保留）
        </h3>
        <div class="conflict-row">
          <div><span class="compare-label">当前有效</span><strong>{{ selectedFeature.value }}</strong></div>
          <span class="conflict-vs">vs</span>
          <div><span class="compare-label">冲突候选</span><strong class="conflict-alt">{{ selectedFeature.conflict.value }}</strong></div>
        </div>
        <p class="hint">{{ selectedFeature.conflict.reason }}。系统不会用高分候选自动覆盖已核验值；需人工判断或继续补充证据。</p>
      </section>

      <section class="block">
        <h3 class="block-title">审核与操作历史</h3>
        <ol class="history">
          <li v-for="item in history" :key="item.time + item.action">
            <span class="history-dot" />
            <span class="history-main">
              <span class="history-action">{{ item.action }}</span>
              <span class="history-meta"><b>{{ item.actor }}</b> · <span class="num">{{ item.time }}</span> · {{ item.note }}</span>
            </span>
          </li>
        </ol>
        <p class="hint">撤销审核会恢复上一版特征视图，并提示受影响的推断；历史不被静默删除。</p>
      </section>

      <section class="block">
        <h3 class="block-title">审核理由（关键修正需记录）</h3>
        <textarea v-model="reason" class="textarea" rows="3" placeholder="例如：核对原始裁剪，外套颜色与夜间灰度画面冲突，保留原值" />
        <p class="hint">理由随审核记录留档；操作人：李调查员（可审核）。</p>
      </section>
    </template>

    <template #footer>
      <button class="btn btn--sm btn--ghost" @click="jumpToObservation">
        <AppIcon name="link" :size="12" />查看来源观测
      </button>
      <span class="grow" />
      <button class="btn btn--sm btn--ghost" @click="resolve('revoke')">撤销核验</button>
      <button class="btn btn--sm btn--outline" @click="resolve('unknown')">标为未知</button>
      <button class="btn btn--sm btn--outline" @click="resolve('reject')">驳回提议</button>
      <button class="btn btn--sm btn--primary" @click="resolve('confirm')">确认该特征</button>
    </template>
  </SideDrawer>
</template>

<style scoped>
.block {
  display: grid;
  gap: 7px;
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--line);
}

.block:last-child {
  border-bottom: 0;
  margin-bottom: 0;
}

.block-title {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-1);
  letter-spacing: 0.02em;
}

.block--conflict {
  padding: 9px;
  border: 1px solid rgba(240, 180, 90, 0.28);
  border-radius: var(--radius-sm);
  background: rgba(240, 180, 90, 0.05);
}

.block--conflict .block-title {
  color: #f3c47c;
}

.evidence {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.evidence-item {
  margin: 0;
  display: grid;
  gap: 3px;
}

.evidence-frame {
  aspect-ratio: 4 / 3;
  border-radius: 6px;
  overflow: hidden;
  border: 1px solid var(--line-2);
  background: #060a0f;
}

.evidence-item figcaption {
  display: grid;
  font-size: 8.5px;
  line-height: 1.35;
}

.evidence-item figcaption .num {
  color: var(--text-1);
}

.evidence-item figcaption .dim {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hint {
  font-size: 9.5px;
  line-height: 1.5;
  color: var(--text-3);
}

.compare {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.compare-cell {
  display: grid;
  gap: 2px;
  padding: 7px 9px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--bg-2);
}

.compare-cell--human {
  border-color: rgba(47, 207, 146, 0.28);
  background: rgba(47, 207, 146, 0.05);
}

.compare-label {
  font-size: 8.5px;
  letter-spacing: 0.06em;
  color: var(--text-3);
}

.compare-value {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-0);
}

.compare-value--proposed {
  color: var(--text-2);
  font-weight: 500;
}

.compare-cell .dim {
  font-size: 8.5px;
}

.facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 14px;
}

.facts > div {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 10px;
  border-bottom: 1px dashed var(--line);
  padding-bottom: 3px;
}

.facts dt {
  color: var(--text-3);
  white-space: nowrap;
}

.facts dd {
  color: var(--text-1);
  text-align: right;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conflict-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 10px;
}

.conflict-row > div {
  display: grid;
  gap: 1px;
  flex: 1;
}

.conflict-row strong {
  font-size: 11.5px;
  color: var(--text-0);
  font-weight: 600;
}

.conflict-vs {
  font-size: 9px;
  color: var(--text-4);
}

.conflict-alt {
  color: #f3c47c !important;
}

.history {
  display: grid;
  gap: 8px;
}

.history li {
  display: flex;
  gap: 8px;
}

.history-dot {
  width: 6px;
  height: 6px;
  margin-top: 4px;
  border-radius: 50%;
  background: var(--line-3);
  flex: none;
}

.history-main {
  display: grid;
  gap: 1px;
}

.history-action {
  font-size: 10.5px;
  color: var(--text-1);
}

.history-meta {
  font-size: 9px;
  color: var(--text-3);
}

.history-meta b {
  color: var(--text-2);
  font-weight: 500;
}

.grow {
  flex: 1;
}
</style>
