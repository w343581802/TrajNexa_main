<template>
  <main class="layout">
    <header class="header">
      <div><div class="eyebrow">TrajNexa / 迹枢</div><h1>主系统 Demo M1b</h1></div>
      <div class="state"><span :class="['dot', health === 'ok' ? 'ready' : '']"></span> API {{ health === 'ok' ? '就绪' : '未连接' }}</div>
    </header>
    <p class="banner">M1b 支持可选 PaddleOCR 角标识别与多帧时间检查（须启用 OCR Worker）；尚未开展设备 UUID 绑定、全片逐帧核验、地图视角校验或跨镜头分析。</p>
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
            <h3>M1b 媒体预检报告</h3>
            <p>媒体类型：{{ report.result_json.media.kind }}；角标时间：{{ (report.result_json.time as any)?.time_status ?? "未知" }}；角标摄像头：{{ (report.result_json.camera as any)?.overlay_name_raw ?? "未确定" }}；摄像头匹配：{{ (report.result_json.camera as any)?.match_status ?? "未知" }}；解码：{{ (report.result_json.quality as any)?.decode_status ?? "未知" }}；地图视角：待检查。</p>
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
type Report = { result_json: { media: { kind: string }, [key: string]: unknown } }
const media = ref<Media[]>([])
const job = ref<Job | null>(null)
const report = ref<Report | null>(null)
const selectedFile = ref<File | null>(null)
const busy = ref(false)
const error = ref('')
const health = ref('unknown')
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
  busy.value = true; error.value = ''; report.value = null
  try { job.value = await api<Job>(`/media/${mediaId}/inspect`, { method: 'POST' }); await refreshJob() }
  catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function refreshJob() {
  if (!job.value) return
  try {
    job.value = await api<Job>(`/jobs/${job.value.id}`)
    if (job.value.status === 'succeeded') report.value = await api<Report>(`/jobs/${job.value.id}/report`)
  } catch (e) { error.value = String(e) }
}
async function retryJob() {
  if (!job.value) return
  try { job.value = await api<Job>(`/jobs/${job.value.id}/retry`, { method: 'POST' }); report.value = null }
  catch (e) { error.value = String(e) }
}
onMounted(async () => {
  try { health.value = (await api<{status:string}>('/health')).status } catch { health.value = 'offline' }
  await loadMedia()
  poller = setInterval(() => { if (job.value?.status === 'queued' || job.value?.status === 'running') refreshJob() }, 1500)
})
onUnmounted(() => { if (poller) clearInterval(poller) })
</script>
