<script setup lang="ts">
import AppIcon from "./ui/AppIcon.vue";
</script>

<template>
  <section class="history-page">
    <header class="page-head">
      <div>
        <h1>历史记录</h1>
        <p>每轮研判结束后，轮次、时间窗、参与镜头与审核结论会归档在这里。</p>
      </div>
    </header>

    <div class="empty-card">
      <span class="empty-icon"><AppIcon name="clock" :size="22" /></span>
      <strong>暂无历史轮次</strong>
      <span>当前研判过程仍在「任务控制」中；本轮结束后会生成可追溯的轮次记录。</span>
    </div>
  </section>
</template>

<style scoped>
.history-page {
  display: flex;
  flex-direction: column;
  gap: 18px;
  height: 100%;
  min-height: 0;
  padding: 18px 20px 20px;
}

.page-head h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  color: var(--text-0);
}

.page-head p {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-3);
}

.empty-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  flex: 1;
  min-height: 0;
  padding: 32px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg-1);
  text-align: center;
  color: var(--text-3);
}

.empty-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  margin-bottom: 2px;
  border: 1px solid var(--line-2);
  border-radius: 50%;
  background: var(--bg-2);
  color: var(--text-3);
}

.empty-card strong {
  font-size: 14px;
  color: var(--text-1);
}

.empty-card span:last-child {
  max-width: 460px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-4);
}
</style>