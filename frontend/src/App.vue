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
import CorrectionDialog from "./components/modals/CorrectionDialog.vue";
import CoverageModal from "./components/modals/CoverageModal.vue";
import ElementsDrawer from "./components/modals/ElementsDrawer.vue";
import FeatureDetailSheet from "./components/modals/FeatureDetailSheet.vue";
import ToastStack from "./components/ui/ToastStack.vue";
import { applyDemoHash } from "./store/workbench";

const mapView = ref<"demo" | "preview" | "gis">("demo");
onMounted(applyDemoHash);
</script>

<template>
  <div class="app">
    <AppHeader />

    <main class="app-main">
      <div class="stage">
        <div class="m2b-toggle">
          <button type="button" :aria-pressed="mapView === 'demo'" @click="mapView = 'demo'">业务示意</button>
          <button type="button" :aria-pressed="mapView === 'gis'" @click="mapView = 'gis'">真实 GIS 地图</button>
          <button type="button" :aria-pressed="mapView === 'preview'" @click="mapView = 'preview'">空间原始预览</button>
        </div>
        <LiveGisMap v-if="mapView === 'gis'" />
        <SpatialDataPreview v-else-if="mapView === 'preview'" />
        <WorkspaceMap v-else />
        <TargetFeatureBar />
      </div>
      <ObservationTimeline />
    </main>

    <BottomDock />

    <CoverageModal />
    <AddElementModal />
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
  grid-template-rows: minmax(0, 1fr) auto;
  min-height: 0;
  min-width: 0;
}


.m2b-toggle {
  position: absolute;
  z-index: 30;
  bottom: 12px;
  left: 12px;
  right: auto;
  display: flex;
  gap: 4px;
}
.m2b-toggle button {
  color: #d4eaff;
  background: #123148;
  border: 1px solid #437196;
  border-radius: 6px;
  padding: 7px 10px;
  cursor: pointer;
  font-size: 11px;
}
.m2b-toggle button[aria-pressed="true"] {
  border-color: #6aa6ef;
  background: #1b4669;
}
@media (max-width: 1100px) {
  .app-main {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
}
</style>
