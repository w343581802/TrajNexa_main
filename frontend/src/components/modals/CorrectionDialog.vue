<script setup lang="ts">
import { onMounted, ref } from "vue";
import AppIcon from "../ui/AppIcon.vue";
import BaseModal from "../ui/BaseModal.vue";
import { cameraCode, cameraLabel, loadMapSnapshot, type SnapshotCamera } from "../../services/mapSnapshot";
import { toast, ui, wb } from "../../store/workbench";

const target = ref("fov");
const cameraId = ref(wb.selectedCameraId ?? "");
const description = ref("");
const basis = ref("实测画面与 Map 发布视域明显不一致");
const cameras = ref<SnapshotCamera[]>([]);

onMounted(async () => {
  try {
    cameras.value = (await loadMapSnapshot()).cameras;
  } catch {
    cameras.value = [];
  }
  if (!cameraId.value) cameraId.value = cameras.value[0]?.camera_uuid ?? "";
});

function submit() {
  if (!description.value.trim()) {
    toast("请填写修正说明", "warn");
    return;
  }
  toast("修正工单已提交至 TrajNexa_Map 待审核；主系统不直接修改 GIS 数据", "ok");
  ui.correctionOpen = false;
  description.value = "";
}
</script>

<template>
  <BaseModal
    :open="ui.correctionOpen"
    title="提交空间数据修正工单"
    subtitle="主系统只读消费 Map 发布数据；点位、视域与名称修正通过工单审核，不直接写 GIS 表。"
    width="sm"
    @close="ui.correctionOpen = false"
  >
    <div class="form">
      <div class="form-row">
        <span class="field-label">修正对象</span>
        <div class="seg">
          <button :class="{ 'is-on': target === 'fov' }" @click="target = 'fov'">视域 FOV</button>
          <button :class="{ 'is-on': target === 'point' }" @click="target = 'point'">安装点位</button>
          <button :class="{ 'is-on': target === 'name' }" @click="target = 'name'">摄像头名称</button>
        </div>
      </div>

      <label class="field">
        <span class="field-label">摄像头</span>
        <select v-model="cameraId" class="select">
          <option v-for="camera in cameras" :key="camera.camera_uuid ?? camera.camera_id" :value="camera.camera_uuid ?? ''">{{ cameraLabel(camera) }} · {{ cameraCode(camera) }}</option>
        </select>
      </label>

      <label class="field">
        <span class="field-label">修正说明</span>
        <textarea v-model="description" class="textarea" rows="3" placeholder="描述实际观察到的朝向 / 视域 / 名称差异，附上时间窗与录像来源" />
      </label>

      <label class="field">
        <span class="field-label">依据</span>
        <input v-model="basis" class="input" />
      </label>

      <div class="note">
        <AppIcon name="shield" :size="13" />
        <span>工单会带上当前空间快照版本与任务授权范围；审核通过后由 Map 发布新版本，主系统在下次快照更新时生效。</span>
      </div>
    </div>

    <template #footer>
      <span class="dim foot-hint">不会修改录像原件</span>
      <span class="grow" />
      <button class="btn btn--ghost" @click="ui.correctionOpen = false">取消</button>
      <button class="btn btn--primary" @click="submit">提交工单</button>
    </template>
  </BaseModal>
</template>

<style scoped>
.form {
  display: grid;
  gap: 12px;
  padding: 14px 18px 16px;
}

.form-row {
  display: grid;
  gap: 6px;
}

.seg {
  display: flex;
  gap: 3px;
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--bg-2);
  width: fit-content;
}

.seg button {
  height: 24px;
  padding: 0 10px;
  border-radius: 4px;
  font-size: 10.5px;
  color: var(--text-2);
}

.seg button.is-on {
  background: var(--accent-soft);
  color: var(--accent-hi);
  box-shadow: 0 0 0 1px var(--accent-line) inset;
}

.note {
  display: flex;
  gap: 7px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px dashed var(--line-2);
  font-size: 9.5px;
  line-height: 1.55;
  color: var(--text-3);
}

.foot-hint {
  font-size: 9.5px;
}

.grow {
  flex: 1;
}
</style>
