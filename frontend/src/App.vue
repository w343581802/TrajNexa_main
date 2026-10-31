<script setup lang="ts">
import { onMounted, ref } from "vue";
import AppHeader from "./components/AppHeader.vue";
import BottomDock from "./components/BottomDock.vue";
import ObservationTimeline from "./components/ObservationTimeline.vue";
import TargetFeatureBar from "./components/TargetFeatureBar.vue";
import WorkspaceMap from "./components/WorkspaceMap.vue";
import SpatialDataPreview from "./components/SpatialDataPreview.vue";
import LiveGisMap from "./components/LiveGisMap.vue";
import AddElementModal from "./components/modals/AddElementModal.vue";
import CaseManageModal from "./components/modals/CaseManageModal.vue";
import CorrectionDialog from "./components/modals/CorrectionDialog.vue";
import CoverageModal from "./components/modals/CoverageModal.vue";
import ElementsDrawer from "./components/modals/ElementsDrawer.vue";
import FeatureDetailSheet from "./components/modals/FeatureDetailSheet.vue";
import RecordingDirModal from "./components/modals/RecordingDirModal.vue";
import VideoNamingModal from "./components/modals/VideoNamingModal.vue";
import ClipReviewModal from "./components/modals/ClipReviewModal.vue";
import ToastStack from "./components/ui/ToastStack.vue";
import { applyDemoHash, loadCases } from "./store/workbench";

const mapView = ref<"demo" | "preview" | "gis">("demo");
onMounted(() => {
  applyDemoHash();
  // 首次进入读取案件；有案件时默认选中最近创建的一个，保持原来的工作台视图
  void loadCases(true);
});
</script>

<template>
  <div class="app">
    <AppHeader />

    <main class="app-main">
      <div class="stage">
        <div class="map-slot">
          <LiveGisMap v-if="mapView === 'gis'" />
          <SpatialDataPreview v-else-if="mapView === 'preview'" />
          <WorkspaceMap v-else />
        </div>
        <div class="m2b-toggle">
          <div class="map-modes" role="group" aria-label="地图视图">
            <button
              type="button"
              :aria-pressed="mapView === 'demo'"
              :class="{ 'is-on': mapView === 'demo' }"
              @click="mapView = 'demo'"
            >业务示意</button>
            <button
              type="button"
              :aria-pressed="mapView === 'preview'"
              :class="{ 'is-on': mapView === 'preview' }"
              @click="mapView = 'preview'"
            >空间原始预览</button>
            <button
              type="button"
              :aria-pressed="mapView === 'gis'"
              :class="{ 'is-on': mapView === 'gis' }"
              @click="mapView = 'gis'"
            >真实 GIS 地图</button>
          </div>
        </div>
        <TargetFeatureBar />
      </div>
      <ObservationTimeline />
    </main>

    <BottomDock />

    <CoverageModal />
    <AddElementModal />
    <RecordingDirModal />
    <VideoNamingModal />
    <ClipReviewModal />
    <!-- 放在添加要素之后：从添加要素弹窗里打开案件管理时叠在它上面 -->
    <CaseManageModal />
    <FeatureDetailSheet />
    <ElementsDrawer />
    <CorrectionDialog />
    <ToastStack />
  </div>
</template>

<style scoped>
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: var(--bg-0);
}

.app-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) var(--rail-w);
  flex: 1;
  min-height: 0;
  min-width: 0;
}

.stage {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) auto;
  min-height: 0;
  min-width: 0;
}

/* 地图单独占第一行，控件挂在地图内，避免压到下面的目标特征栏 */
.map-slot {
  position: relative;
  display: flex;
  grid-row: 1;
  grid-column: 1;
  min-height: 0;
  min-width: 0;
}
.map-slot > * {
  flex: 1;
  min-height: 0;
  min-width: 0;
}
.feature-bar {
  grid-row: 2;
  grid-column: 1;
}

.m2b-toggle {
  grid-row: 1;
  grid-column: 1;
  z-index: 30;
  align-self: end;
  justify-self: end;
  /* 让开底图署名、比例尺与状态行 */
  margin: 0 12px 46px 0;
  pointer-events: none;
}
.map-modes {
  display: flex;
  gap: 1px;
  overflow: hidden;
  border: 1px solid #437196;
  border-radius: 6px;
  background: #0f2536;
  box-shadow: 0 6px 18px rgba(4, 10, 18, 0.45);
  pointer-events: auto;
}
.map-modes button {
  color: #d4eaff;
  background: transparent;
  border: 0;
  padding: 7px 10px;
  cursor: pointer;
  font-size: 11px;
}
.map-modes button.is-on {
  background: #1d4a68;
  color: #ecf6ff;
}
@media (max-width: 1100px) {
  .app-main {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
}
</style>
