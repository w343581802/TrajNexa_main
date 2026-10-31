<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import { actions, reviewClipCard, ui } from "../../store/workbench";

const note = ref("");

/** 每次打开换一段就把备注重置成这一段上次写的 */
watch(
  () => ui.reviewClipJobId,
  () => {
    note.value = reviewClipCard.value?.job.reviewNote ?? "";
  },
);

const card = computed(() => reviewClipCard.value);
const job = computed(() => card.value?.job ?? null);
const clip = computed(() => card.value?.clip ?? null);
const open = computed(() => ui.reviewClipOpen && Boolean(card.value));
/** 需要审核的卡片打开的是审核流程；普通卡片打开只是看这一段 */
const isReview = computed(() => card.value?.review ?? false);

const heading = computed(() => card.value?.cameraName ?? "未登记镜头");
const title = computed(() => `${isReview.value ? "人工审核" : "分析片段"} · ${heading.value}`);
const subtitle = computed(() =>
  isReview.value
    ? "模型把这一段标成需要人工确认的片段：确认后进入候选结果，排除后不再计入，录像文件保持不动。"
    : "这一段正在分析或已经结束；缩略帧与片段播放接口接入后可直接回看。",
);

const matchText = computed(() => {
  switch (clip.value?.match_status) {
    case "matched":
      return "已匹配台账";
    case "ambiguous":
      return "同名多台，需人工确认";
    case "no_name":
      return "未识别到名称";
    case "ocr_disabled":
      return "未开启名称识别";
    default:
      return clip.value ? "未匹配" : "无扫描结果";
  }
});

const suggestions = computed(() => (clip.value?.suggestions ?? []).filter((item) => !item.reviewed).slice(0, 3));

function decide(verdict: "accept" | "exclude") {
  if (!job.value) return;
  actions.reviewJob(job.value.id, verdict, note.value);
}
</script>

<template>
  <BaseModal
    :open="open"
    width="lg"
    :title="title"
    :subtitle="subtitle"
    @close="actions.closeReviewClip()"
  >
    <div v-if="card && job" class="rv-body">
      <div class="rv-media">
        <img
          v-if="ui.dockThumbMode === 'image' && card.thumbUrl"
          class="rv-img"
          :src="card.thumbUrl"
          :alt="`${heading} 待审核缩略帧`"
        />
        <video v-else-if="ui.dockThumbMode === 'video' && card.clipUrl" class="rv-img" :src="card.clipUrl" controls playsinline />
        <div v-else class="rv-placeholder">
          <AppIcon name="video" :size="24" />
          <strong>视频片段尚未接入</strong>
          <span>缩略帧与片段播放接口还没提供，这里先显示录像段信息；接入后此处直接播放。</span>
        </div>
      </div>

      <div class="rv-side">
        <dl class="kv rv-kv">
          <dt>分析时间</dt>
          <dd class="num">{{ card.timeText }}</dd>
          <dt>片段时长</dt>
          <dd class="num">{{ card.duration || "未知（扫描不推断结束时间）" }}</dd>
          <dt>录像文件</dt>
          <dd class="mono truncate" :title="job.file">{{ job.file }}</dd>
          <dt v-if="clip">所在目录</dt>
          <dd v-if="clip" class="mono truncate" :title="clip.relpath">{{ clip.relpath }}</dd>
          <dt>镜头匹配</dt>
          <dd>{{ matchText }}</dd>
          <dt>作业状态</dt>
          <dd>{{ card.statusText }}</dd>
          <dt>候选数</dt>
          <dd class="num">{{ job.candidates }}</dd>
        </dl>

        <div v-if="suggestions.length" class="rv-hints">
          <span class="rv-hints-title">台账里的相近镜头</span>
          <span v-for="item in suggestions" :key="item.camera_uuid" class="rv-hint">
            {{ item.display_name }}<span class="dim num">{{ Math.round(item.score * 100) }}%</span>
          </span>
        </div>

        <label v-if="isReview" class="field rv-note">
          <span class="field-label">审核备注</span>
          <textarea v-model="note" class="textarea" rows="2" placeholder="例如：画面角标可辨、人物特征与目标一致" />
        </label>
      </div>
    </div>

    <template #footer>
      <span class="rv-foot-hint">
        {{ isReview ? "结论先记在本轮审核记录里；后端审核接口接入后由这里回写。" : "这一段不需要审核，所以没有确认/排除按钮。" }}
      </span>
      <button class="btn btn--ghost" @click="actions.closeReviewClip()">{{ isReview ? "稍后再说" : "关闭" }}</button>
      <template v-if="isReview">
        <button class="btn btn--outline" @click="decide('exclude')">
          <AppIcon name="close" :size="12" />排除
        </button>
        <button class="btn btn--primary" @click="decide('accept')">
          <AppIcon name="check" :size="12" />确认候选
        </button>
      </template>
    </template>
  </BaseModal>
</template>

<style scoped>
.rv-body {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  padding: 14px 16px;
  min-height: 0;
  overflow-y: auto;
}

.rv-media {
  position: relative;
  display: grid;
  place-items: center;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border: 1px solid var(--line-2);
  border-radius: var(--radius);
  background: var(--bg-0);
}

.rv-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #04070b;
}

.rv-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 0 28px;
  color: var(--text-3);
  text-align: center;
}

.rv-placeholder strong {
  font-size: var(--fs-strong);
  color: var(--text-1);
}

.rv-placeholder span {
  font-size: var(--fs-note);
  line-height: 1.6;
  color: var(--text-4);
}

.rv-side {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.rv-kv {
  display: grid;
  grid-template-columns: 68px minmax(0, 1fr);
  align-items: baseline;
  gap: 5px 10px;
}

.rv-kv dd {
  text-align: left;
}

.rv-hints {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.rv-hints-title {
  font-size: var(--fs-note);
  color: var(--text-3);
}

.rv-hint {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  border: 1px solid var(--line-2);
  border-radius: 999px;
  font-size: var(--fs-note);
  color: var(--text-1);
}

.rv-note {
  gap: 4px;
}

.rv-foot-hint {
  margin-right: auto;
  font-size: var(--fs-note);
  color: var(--text-4);
}
</style>