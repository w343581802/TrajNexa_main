/** 视频命名工具：本地路径/目录 → 开头几秒 OCR → 原地重命名 → CSV 表格。 */
const BASE = "/api/v1/video-tools/naming";

export type VideoNamingItem = {
  media_id: string;
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  progress: number;
  original_name: string;
  original_path: string;
  renamed_name: string;
  renamed_path: string;
  folder_original_path: string;
  folder_renamed_path: string;
  folder_renamed_name: string;
  manual_renamed?: boolean;
  camera_name: string;
  camera_uuid: string | null;
  capture_time: string | null;
  time_source: "ocr" | "filename" | "" | string;
  ocr_status: string;
  match_status: string;
  duration_seconds: number | null;
  size_bytes: number;
  needs_review: boolean;
  warnings: string[];
  error: string | null;
  frame_relpath: string | null;
  renamed_relpath: string | null;
  download_url: string | null;
  frame_url: string | null;
};

export type VideoNamingBatch = {
  batch_id: string;
  status: "processing" | "succeeded" | "partial" | "failed" | string;
  total: number;
  completed: number;
  succeeded: number;
  failed: number;
  created_at: string;
  items: VideoNamingItem[];
  table_url: string;
  can_download_table: boolean;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new Error("无法连接主系统后端（请确认 api 服务与 /api 代理已启动）");
  }
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = (await response.json()) as {
        error?: { message?: unknown };
        detail?: unknown;
      };
      const detail = body?.error?.message ?? body?.detail;
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail === "object" && "message" in detail) {
        message = String((detail as { message?: unknown }).message ?? message);
      } else if (detail) message = JSON.stringify(detail);
    } catch {
      /* 保留默认文案 */
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const videoNamingApi = {
  start(paths: string[], recursive = true, renameFolders = false): Promise<VideoNamingBatch> {
    return request<VideoNamingBatch>(`${BASE}/batches`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths, recursive, rename_folders: renameFolders }),
    });
  },
  get(batchId: string): Promise<VideoNamingBatch> {
    return request<VideoNamingBatch>(`${BASE}/batches/${encodeURIComponent(batchId)}`);
  },
  rename(batchId: string, mediaId: string, name: string): Promise<VideoNamingItem> {
    return request<VideoNamingItem>(
      `${BASE}/batches/${encodeURIComponent(batchId)}/items/${encodeURIComponent(mediaId)}/rename`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
  },
};
