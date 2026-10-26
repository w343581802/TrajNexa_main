<script setup lang="ts">
import { onMounted } from "vue";
import AppHeader from "./components/AppHeader.vue";
import BottomDock from "./components/BottomDock.vue";
import ObservationTimeline from "./components/ObservationTimeline.vue";
import TargetFeatureBar from "./components/TargetFeatureBar.vue";
import WorkspaceMap from "./components/WorkspaceMap.vue";
import AddElementModal from "./components/modals/AddElementModal.vue";
import CorrectionDialog from "./components/modals/CorrectionDialog.vue";
import CoverageModal from "./components/modals/CoverageModal.vue";
import ElementsDrawer from "./components/modals/ElementsDrawer.vue";
import FeatureDetailSheet from "./components/modals/FeatureDetailSheet.vue";
import ToastStack from "./components/ui/ToastStack.vue";
import { applyDemoHash } from "./store/workbench";

onMounted(applyDemoHash);
</script>

<template>
  <div class="app">
    <AppHeader />

    <main class="app-main">
      <div class="stage">
        <WorkspaceMap />
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
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  min-height: 0;
  min-width: 0;
}

@media (max-width: 1100px) {
  .app-main {
    grid-template-columns: minmax(0, 1fr) 300px;
  }
}
</style>
