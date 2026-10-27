/**
 * TrajNexa 主系统后端（FastAPI）客户端。
 *
 * 开发环境经 Vite 代理 `/api` → API_PROXY_TARGET（默认 http://localhost:8000），
 * 生产环境由网关提供同路径，因此这里始终用相对路径，避免浏览器跨域。
 *
 * 当前只消费「添加目标要素」需要的三段能力：
 *   1. 媒体入库（图片/视频）
 *   2. 媒体预检作业（M1b/M1c：角标时间 OCR + 摄像头名称识别）
 *   3. 预检报告（时间候选、摄像头候选、质量与冲突标记）
 * 目标检测（人体 / 人脸 / 车辆）尚未接入，接口层不提供。
 */

const BASE = "/api/v1";

export type MediaItem = {
  id: string;
  kind: "image" | "video" | string;
  original_name: string;
  mime_detected: string;
  sha256: string;
  size_bytes: number;
  ingested_at: string;
};

export type InspectJob = {
  id: string;
  media_id: string;
  module: string;
  status: "queued" | "running" | "succeeded" | "failed" | "canceled" | string;
  attempt: number;
  progress: number;
  error_code: string | null;
  error_message: string | null;
};

export type OcrRow = { text: string; confidence: number; region?: string; preprocess?: string };

export type TimeSample = {
  offset_seconds: number | null;
  time_text: string | null;
  local_time: string | null;
  name_candidate: string | null;
  name_status: string;
  ocr_rows: OcrRow[];
};

export type CameraBlock = {
  overlay_name_raw: string | null;
  camera_candidates: string[];
  camera_uuid: string | null;
  match_status: string;
  ocr_engine_status: string;
  catalog_revision?: string;
  filename_name_hint?: { text: string; source: string; camera_uuid: string | null } | null;
  directory_candidates?: string[];
  directory_status?: string;
  /** 目录里与 OCR 名称相近的候选（只做建议，未绑定，需人工确认） */
  name_suggestions?: {
    camera_uuid: string;
    display_name: string;
    matched_value: string;
    ocr_text: string;
    score: number;
    method: string;
    map_camera_id?: unknown;
    /** Map 台账里这台镜头是否人工审核过；false 表示名称只是台账写法 */
    reviewed?: boolean;
    requires_human_confirmation?: boolean;
  }[];
  /** 参与比对的启用镜头数；null/缺失表示目录没读到 */
  directory_total?: number | null;
  /** 其中人工审核过的台数 */
  directory_reviewed?: number | null;
  /** 没有达到阈值的建议时，给出最接近的一条，便于解释“为什么没建议” */
  directory_nearest?: {
    camera_uuid?: string;
    display_name: string;
    score: number;
    reviewed?: boolean;
  } | null;
};

export type InspectionResult = {
  schema_version: string;
  processing_stage: string;
  media: {
    media_id: string;
    kind: string;
    sha256: string;
    size_bytes: number;
    mime_detected: string;
    technical_metadata?: { width?: number; height?: number; channels?: number; duration_seconds?: number };
  };
  time: {
    time_status: string;
    overlay_time_samples: TimeSample[];
    timezone?: string | null;
    ocr_engine_status?: string;
    /** 实际使用的 OCR 库与模型版本，例如 paddleocr 3.7.0 / PP-OCRv6 */
    ocr_engine?: { library: string; library_version?: string | null; ocr_version: string } | null;
    ocr_time_diagnostics?: { top_raw_texts: OcrRow[]; parsed_sample_count?: number };
  };
  camera: CameraBlock;
  quality?: {
    overall_status: string;
    issues: string[];
    decode_status: string;
    measurements?: Record<string, number | boolean>;
  };
  review_state?: string;
};

export type InspectionReport = {
  id: string;
  job_id: string;
  media_id: string;
  schema_version: string;
  result_json: InspectionResult;
  created_at: string;
};

export type CameraCatalogEntry = {
  camera_uuid: string;
  display_name: string;
  aliases: string[];
  active: boolean;
};

export type CameraCatalog = {
  status: string;
  reason: string | null;
  spatial_revision: string | null;
  catalog_sha256: string;
  cameras: CameraCatalogEntry[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    throw new Error("无法连接主系统后端（请确认 api 服务与 /api 代理已启动）");
  }
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: unknown; message?: unknown };
      const detail = body?.detail ?? body?.message;
      if (typeof detail === "string") message = detail;
      else if (detail) message = JSON.stringify(detail);
    } catch {
      /* 保留默认文案 */
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const mainApi = {
  listMedia: () => request<MediaItem[]>("/media"),

  /** 原始画面（只读）：用于预览与人工核对角标证据 */
  mediaContentUrl: (mediaId: string) => `${BASE}/media/${encodeURIComponent(mediaId)}/content`,

  uploadMedia(file: File) {
    const body = new FormData();
    body.append("file", file);
    return request<MediaItem>("/media", { method: "POST", body });
  },

  startInspect: (mediaId: string) =>
    request<InspectJob>(`/media/${encodeURIComponent(mediaId)}/inspect`, { method: "POST" }),

  getJob: (jobId: string) => request<InspectJob>(`/jobs/${encodeURIComponent(jobId)}`),

  getReport: (jobId: string) =>
    request<InspectionReport>(`/jobs/${encodeURIComponent(jobId)}/report`),

  listCameras: () => request<CameraCatalog>("/cameras/catalog"),
};

/** 轮询预检作业直到结束；成功返回报告，失败抛错 */
export async function runInspect(
  mediaId: string,
  options: { intervalMs?: number; timeoutMs?: number; onProgress?: (job: InspectJob) => void } = {},
): Promise<InspectionReport> {
  const { intervalMs = 900, timeoutMs = 120_000, onProgress } = options;
  const job = await mainApi.startInspect(mediaId);
  const deadline = Date.now() + timeoutMs;
  let current = job;
  while (current.status !== "succeeded") {
    if (current.status === "failed" || current.status === "canceled") {
      throw new Error(current.error_message || `预检作业${current.status === "failed" ? "失败" : "已取消"}`);
    }
    if (Date.now() > deadline) throw new Error("预检超时（>120s），请稍后重试");
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
    current = await mainApi.getJob(job.id);
    onProgress?.(current);
  }
  return mainApi.getReport(job.id);
}

/* ------------------------------ 报告解读 ------------------------------ */

export type TimeCandidate = { text: string; date: string | null; time: string | null; confidence: number };

const pad = (value: number) => String(value).padStart(2, "0");

/** 把 OCR 的 `2026-10-3/14:59:59`、`08:19:26` 之类统一成日期 + 时分秒 */
export function parseOverlayTime(text: string): { date: string | null; time: string | null } {
  const normalized = text.replace(/[／/]/g, "-").replace(/[：]/g, ":").trim();
  const dateMatch = normalized.match(/(\d{4})-(\d{1,2})-(\d{1,2})/);
  const timeMatch = normalized.match(/(\d{1,2}):(\d{2}):(\d{2})/);
  const date = dateMatch ? `${dateMatch[1]}-${pad(Number(dateMatch[2]))}-${pad(Number(dateMatch[3]))}` : null;
  const time = timeMatch ? `${pad(Number(timeMatch[1]))}:${timeMatch[2]}:${timeMatch[3]}` : null;
  return { date, time };
}

/** 时间候选：优先结构化样本，其次 OCR 原始行里能解析出时间的文本，同值去重 */
export function timeCandidates(result: InspectionResult): TimeCandidate[] {
  const out: TimeCandidate[] = [];
  const push = (text: string, confidence: number) => {
    const parsed = parseOverlayTime(text);
    if (!parsed.time) return;
    if (out.some((item) => item.time === parsed.time)) return;
    out.push({ text, date: parsed.date, time: parsed.time, confidence });
  };
  for (const sample of result.time.overlay_time_samples ?? []) {
    if (sample.time_text) {
      const rows = sample.ocr_rows ?? [];
      const own = rows.find((row) => row.text?.trim() === sample.time_text?.trim());
      push(sample.time_text, own?.confidence ?? 0);
    }
    for (const row of sample.ocr_rows ?? []) push(row.text, row.confidence ?? 0);
  }
  for (const row of result.time.ocr_time_diagnostics?.top_raw_texts ?? []) push(row.text, row.confidence ?? 0);
  return out.sort((a, b) => b.confidence - a.confidence).slice(0, 4);
}

/** 摄像头名称候选：角标名、结构化 OSD、文件名提示、目录候选、OCR 文本依次合并去重 */
export function cameraNameCandidates(result: InspectionResult): { name: string; source: string }[] {
  const camera = result.camera;
  const out: { name: string; source: string }[] = [];
  const push = (name: string | null | undefined, source: string) => {
    const value = (name ?? "").trim();
    if (!value || out.some((item) => item.name === value)) return;
    out.push({ name: value, source });
  };
  push(camera.overlay_name_raw, "画面角标");
  for (const name of camera.camera_candidates ?? []) push(name, "画面角标候选");
  push(camera.filename_name_hint?.text, "文件名提示");
  for (const name of camera.directory_candidates ?? []) push(name, "目录候选");

  // 后端结构化解析失败时，画面里仍可能印着名称：从 OCR 行里挑像名称的文本作为低信任候选
  for (const sample of result.time.overlay_time_samples ?? []) {
    for (const row of sample.ocr_rows ?? []) {
      if (looksLikeCameraName(row.text)) push(row.text, "OCR 文本候选");
    }
  }
  return out;
}

/** 只做“像不像摄像头名称”的粗筛：排除时间/日期/地名，要求带分隔符或数字 */
export function looksLikeCameraName(text: string | null | undefined): boolean {
  const value = (text ?? "").trim();
  if (value.length < 4) return false;
  if (/^[\d\s:：/\-.]+$/.test(value)) return false;
  if (/^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(value)) return false;
  if (!/[\u4e00-\u9fa5]/.test(value) && !/[A-Za-z]/.test(value)) return false;
  return value.includes("-") || value.includes("_") || /\d/.test(value);
}

export const TIME_STATUS_TEXT: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
  parsed: { label: "时间已解析", tone: "ok" },
  ok: { label: "时间已解析", tone: "ok" },
  source_conflict_needs_review: { label: "时间存在冲突，需人工确认", tone: "warn" },
  observed_unverified: { label: "时间待人工确认", tone: "warn" },
  unreadable: { label: "未能识别时间", tone: "danger" },
  unknown: { label: "未能识别时间", tone: "danger" },
};

export const CAMERA_STATUS_TEXT: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
  matched: { label: "已匹配到镜头目录", tone: "ok" },
  structured_osd_candidate_unbound: { label: "识别到角标名称，未绑定镜头目录", tone: "warn" },
  overlay_only_unbound: { label: "识别到角标名称，未绑定镜头目录", tone: "warn" },
  no_labelled_name: { label: "画面无结构化摄像头名称", tone: "warn" },
  no_camera_name_pattern: { label: "画面无结构化摄像头名称", tone: "warn" },
  observed_unverified: { label: "名称待人工确认", tone: "warn" },
};
