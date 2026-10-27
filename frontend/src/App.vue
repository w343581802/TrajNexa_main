<script setup lang="ts">
import { onMounted, ref } from "vue";
import AppHeader from "./components/AppHeader.vue";
import BottomDock from "./components/BottomDock.vue";
import ObservationTimeline from "./components/ObservationTimeline.vue";
import TargetFeatureBar from "./components/TargetFeatureBar.vue";
import WorkspaceMap from "./components/WorkspaceMap.vue";
import SpatialDataPreview from "./components/SpatialDataPreview.vue";
import MapGisView from "./components/MapGisView.vue";
import AddElementModal from "./components/modals/AddElementModal.vue";
import CorrectionDialog from "./components/modals/CorrectionDialog.vue";
import CoverageModal from "./components/modals/CoverageModal.vue";
import ElementsDrawer from "./components/modals/ElementsDrawer.vue";
import FeatureDetailSheet from "./components/modals/FeatureDetailSheet.vue";
import ToastStack from "./components/ui/ToastStack.vue";
import { applyDemoHash } from "./store/workbench";

const mapMode = ref<"demo" | "spatial" | "gis">("demo");
onMounted(applyDemoHash);
</script>

<template>
  <div class="app">
    <AppHeader />

    <main class="app-main">
      <div class="stage">
        <div class="m2b-toggle">
          <div class="map-modes" role="group" aria-label="地图视图">
            <button type="button" :class="{ 'is-on': mapMode === 'demo' }" @click="mapMode = 'demo'">示意地图</button>
            <button type="button" :class="{ 'is-on': mapMode === 'spatial' }" @click="mapMode = 'spatial'">空间数据</button>
            <button type="button" :class="{ 'is-on': mapMode === 'gis' }" @click="mapMode = 'gis'">Map GIS</button>
          </div>
        </div>
        <MapGisView v-if="mapMode === 'gis'" />
        <SpatialDataPreview v-else-if="mapMode === 'spatial'" />
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
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr) auto;
  min-height: 0;
  min-width: 0;
}


.m2b-toggle {
  position: absolute;
  z-index: 30;
  bottom: 10px;
  right: 12px;
}
.map-modes {
  display: flex;
  gap: 1px;
  overflow: hidden;
  border: 1px solid #437196;
  border-radius: 6px;
  background: #0f2536;
  box-shadow: 0 6px 18px rgba(4, 10, 18, 0.45);
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
