<template>
  <main class="layout">
    <header class="header">
      <div><div class="eyebrow">TrajNexa / 迹枢</div><h1>主系统 Demo M1d</h1></div>
      <div class="state"><span :class="['dot', health === 'ok' ? 'ready' : '']"></span> API {{ health === 'ok' ? '就绪' : '未连接' }}</div>
    </header>
    <p class="banner">M1d：人工绑定摄像头后，可登记已核对的固定场景参考截图，并比较静态背景。匹配结果不代表地理朝向角度已验证。</p>
    <div class="content">
      <div class="main-col">
        <section class="panel">
          <h2>素材上传</h2>
          <p class="muted">支持 PNG / JPG / WebP / MP4 / MKV / AVI；最大文件大小由后端配置。请仅使用有权处理的测试素材。</p>
          <form @submit.prevent="upload">
            <input type="file" accept="image/png,image/jpeg,image/webp,video/mp4,video/x-matroska,video/x-msvideo" @change="chooseFile" />
            <button :disabled="!selectedFile || busy" type="submit">{{ busy ? '上传中…' : '上传素材' }}</button>
          </form>
          <p v-if="error" class="error" role="alert">{{ error }}</p>
        </section>
        <section class="panel">
          <div class="section-head"><h2>媒体素材</h2><button class="secondary" @click="loadMedia">刷新</button></div>
          <p v-if="!media.length" class="muted">尚无上传素材。</p>
          <div v-for="item in media" :key="item.id" class="item">
            <div><strong>{{ item.original_name }}</strong><p class="muted">{{ item.kind }} · {{ bytes(item.size_bytes) }} · SHA-256 {{ item.sha256.slice(0, 12) }}…</p></div>
            <button @click="createJob(item.id)" :disabled="busy">开始媒体预检</button>
          </div>
        </section>
        <section class="panel" v-if="job">
          <div class="section-head"><h2>作业状态</h2><button class="secondary" @click="refreshJob">刷新</button></div>
          <div class="pills"><span class="chip">{{ job.status }}</span><span class="muted">{{ job.progress }}% · 尝试 {{ job.attempt }} 次</span></div>
          <p class="mono muted">作业 ID：{{ job.id }}</p>
          <p v-if="job.error_message" class="error">{{ job.error_message }}</p>
          <button v-if="job.status === 'failed'" @click="retryJob">重试</button>
          <div v-if="report" class="report">
            <h3>M1d 媒体预检报告</h3>
            <p>媒体类型：{{ report.result_json.media.kind }}；角标时间：{{ (report.result_json.time as any)?.time_status ?? "未知" }}；角标摄像头：{{ (report.result_json.camera as any)?.overlay_name_raw ?? "未确定" }}；摄像头匹配：{{ (report.result_json.camera as any)?.match_status ?? "未知" }}；解码：{{ (report.result_json.quality as any)?.decode_status ?? "未知" }}；目录候选：{{ (report.result_json.camera as any)?.directory_status ?? "未知" }}；地图视角：需人工绑定后另行校验。</p>
            <div class="camera-review">
              <h3>设备绑定（人工确认）</h3>
              <p class="muted">自动匹配仅提供候选，不会认定设备。当前目录：{{ catalog?.status === 'available' ? (catalog.spatial_revision + ' · ' + catalog.cameras.length + ' 台') : '不可用，请导入只读快照' }}</p>
              <p v-if="reportCamera.directory_candidates?.length" class="muted">
                OCR 名称候选：{{ reportCamera.directory_candidates.map(c => c.display_name + ' [' + c.camera_uuid.slice(0, 8) + ']').join('、') }}
              </p>
              <p v-if="bindingHistory.length" class="muted">最新操作：{{ bindingHistory[bindingHistory.length - 1].decision === 'confirm' ? '已人工绑定' : '已撤销' }} · {{ bindingHistory[bindingHistory.length - 1].camera_name_snapshot ?? '无当前设备' }}</p>
              <div class="camera-fields">
                <label for="camera-select">摄像头</label>
                <select id="camera-select" v-model="selectedCamera" :disabled="catalog?.status !== 'available' || bindingBusy">
                  <option value="">请选择设备</option>
                  <option v-for="device in (catalog?.cameras ?? []).filter(x => x.active)" :key="device.camera_uuid" :value="device.camera_uuid">{{ device.display_name }} · {{ device.camera_uuid.slice(0, 8) }}</option>
                </select>
                <label for="camera-note">核对依据（至少 8 字）</label>
                <textarea id="camera-note" v-model="bindingNote" rows="2" maxlength="500" placeholder="例如：人工核对原录像的设备通道编号" />
                <div class="binding-actions">
                  <button :disabled="bindingBusy || !selectedCamera || bindingNote.trim().length < 8" @click="recordBinding('confirm')">确认此设备</button>
                  <button class="secondary" :disabled="bindingBusy || bindingNote.trim().length < 8" @click="recordBinding('revoke')">撤销当前绑定</button>
                </div>
              </div>
              <p v-if="bindingError" role="alert" class="error">{{ bindingError }}</p>
              <details v-if="bindingHistory.length"><summary>绑定历史（{{ bindingHistory.length }} 条）</summary>
                <ul><li v-for="entry in bindingHistory" :key="entry.id">{{ entry.created_at }} · {{ entry.decision }} · {{ entry.camera_name_snapshot ?? '—' }} · {{ entry.note }}</li></ul>
              </details>
            </div>
            <div class="camera-review">
              <h3>固定场景视角校验（M1d）</h3>
              <p class="muted">先人工确认摄像头，再从已上传图片中登记一张经过核实的固定场景参考图。只比较画面背景，不验证绝对地理角度。</p>
              <p class="muted">当前有效绑定：{{ currentBoundCamera || '未绑定 / 已撤销' }}</p>
              <div v-if="currentBoundCamera" class="camera-fields">
                <label for="view-reference-media">参考图片（已有素材）</label>
                <select id="view-reference-media" v-model="referenceMediaId" :disabled="viewBusy">
                  <option value="">请选择图片</option>
                  <option v-for="item in media.filter(x => x.kind === 'image')" :key="item.id" :value="item.id">{{ item.original_name }} · {{ item.id.slice(0, 8) }}</option>
                </select>
                <label for="view-reference-note">参考来源说明（至少 8 字）</label>
                <textarea id="view-reference-note" v-model="referenceNote" rows="2" maxlength="500" placeholder="例如：人工核对原摄像头固定画面的参考截图" />
                <button :disabled="viewBusy || !referenceMediaId || referenceNote.trim().length < 8" @click="registerViewReference">登记参考图</button>
                <label for="view-reference-select">用于比较的参考图</label>
                <select id="view-reference-select" v-model="selectedReferenceId" :disabled="viewBusy">
                  <option value="">请选择已登记的参考图</option>
                  <option v-for="item in viewReferences" :key="item.id" :value="item.id">{{ item.media_id.slice(0, 8) }} · {{ item.note }}</option>
                </select>
                <button :disabled="viewBusy || !selectedReferenceId" @click="runViewCheck">比较当前素材与参考图</button>
              </div>
              <p v-if="viewError" class="error" role="alert">{{ viewError }}</p>
              <p v-if="viewChecks.length" class="muted">最新比较：{{ viewChecks[viewChecks.length - 1].result_json.status }} · {{ viewChecks[viewChecks.length - 1].result_json.reason }}（仅候选，需人工复核）</p>
              <details v-if="viewChecks.length"><summary>查看场景校验历史（{{ viewChecks.length }} 条）</summary>
                <pre>{{ JSON.stringify(viewChecks, null, 2) }}</pre>
              </details>
            </div>
            <pre>{{ JSON.stringify(report.result_json, null, 2) }}</pre>
          </div>
        </section>
      </div>
      <DemoMap />
    </div>
  </main>
</template>
<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import DemoMap from './components/DemoMap.vue'

type Media = { id: string; kind: string; original_name: string; size_bytes: number; sha256: string }
type Job = { id: string; status: string; attempt: number; progress: number; error_message: string | null }
type CameraRecord = { camera_uuid: string; display_name: string; aliases: string[]; active: boolean }
type CameraCatalog = { status: string; spatial_revision: string | null; cameras: CameraRecord[] }
type BindingEntry = { id: string; decision: 'confirm' | 'revoke'; camera_uuid: string | null; camera_name_snapshot: string | null; note: string; created_at: string }
type ViewReference = { id: string; camera_uuid: string; media_id: string; note: string; catalog_revision: string }
type ViewCheck = { id: string; camera_uuid: string; result_json: { status: string; reason: string; [key: string]: unknown } }
type Report = { id: string; result_json: { media: { kind: string }, camera?: { directory_status?: string; directory_candidates?: {camera_uuid: string; display_name: string}[] }; [key: string]: unknown } }
const media = ref<Media[]>([])
const job = ref<Job | null>(null)
const report = ref<Report | null>(null)
const selectedFile = ref<File | null>(null)
const busy = ref(false)
const error = ref('')
const health = ref('unknown')
const catalog = ref<CameraCatalog | null>(null)
const bindingHistory = ref<BindingEntry[]>([])
const selectedCamera = ref('')
const bindingNote = ref('')
const bindingBusy = ref(false)
const bindingError = ref('')
const reportCamera = computed(() => report.value?.result_json.camera ?? {})
const currentBoundCamera = computed(() => {
  const last = bindingHistory.value[bindingHistory.value.length - 1]
  return last?.decision === 'confirm' ? (last.camera_uuid ?? '') : ''
})
const viewReferences = ref<ViewReference[]>([])
const viewChecks = ref<ViewCheck[]>([])
const selectedReferenceId = ref('')
const referenceMediaId = ref('')
const referenceNote = ref('')
const viewBusy = ref(false)
const viewError = ref('')
let poller: ReturnType<typeof setInterval> | undefined
const bytes = (n: number) => n < 1048576 ? `${(n / 1024).toFixed(1)} KiB` : `${(n / 1048576).toFixed(1)} MiB`
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch('/api/v1' + path, init)
  const body = await res.json()
  if (!res.ok) throw new Error(body?.error?.message || body?.detail || `HTTP ${res.status}`)
  return body as T
}
function chooseFile(e: Event) { selectedFile.value = (e.target as HTMLInputElement).files?.[0] ?? null }
async function loadMedia() { try { media.value = await api<Media[]>('/media') } catch (e) { error.value = String(e) } }
async function upload() {
  if (!selectedFile.value) return
  busy.value = true; error.value = ''
  try {
    const form = new FormData(); form.append('file', selectedFile.value)
    await api<Media>('/media', { method: 'POST', body: form })
    selectedFile.value = null
    await loadMedia()
  } catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function createJob(mediaId: string) {
  busy.value = true; error.value = ''; report.value = null; bindingHistory.value = []; selectedCamera.value = ''; bindingNote.value = ''; viewReferences.value = []; viewChecks.value = []; selectedReferenceId.value = ''
  try { job.value = await api<Job>(`/media/${mediaId}/inspect`, { method: 'POST' }); await refreshJob() }
  catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function refreshJob() {
  if (!job.value) return
  try {
    job.value = await api<Job>(`/jobs/${job.value.id}`)
    if (job.value.status === 'succeeded' && !report.value) {
      report.value = await api<Report>(`/jobs/${job.value.id}/report`)
      bindingHistory.value = await api<BindingEntry[]>(`/inspections/${report.value.id}/camera-bindings`)
      await loadViewData()
      const suggested = report.value.result_json.camera?.directory_candidates
      if (suggested?.length === 1) selectedCamera.value = suggested[0].camera_uuid
    }
  } catch (e) { error.value = String(e) }
}
async function retryJob() {
  if (!job.value) return
  try { job.value = await api<Job>(`/jobs/${job.value.id}/retry`, { method: 'POST' }); report.value = null; bindingHistory.value = []; viewReferences.value = []; viewChecks.value = [] }
  catch (e) { error.value = String(e) }
}
async function recordBinding(decision: 'confirm' | 'revoke') {
  if (!report.value) return
  bindingBusy.value = true; bindingError.value = ''
  try {
    await api(`/inspections/${report.value.id}/camera-bindings`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision, camera_uuid: decision === 'confirm' ? selectedCamera.value : null, note: bindingNote.value.trim() }) })
    bindingHistory.value = await api<BindingEntry[]>(`/inspections/${report.value.id}/camera-bindings`)
    bindingNote.value = ''
    await loadViewData()
  } catch (e) { bindingError.value = String(e) } finally { bindingBusy.value = false }
}

async function loadViewData() {
  viewReferences.value = []
  selectedReferenceId.value = ''
  if (!report.value) return
  viewChecks.value = await api<ViewCheck[]>(`/inspections/${report.value.id}/view-checks`)
  if (!currentBoundCamera.value) return
  viewReferences.value = await api<ViewReference[]>(`/cameras/${currentBoundCamera.value}/view-references`)
  selectedReferenceId.value = viewReferences.value[0]?.id ?? ''
}
async function registerViewReference() {
  if (!currentBoundCamera.value) return
  viewBusy.value = true; viewError.value = ''
  try {
    const item = await api<ViewReference>(`/cameras/${currentBoundCamera.value}/view-references`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_id: referenceMediaId.value, note: referenceNote.value.trim() })
    })
    referenceNote.value = ''
    await loadViewData()
    selectedReferenceId.value = item.id
  } catch (e) { viewError.value = String(e) } finally { viewBusy.value = false }
}
async function runViewCheck() {
  if (!report.value || !selectedReferenceId.value) return
  viewBusy.value = true; viewError.value = ''
  try {
    await api<ViewCheck>(`/inspections/${report.value.id}/view-checks`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reference_id: selectedReferenceId.value })
    })
    viewChecks.value = await api<ViewCheck[]>(`/inspections/${report.value.id}/view-checks`)
  } catch (e) { viewError.value = String(e) } finally { viewBusy.value = false }
}
onMounted(async () => {
  try { health.value = (await api<{status:string}>('/health')).status } catch { health.value = 'offline' }
  await loadMedia()
  try { catalog.value = await api<CameraCatalog>('/cameras/catalog') } catch (e) { bindingError.value = String(e) }
  poller = setInterval(() => { if (job.value?.status === 'queued' || job.value?.status === 'running') refreshJob() }, 1500)
})
onUnmounted(() => { if (poller) clearInterval(poller) })
</script>
