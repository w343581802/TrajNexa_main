/**
 * TrajNexa 主系统后端（FastAPI）客户端。
 *
 * 开发环境经 Vite 代理 `/api` → API_PROXY_TARGET（默认 http://localhost:8000），
 * 生产环境由网关提供同路径，因此这里始终用相对路径，避免浏览器跨域。
 *
 * 当前消费「案件」与「添加目标要素」需要的接口：
 *   1. 案件（案件列表 / 新建 / 删除；删除只清理该案件上传的要素文件）
 *   2. 媒体入库（图片/视频，可归属到案件）
 *   3. 媒体预检作业（M1b/M1c：角标时间 OCR + 摄像头名称识别）
 *   4. 预检报告（时间候选、摄像头候选、质量与冲突标记）
 * 目标检测（人体 / 人脸 / 车辆）尚未接入，接口层不提供。
 */

const BASE = "/api/v1";

export type M4DetectionProposal = {
  proposal_index: number;
  object_class: "person" | "vehicle";
  detector_label: string;
  confidence: number;
  bbox_normalized: [number, number, number, number];
  status: "unverified_detection_proposal";
};
export type M4DetectionPreview = {
  schema_version: string;
  status: "candidates_unverified";
  media_id: string;
  case_id: string;
  model_family: string;
  model_sha256: string;
  source_sha256: string;
  candidate_count: number;
  candidates: M4DetectionProposal[];
};

export type M4Observation = {
  id: string;
  case_id: string;
  media_id: string;
  object_class: "person" | "vehicle" | "other";
  review_state: "pending" | string;
  created_at: string;
  review_note: string;
  source_inspection_id: string | null;
  analysis_json: {
    schema_version: string;
    bbox_normalized: number[];
    bbox_pixels: number[];
    method: string;
    quality: { heuristic_only: boolean; flags: string[];
      median_luminance_0_255: number; laplacian_variance: number };
    appearance: { roi_tones_unverified: { tone: string; fraction: number }[];
      semantic_status: string };
    linkage: { camera_uuid: string | null; geo_status: string };
  };
};

export type MediaItem = {
  id: string;
  /** 归属案件；未归属（历史素材）时为 null */
  case_id: string | null;
  kind: "image" | "video" | string;
  original_name: string;
  mime_detected: string;
  sha256: string;
  size_bytes: number;
  ingested_at: string;
};

/** 案件：主系统组织研判的入口，只持有上传的要素文件 */
export type CaseItem = {
  id: string;
  code: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  media_count: number;
  media_bytes: number;
};

export type CaseDeleteResult = {
  id: string;
  code: string;
  name: string;
  media_files_removed: number;
  bytes_removed: number;
  media_files_missing: number;
  recordings_preserved: boolean;
  message: string;
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

/**
 * 后端 overlay_identity 的归一结论：录像资源扫描、视频命名工具、添加要素截图识别
 * 三处共用同一份形状，前端只认这一份，不再各自解析 time/camera 原始字段。
 */
export type OverlayIdentity = {
  capture_time: string | null;
  /** ocr=画面角标读到；filename=角标读不出、用文件名 14 位时间戳兜底 */
  time_source: string;
  camera_name: string | null;
  name_status: string;
  names: string[];
  /** ran / disabled / unavailable / error / no_frame / not_checked */
  ocr_status: string;
  duration_seconds: number | null;
  frame_offset: number | null;
  warnings: string[];
};

export type InspectionResult = {
  /** 归一结论；老报告可能没有这个字段 */
  identity?: OverlayIdentity;
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

/* ------------------------------ 录像资源 ------------------------------ */

export type RecordingDirEntry = { name: string; path: string; video_count: number };
export type RecordingBrowse = {
  root: string;
  path: string;
  parent: string;
  dirs: RecordingDirEntry[];
  videos: { name: string; path: string; size_bytes: number }[];
};

export type RecordingScanJob = {
  id: string;
  source_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  total_files: number;
  processed_files: number;
  added_files: number;
  updated_files: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
};

export type RecordingSource = {
  id: string;
  case_id: string;
  /** 相对录像根目录的路径；空串表示根目录本身 */
  path: string;
  label: string;
  auto_scan: boolean;
  created_at: string;
  last_scan_at: string | null;
  last_status: string;
  last_error: string | null;
  file_count: number;
  time_count: number;
  matched_count: number;
  scanning: boolean;
  job: RecordingScanJob | null;
};

/** 扫描到的一段录像：只有开始时间，不推断结束时间 */
export type RecordingClip = {
  id: string;
  source_id: string;
  relpath: string;
  file_name: string;
  duration_seconds: number | null;
  start_local: string | null;
  start_source: "ocr" | "filename" | string;
  camera_name: string | null;
  camera_uuid: string | null;
  name_source: "ocr" | "filename" | "none" | string;
  match_status: string;
  ocr_status: string;
  review_kind: "none" | "confirmed" | "rejected" | string;
  reviewer_note: string | null;
  suggestions: { camera_uuid: string; display_name: string; score: number; reviewed?: boolean }[];
  scanned_at: string;
};

export type RecordingKeyframe = {
  requested_offset_seconds: number;
  status: "frame_available" | "frame_unavailable";
  width: number | null; height: number | null;
  quality: { heuristic_only: boolean; warnings: string[];
    median_luminance_0_255: number; laplacian_variance: number } | null;
  detections: { model_family: string; model_sha256: string | null;
    status: "candidates_unverified";
    candidates: { proposal_index: number; object_class: string;
      confidence: number; bbox_normalized: number[];
      detector_label: string }[] } | null;
};
export type M4dAttributePreview = {
  case_id: string; clip_id: string; requested_offset_seconds: number;
  image_sha256: string; frame_time_verified: false; identity_established: false;
  vlm_status: string; vlm_error_code: string | null;
  attributes_status: string; requires_human_review: true;
  attributes: { name: string; state: string; value: string | null; source: string }[];
};

export type RecordingKeyframePreview = {
  status: "sampled_keyframes_unverified";
  case_id: string; clip_id: string;
  start_time_candidate: string | null;
  start_time_source: string;
  absolute_frame_time_verified: false;
  camera_uuid_candidate: string | null;
  camera_verified_for_observation: false;
  tracklet_status: "not_established";
  frame_time_semantics: "requested_seek_offset_not_verified_pts";
  frames: RecordingKeyframe[];
};

export type RecordingSummary = {
  case_id: string;
  source_count: number;
  file_count: number;
  time_count: number;
  name_count: number;
  matched_count: number;
  camera_uuids: string[];
  last_scan_at: string | null;
  scanning: boolean;
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
      const body = (await response.json()) as {
        detail?: unknown;
        message?: unknown;
        error?: { message?: unknown };
      };
      // 后端统一错误体是 { error: { code, message } }，旧接口也可能只给 detail
      const detail = body?.error?.message ?? body?.detail ?? body?.message;
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
  /* ------------------------------ 案件 ------------------------------ */

  listCases: () => request<CaseItem[]>("/cases"),

  createCase: (name: string, description = "") =>
    request<CaseItem>("/cases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    }),

  /** 删除案件：后端只清理该案件上传的要素文件，录像资源保持不动 */
  deleteCase: (caseId: string) =>
    request<CaseDeleteResult>(`/cases/${encodeURIComponent(caseId)}`, { method: "DELETE" }),

  /* ------------------------------ 媒体 ------------------------------ */

  /** 传 caseId 只看该案件的要素文件；不传则看全部（含未归属） */
  getMedia: (mediaId: string) => request<MediaItem>(`/media/${encodeURIComponent(mediaId)}`),

  listMedia: (caseId?: string | null) =>
    request<MediaItem[]>(caseId ? `/media?case_id=${encodeURIComponent(caseId)}` : "/media"),

  /** 原始画面（只读）：用于预览与人工核对角标证据 */
  mediaContentUrl: (mediaId: string) => `${BASE}/media/${encodeURIComponent(mediaId)}/content`,

  uploadMedia(file: File, caseId?: string | null) {
    const body = new FormData();
    body.append("file", file);
    if (caseId) body.append("case_id", caseId);
    return request<MediaItem>("/media", { method: "POST", body });
  },

  startInspect: (mediaId: string) =>
    request<InspectJob>(`/media/${encodeURIComponent(mediaId)}/inspect`, { method: "POST" }),

  getJob: (jobId: string) => request<InspectJob>(`/jobs/${encodeURIComponent(jobId)}`),

  getReport: (jobId: string) =>
    request<InspectionReport>(`/jobs/${encodeURIComponent(jobId)}/report`),

  /** M4a：人工框选截图区域 → 观测记录；只读原截图，不执行身份识别 */
  addM4Observation: (mediaId: string, body: {
    object_class: "person" | "vehicle" | "other";
    bbox_normalized: number[];
    review_note: string;
  }) => request<M4Observation>(`/media/${encodeURIComponent(mediaId)}/observations`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }),
  listM4Observations: (mediaId: string, limit = 50, offset = 0) =>
    request<M4Observation[]>(`/media/${encodeURIComponent(mediaId)}/observations?limit=${limit}&offset=${offset}`),
  listCaseM4Observations: (caseId: string, limit = 50, offset = 0) =>
    request<M4Observation[]>(`/cases/${encodeURIComponent(caseId)}/observations?limit=${limit}&offset=${offset}`),
  /** M4b: candidates only; model is optional, no automatic identity binding. */
  previewM4Detections: (mediaId: string, minConfidence = .4) =>
    request<M4DetectionPreview>(`/media/${encodeURIComponent(mediaId)}/detections/preview`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_confidence: minConfidence }),
    }),
  getM4Observation: (id: string) =>
    request<M4Observation>(`/observations/${encodeURIComponent(id)}`),

  listCameras: () => request<CameraCatalog>("/cameras/catalog"),

  /* ------------------------------ 录像资源（只读目录） ------------------------------ */

  recordingRoot: () =>
    request<{ root: string; exists: boolean; scan_interval_seconds: number }>("/recordings/root"),

  browseRecordingDirs: (path = "") =>
    request<RecordingBrowse>(`/recordings/browse?path=${encodeURIComponent(path)}`),

  listRecordingSources: (caseId: string) =>
    request<RecordingSource[]>(`/recordings/sources?case_id=${encodeURIComponent(caseId)}`),

  addRecordingSource: (caseId: string, path: string, autoScan = false) =>
    request<RecordingSource>("/recordings/sources", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: caseId, path, auto_scan: autoScan }),
    }),

  setRecordingAutoScan: (sourceId: string, autoScan: boolean) =>
    request<RecordingSource>(`/recordings/sources/${encodeURIComponent(sourceId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_scan: autoScan }),
    }),

  removeRecordingSource: (sourceId: string) =>
    request<{ id: string; recordings_preserved: boolean; message: string }>(
      `/recordings/sources/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" },
    ),

  scanRecordingSource: (sourceId: string) =>
    request<{ source: RecordingSource; job: RecordingScanJob; already_running: boolean }>(
      `/recordings/sources/${encodeURIComponent(sourceId)}/scan`,
      { method: "POST" },
    ),

  getRecordingSourceJob: (sourceId: string) =>
    request<RecordingScanJob | null>(`/recordings/sources/${encodeURIComponent(sourceId)}/job`),

  listRecordingClips: (caseId: string) =>
    request<RecordingClip[]>(`/recordings/clips?case_id=${encodeURIComponent(caseId)}`),

  recordingSummary: (caseId: string) =>
    request<RecordingSummary>(`/recordings/summary?case_id=${encodeURIComponent(caseId)}`),

  /** 这段录像的开头截图：人工核对摄像头名称时看左边那张图 */
  previewRecordingKeyframes: (caseId: string, clipId: string, body: {
    offsets_seconds: number[]; include_detection: boolean;
  }) => request<RecordingKeyframePreview>(
    `/cases/${encodeURIComponent(caseId)}/recordings/clips/${encodeURIComponent(clipId)}/keyframes/preview`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
  ),
  previewM4dFrameAttributes: (caseId: string, clipId: string, offsetSeconds: number) =>
    request<M4dAttributePreview>(
      `/cases/${encodeURIComponent(caseId)}/recordings/clips/${encodeURIComponent(clipId)}/vlm/preview`,
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offset_seconds: offsetSeconds }) },
    ),
  recordingKeyframeImageUrl: (caseId: string, clipId: string, offsetSeconds: number) =>
    `/api/v1/cases/${encodeURIComponent(caseId)}/recordings/clips/${encodeURIComponent(clipId)}/keyframes/image?offset_seconds=${encodeURIComponent(String(offsetSeconds))}`,

  recordingClipFrameUrl: (clipId: string) =>
    `/api/v1/recordings/clips/${encodeURIComponent(clipId)}/frame`,

  /** 人工核对后写入识别名称，后端立即按台账重新匹配摄像头 */
  setRecordingClipName: (clipId: string, name: string, note?: string) =>
    request<RecordingClip>(`/recordings/clips/${encodeURIComponent(clipId)}/name`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, note: note ?? null }),
    }),

  rematchRecordingSource: (sourceId: string) =>
    request<{ source_id: string; updated: number; unchanged: number; human_kept: number }>(
      `/recordings/sources/${encodeURIComponent(sourceId)}/rematch`,
      { method: "POST" },
    ),
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
/** 归一结论：优先用后端 identity，老报告没有就按原始字段就地推一份等价的 */
export function overlayIdentity(result: InspectionResult): OverlayIdentity {
  if (result.identity) return result.identity;
  const samples = result.time.overlay_time_samples ?? [];
  const capture = samples.find((sample) => sample.local_time)?.local_time ?? null;
  const named = samples.find((sample) => sample.name_candidate);
  const engine = result.time.ocr_engine_status ?? "";
  let ocrStatus = "ran";
  if (engine === "disabled" || engine === "unavailable" || engine === "error") ocrStatus = engine;
  else if (!samples.length || result.time.time_status === "frame_unavailable") ocrStatus = "no_frame";
  return {
    capture_time: capture,
    time_source: capture ? "ocr" : "",
    camera_name: named?.name_candidate ?? result.camera.overlay_name_raw ?? null,
    name_status: named?.name_status ?? "",
    names: samples.map((sample) => sample.name_candidate).filter((v): v is string => Boolean(v)),
    ocr_status: ocrStatus,
    duration_seconds: result.media.technical_metadata?.duration_seconds ?? null,
    frame_offset: null,
    warnings: [],
  };
}

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
