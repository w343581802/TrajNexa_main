/* ------------------------------------------------------------------
 * 界面占位数据（不接后端）
 * 说明：业务数据（案件 / 要素文件 / 录像资源 / 镜头点位）全部走 /api/v1 或
 * TrajNexa_Map 只读快照；这里只保留类型定义与时间窗、日期、轮次文案等 UI 占位。
 * 演示种子（假摄像头 / 假要素 / 假观测 / 假作业 / 假轨迹）已全部删除。
 * ------------------------------------------------------------------ */

export type ElementAttribute = { key: string; value: string; unknown: boolean };

export type ElementBox = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  kind: "person" | "face" | "vehicle";
  label: string;
  auto: boolean;
};

export type TargetElement = {
  id: string;
  timeLabel: string;
  time: string;
  cameraId: string | null;
  cameraLabel: string;
  status: "confirmed" | "candidate" | "unlocated";
  statusLabel: string;
  note: string;
  tone: "color" | "gray";
  quality: "good" | "low";
  /** 目标类别：人员 / 车辆 / 其他 */
  category?: "person" | "vehicle" | "other";
  /** 录入的可观察属性（保存后转为特征候选） */
  attributes?: ElementAttribute[];
  /** 人工框选 / 自动检测框，仅用于回溯画面位置 */
  boxes?: ElementBox[];
  /** 角标时间是否未知（未知时不参与时间排序） */
  timeUnknown?: boolean;
  /** 记录来源：一律是用户添加 */
  origin?: "user";
};

export type FeatureStatus = "verified" | "review" | "unknown" | "conflict" | "proposed";

export type TargetFeature = {
  id: string;
  /** 由哪个要素录入产生 */
  elementId?: string;
  attr: string;
  value: string;
  /** 机器提议值：仅作为候选展示，永不自动覆盖 value */
  proposed?: string;
  status: FeatureStatus;
  statusLabel: string;
  meta: string;
  sources: number;
  /** 特征可信度（0–100），仅用于界面呈现；未知特征不给分 */
  confidence?: number;
  window: string;
  conflict?: { value: string; reason: string };
};

export type ObsKind = "anchor" | "observation" | "gap";

export type Reliability = "detected" | "review" | "verified" | "invalid";
export type Relation = "unknown" | "hypothesis" | "pending" | "confirmed" | "excluded" | "indeterminate";

export type Observation = {
  id: string;
  timeLabel: string;
  kind: ObsKind;
  cameraId: string | null;
  title: string;
  sub: string;
  reliability: Reliability | null;
  relation: Relation | null;
  round: string | null;
  elementId?: string;
  note?: string;
};

export type JobStatus = "queued" | "reading" | "detecting" | "assembling" | "review" | "done" | "nodetect" | "failed" | "canceled";

export type Job = {
  id: string;
  cameraId: string;
  file: string;
  from: string;
  to: string;
  status: JobStatus;
  statusLabel: string;
  progress: number;
  candidates: number;
  undetected: number;
  note?: string;
  /** 台账里的摄像头名称；没有时卡片退回显示 cameraId */
  cameraName?: string;
  /** 这段需要人工审核：卡片自动排到最前并高亮 */
  needsReview?: boolean;
  /** 审核备注（人工填写） */
  reviewNote?: string;
  /** 对应录像段（缩略帧 / 片段来源，来自扫描结果） */
  clipId?: string | null;
  /** 缩略帧地址；没有就显示占位帧，不伪造画面 */
  thumbUrl?: string | null;
  /** 缩略视频地址；没有就显示占位 */
  clipUrl?: string | null;
};

/* ------------------------------ 轮次占位 ------------------------------ */

/** 研判轮次尚未接后端：空任务态不显示，添加要素后才用它渲染轮次摘要 */
export const roundInfo = {
  id: "R2",
  directionLabel: "向前倒查",
  anchor: "起始要素",
  stage: "跨镜头关联",
  progress: 0,
  queued: 0,
  done: 0,
  failed: 0,
  review: 0,
};

/* ------------------------------ 时间工具 ------------------------------ */

export const pad2 = (n: number) => String(n).padStart(2, "0");

/** 分钟数 → HH:mm */
export const minutesToLabel = (m: number) => `${pad2(Math.floor(m / 60) % 24)}:${pad2(Math.round(m % 60))}`;

export type TimeWindow = { start: number; end: number };

export const timeWindowPresets: { id: string; label: string; start: number; end: number }[] = [
  { id: "night", label: "当夜 18:00–24:00", start: 1080, end: 1440 },
  { id: "all", label: "全天 00:00–24:00", start: 0, end: 1440 },
  { id: "evening", label: "20:00–22:00", start: 1200, end: 1320 },
  { id: "early", label: "18:00–20:00", start: 1080, end: 1200 },
];