<template>
  <main class="layout">
    <header class="header">
      <div><div class="eyebrow">TrajNexa / 迹枢</div><h1>主系统 Demo M1c</h1></div>
      <div class="state"><span :class="['dot', health === 'ok' ? 'ready' : '']"></span> API {{ health === 'ok' ? '就绪' : '未连接' }}</div>
    </header>
    <p class="banner">M1c：可选角标 OCR 与本地只读设备目录候选；人工选择、确认与撤销设备绑定。地图视角校验仍属于 M1d。</p>
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
            <h3>M1c 媒体预检报告</h3>
            <p>媒体类型：{{ report.result_json.media.kind }}；角标时间：{{ (report.result_json.time as any)?.time_status ?? "未知" }}；角标摄像头：{{ (report.result_json.camera as any)?.overlay_name_raw ?? "未确定" }}；摄像头匹配：{{ (report.result_json.camera as any)?.match_status ?? "未知" }}；解码：{{ (report.result_json.quality as any)?.decode_status ?? "未知" }}；目录匹配：{{ (report.result_json.camera as any)?.directory_status ?? '未检查' }}；地图视角：待检查。</p>
            <section class="camera-review">
              <h3>设备绑定（人工确认）</h3>
              <p class="muted">目录：{{ catalog?.status === 'available' ? catalog.spatial_revision : '未配置' }}。自动候选不等于已绑定。</p>
              <p v-if="report.result_json.camera?.directory_candidates?.length" class="muted">OCR 候选：{{ report.result_json.camera.directory_candidates.map(c => c.display_name).join('、') }}</p>
              <p v-if="history.length" class="muted">最近一次人工操作：{{ history[history.length-1].decision === 'confirm' ? '确认设备' : '撤销绑定' }} {{ history[history.length-1].camera_name_snapshot ?? '' }}</p>
              <label for="cam-choice">摄像头目录</label>
              <select id="cam-choice" v-model="selectedCamera" :disabled="catalog?.status !== 'available' || bindingBusy">
                <option value="">请选择</option>
                <option v-for="device in (catalog?.cameras ?? []).filter(c => c.active)" :key="device.camera_uuid" :value="device.camera_uuid">{{ device.display_name }}</option>
              </select>
              <label for="cam-note">人工核对依据（至少 8 字）</label>
              <textarea id="cam-note" v-model="bindingNote" maxlength="500" rows="2" />
              <div class="binding-actions">
                <button :disabled="bindingBusy || !selectedCamera || bindingNote.trim().length < 8" @click="binding('confirm')">确认绑定</button>
                <button class="secondary" :disabled="bindingBusy || bindingNote.trim().length < 8" @click="binding('revoke')">撤销绑定</button>
              </div>
              <p class="error" v-if="bindingError">{{ bindingError }}</p>
              <details v-if="history.length"><summary>查看历史（{{ history.length }}）</summary>
                <ul><li v-for="item in history" :key="item.id">{{ item.created_at }} · {{ item.decision }} · {{ item.camera_name_snapshot ?? '-' }} · {{ item.note }}</li></ul>
              </details>
            </section>
            <pre>{{ JSON.stringify(report.result_json, null, 2) }}</pre>
          </div>
        </section>
      </div>
      <DemoMap />
    </div>
  </main>
</template>
<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import DemoMap from './components/DemoMap.vue'

type Media = { id: string; kind: string; original_name: string; size_bytes: number; sha256: string }
type Job = { id: string; status: string; attempt: number; progress: number; error_message: string | null }
type Catalog = {status:string; spatial_revision:string|null; cameras: {camera_uuid:string;display_name:string;active:boolean}[]}
type BindingEntry = {id:string;decision:string;camera_name_snapshot:string|null;note:string;created_at:string}
type Report = {id:string; result_json:{media:{kind:string};camera?:{directory_candidates?:{camera_uuid:string;display_name:string}[]};[key:string]:unknown}}
const media = ref<Media[]>([])
const job = ref<Job | null>(null)
const report = ref<Report | null>(null)
const selectedFile = ref<File | null>(null)
const busy = ref(false)
const error = ref('')
const health = ref('unknown')
const catalog = ref<Catalog | null>(null)
const history = ref<BindingEntry[]>([])
const selectedCamera = ref('')
const bindingNote = ref('')
const bindingBusy = ref(false)
const bindingError = ref('')
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
  busy.value = true; error.value = ''; report.value = null; history.value = []; selectedCamera.value = ''
  try { job.value = await api<Job>(`/media/${mediaId}/inspect`, { method: 'POST' }); await refreshJob() }
  catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function refreshJob() {
  if (!job.value) return
  try {
    job.value = await api<Job>(`/jobs/${job.value.id}`)
    if (job.value.status === 'succeeded' && !report.value) {
      report.value = await api<Report>(`/jobs/${job.value.id}/report`)
      history.value = await api<BindingEntry[]>(`/inspections/${report.value.id}/camera-bindings`)
      const choices = report.value.result_json.camera?.directory_candidates
      if (choices?.length === 1) selectedCamera.value = choices[0].camera_uuid
    }
  } catch (e) { error.value = String(e) }
}
async function retryJob() {
  if (!job.value) return
  try { job.value = await api<Job>(`/jobs/${job.value.id}/retry`, { method: 'POST' }); report.value = null }
  catch (e) { error.value = String(e) }
}
async function binding(decision: 'confirm' | 'revoke') {
  if (!report.value) return
  bindingBusy.value = true; bindingError.value = ''
  try {
    await api(`/inspections/${report.value.id}/camera-bindings`, {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({decision,camera_uuid:decision === 'confirm' ? selectedCamera.value : null,
                            note:bindingNote.value.trim()})})
    history.value = await api<BindingEntry[]>(`/inspections/${report.value.id}/camera-bindings`)
    bindingNote.value = ''
  } catch(e) { bindingError.value = String(e) } finally { bindingBusy.value = false }
}
onMounted(async () => {
  try { health.value = (await api<{status:string}>('/health')).status } catch { health.value = 'offline' }
  await loadMedia()
  try { catalog.value = await api<Catalog>('/cameras/catalog') } catch { bindingError.value = '设备目录读取失败' }
  poller = setInterval(() => { if (job.value?.status === 'queued' || job.value?.status === 'running') refreshJob() }, 1500)
})
onUnmounted(() => { if (poller) clearInterval(poller) })
</script>
