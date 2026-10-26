/* ------------------------------------------------------------------
 * 演示数据（不接后端）
 * 说明：全部为 UI 原型用的占位数据。空间点位与 FOV 由 TrajNexa_Map 发布，
 * 这里仅按同一数据形态模拟，不代表真实摄像头位置或录像。
 * ------------------------------------------------------------------ */

export type CoverageStatus = "indexed" | "scanning" | "unknown" | "error";

export type CoverageInterval = {
  id: string;
  /** 当日分钟数，例如 1080 = 18:00 */
  s: number;
  e: number;
  status: CoverageStatus;
  files: string[];
  note?: string;
};

export type Camera = {
  id: string;
  uuid: string;
  name: string;
  site: string;
  channel: string;
  x: number;
  y: number;
  /** 朝向（度，0 = 正北） */
  heading: number;
  fov: number;
  fovType: "校准" | "理论";
  quality: string;
  source: string;
  notice?: "mapping" | "unreadable" | null;
  analyzing?: boolean;
  intervals: CoverageInterval[];
};

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
  /** 记录来源：seed 演示数据 / 用户添加 */
  origin?: "seed" | "user";
};

export type FeatureStatus = "verified" | "review" | "unknown" | "conflict" | "proposed";

export type TargetFeature = {
  id: string;
  /** 由哪个要素录入产生；种子特征为空 */
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
};

/* ------------------------------ 基础几何 ------------------------------ */

export type Road = { id: string; name: string; width: number; points: [number, number][] };
export type Building = { x: number; y: number; w: number; h: number; tone: 0 | 1 | 2 };
export type MapLabel = { text: string; x: number; y: number; size?: number; kind?: "area" | "road" };

export const roads: Road[] = [
  { id: "r-main", name: "城北大道", width: 54, points: [[-20, 214], [1420, 206]] },
  { id: "r-west", name: "西环路", width: 40, points: [[300, -20], [296, 450]] },
  { id: "r-south", name: "南巷", width: 34, points: [[882, -20], [878, 450]] },
  { id: "r-north", name: "北门街", width: 26, points: [[280, 102], [1420, 94]] },
  { id: "r-back", name: "后巷", width: 22, points: [[946, 450], [1420, 250]] },
  { id: "r-east", name: "东门巷", width: 20, points: [[286, 320], [892, 312]] },
];

/** 街区外框（内部再生成建筑体块） */
const blockFrames: [number, number, number, number][] = [
  [14, 14, 260, 158],
  [14, 252, 260, 166],
  [324, 14, 528, 74],
  [324, 116, 528, 62],
  [324, 252, 528, 54],
  [324, 334, 528, 84],
  [904, 14, 482, 74],
  [904, 116, 482, 62],
  [904, 252, 482, 66],
  [904, 346, 300, 72],
  [1250, 346, 136, 72],
];

function makeBuildings(): Building[] {
  let seed = 20261025;
  const rnd = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  const out: Building[] = [];
  for (const [bx, by, bw, bh] of blockFrames) {
    const cols = Math.max(1, Math.round(bw / 64));
    const rows = Math.max(1, Math.round(bh / 58));
    const gap = 7;
    const cw = (bw - gap * (cols + 1)) / cols;
    const ch = (bh - gap * (rows + 1)) / rows;
    for (let c = 0; c < cols; c += 1) {
      for (let r = 0; r < rows; r += 1) {
        if (cw < 16 || ch < 14) continue;
        const jitterX = (rnd() - 0.5) * 4;
        const jitterY = (rnd() - 0.5) * 4;
        out.push({
          x: bx + gap + c * (cw + gap) + jitterX,
          y: by + gap + r * (ch + gap) + jitterY,
          w: cw - (rnd() < 0.25 ? 6 : 0),
          h: ch - (rnd() < 0.25 ? 4 : 0),
          tone: (rnd() < 0.55 ? 0 : rnd() < 0.6 ? 1 : 2) as 0 | 1 | 2,
        });
      }
    }
  }
  return out;
}

export const buildings: Building[] = makeBuildings();

export const mapLabels: MapLabel[] = [
  { text: "西 片 城 中 村", x: 140, y: 64, size: 13, kind: "area" },
  { text: "南 巷 市 场", x: 600, y: 400, size: 12, kind: "area" },
  { text: "北 门 广 场", x: 1150, y: 148, size: 11.5, kind: "area" },
  { text: "地 铁 A 口", x: 928, y: 244, size: 10.5, kind: "area" },
  { text: "城北大道", x: 700, y: 222, size: 10, kind: "road" },
  { text: "西环路", x: 288, y: 392, size: 9.5, kind: "road" },
  { text: "南巷", x: 862, y: 46, size: 9.5, kind: "road" },
  { text: "北门街", x: 470, y: 98, size: 9.5, kind: "road" },
  { text: "东门巷", x: 560, y: 314, size: 9.5, kind: "road" },
];

/* ------------------------------ 摄像头 ------------------------------ */

const f = (name: string) => name;

export const cameras: Camera[] = [
  {
    id: "CAM-01",
    uuid: "c7f1a2d4-01",
    name: "东门入口 01",
    site: "东门入口",
    channel: "通道 01",
    x: 302,
    y: 198,
    heading: 128,
    fov: 76,
    fovType: "校准",
    quality: "实测校准 · 质量 B",
    source: "rev-2026.10.25-A",
    intervals: [
      { id: "i-0101", s: 1085, e: 1217, status: "indexed", files: [f("东门入口01_20261025_180500_183600.mp4"), f("东门入口01_20261025_183600_190700.mp4"), f("东门入口01_20261025_190700_201700.mp4")] },
      { id: "i-0102", s: 1300, e: 1325, status: "indexed", files: [f("东门入口01_20261025_214000_220500.mp4")] },
      { id: "i-0103", s: 1390, e: 1430, status: "scanning", files: [] },
    ],
  },
  {
    id: "CAM-01B",
    uuid: "c7f1a2d4-02",
    name: "东门入口 01B",
    site: "东门入口",
    channel: "通道 02",
    x: 292,
    y: 188,
    heading: 150,
    fov: 40,
    fovType: "理论",
    quality: "未标定 · 质量 C",
    source: "rev-2026.10.25-A",
    intervals: [{ id: "i-01b1", s: 1203, e: 1217, status: "indexed", files: [f("东门入口01B_20261025_200300_201700.mp4")] }],
  },
  {
    id: "CAM-02",
    uuid: "b2d8e5f0-03",
    name: "西门通道 02",
    site: "西门通道",
    channel: "通道 01",
    x: 298,
    y: 300,
    heading: 40,
    fov: 64,
    fovType: "校准",
    quality: "实测校准 · 质量 B",
    source: "rev-2026.10.25-A",
    intervals: [
      { id: "i-0201", s: 1140, e: 1200, status: "indexed", files: [f("西门通道02_20261025_190000_193000.mp4"), f("西门通道02_20261025_193000_200000.mp4")] },
      { id: "i-0202", s: 1215, e: 1240, status: "error", files: [f("西门通道02_20261025_201500_204000.mp4")], note: "容器时长与文件名不符，待人工核查" },
      { id: "i-0203", s: 1260, e: 1350, status: "indexed", files: [f("西门通道02_20261025_210000_213000.mp4"), f("西门通道02_20261025_213000_223000.mp4")] },
    ],
  },
  {
    id: "CAM-03",
    uuid: "9a4c1b77-04",
    name: "南侧道路 03",
    site: "南侧道路",
    channel: "通道 01",
    x: 620,
    y: 238,
    heading: 96,
    fov: 100,
    fovType: "校准",
    quality: "实测校准 · 质量 A",
    source: "rev-2026.10.25-A",
    intervals: [
      { id: "i-0301", s: 1080, e: 1170, status: "indexed", files: [f("南侧道路03_20261025_180000_183000.mp4"), f("南侧道路03_20261025_183000_193000.mp4")] },
      { id: "i-0302", s: 1185, e: 1225, status: "indexed", files: [f("南侧道路03_20261025_194500_202500.mp4")] },
      { id: "i-0303", s: 1258, e: 1300, status: "indexed", files: [f("南侧道路03_20261025_205800_214000.mp4")] },
    ],
  },
  {
    id: "CAM-04",
    uuid: "44e0a912-05",
    name: "北门广场 04",
    site: "北门广场",
    channel: "通道 01",
    x: 1180,
    y: 168,
    heading: 202,
    fov: 92,
    fovType: "理论",
    quality: "理论视域 · 无标定",
    source: "rev-2026.10.25-A",
    intervals: [],
  },
  {
    id: "CAM-05",
    uuid: "7d21c6a8-06",
    name: "地铁 A 口 05",
    site: "地铁 A 口",
    channel: "通道 01",
    x: 900,
    y: 190,
    heading: 250,
    fov: 70,
    fovType: "校准",
    quality: "实测校准 · 质量 B",
    source: "rev-2026.10.25-A",
    analyzing: true,
    intervals: [
      { id: "i-0501", s: 1220, e: 1245, status: "indexed", files: [f("地铁A口05_20261025_202000_204500.mp4")] },
      { id: "i-0502", s: 1290, e: 1380, status: "indexed", files: [f("地铁A口05_20261025_213000_223000.mp4"), f("地铁A口05_20261025_220500_230000.mp4")] },
    ],
  },
  {
    id: "CAM-06",
    uuid: "e613f0b2-07",
    name: "巷口便利店 06",
    site: "巷口便利店",
    channel: "通道 01",
    x: 860,
    y: 330,
    heading: 15,
    fov: 56,
    fovType: "理论",
    quality: "未标定 · 质量 C",
    source: "rev-2026.10.25-A",
    intervals: [{ id: "i-0601", s: 1224, e: 1233, status: "unknown", files: [f("便利店_不定时_001.mp4")], note: "文件名无时间，画面角标未识别" }],
  },
  {
    id: "CAM-07",
    uuid: "1f9b7c35-08",
    name: "停车场东 07",
    site: "停车场东",
    channel: "通道 01",
    x: 1250,
    y: 300,
    heading: 300,
    fov: 80,
    fovType: "理论",
    quality: "理论视域 · 低质量",
    source: "rev-2026.10.25-A",
    intervals: [],
  },
  {
    id: "CAM-08",
    uuid: "5c0d2e91-09",
    name: "天桥西 08",
    site: "天桥西",
    channel: "通道 01",
    x: 420,
    y: 390,
    heading: 345,
    fov: 68,
    fovType: "校准",
    quality: "实测校准 · 质量 B",
    source: "rev-2026.10.25-A",
    intervals: [{ id: "i-0801", s: 1230, e: 1244, status: "indexed", files: [f("天桥西08_20261025_203000_204400.mp4")] }],
  },
  {
    id: "CAM-09",
    uuid: "a37e8d14-10",
    name: "市场后巷 09",
    site: "市场后巷",
    channel: "通道 01",
    x: 1010,
    y: 400,
    heading: 305,
    fov: 54,
    fovType: "校准",
    quality: "实测校准 · 质量 B",
    source: "rev-2026.10.25-A",
    intervals: [
      { id: "i-0901", s: 1220, e: 1236, status: "indexed", files: [f("市场后巷09_20261025_202000_203600.mp4"), f("市场后巷09_20261025_202600_203041.mp4")] },
      { id: "i-0902", s: 1240, e: 1258, status: "indexed", files: [f("市场后巷09_20261025_204000_205800.mp4")] },
    ],
  },
  {
    id: "CAM-10",
    uuid: "0be4a6c3-11",
    name: "学校围墙 10",
    site: "学校围墙",
    channel: "通道 01",
    x: 60,
    y: 130,
    heading: 60,
    fov: 48,
    fovType: "理论",
    quality: "未标定 · 质量 C",
    source: "rev-2026.10.25-A",
    notice: "mapping",
    intervals: [{ id: "i-1001", s: 1080, e: 1120, status: "indexed", files: [f("学校北墙_20261025_180000_184000.mp4")] }],
  },
  {
    id: "CAM-11",
    uuid: "d9c2507e-12",
    name: "工地门口 11",
    site: "工地门口",
    channel: "通道 01",
    x: 1340,
    y: 380,
    heading: 286,
    fov: 66,
    fovType: "理论",
    quality: "理论视域 · 无标定",
    source: "rev-2026.10.25-A",
    intervals: [{ id: "i-1101", s: 1330, e: 1400, status: "indexed", files: [f("工地门口11_20261025_221000_232000.mp4")] }],
  },
  {
    id: "CAM-12",
    uuid: "3a8f5d60-13",
    name: "北门街 12",
    site: "北门街",
    channel: "通道 01",
    x: 700,
    y: 102,
    heading: 172,
    fov: 60,
    fovType: "校准",
    quality: "实测校准 · 质量 B",
    source: "rev-2026.10.25-A",
    intervals: [{ id: "i-1201", s: 1238, e: 1252, status: "indexed", files: [f("北门街12_20261025_203800_205200.mp4")] }],
  },
];

/* ------------------------------ 目标要素 ------------------------------ */

export const elements: TargetElement[] = [
  {
    id: "E01",
    timeLabel: "20:12:08",
    time: "2026-10-25 20:12:08",
    cameraId: "CAM-01",
    cameraLabel: "CAM-01 东门入口 01",
    status: "confirmed",
    statusLabel: "来源已确认",
    note: "用户框选 · 已核对原录像通道号",
    tone: "color",
    quality: "good",
  },
  {
    id: "E02",
    timeLabel: "20:26:41",
    time: "2026-10-25 20:26:41",
    cameraId: "CAM-09",
    cameraLabel: "CAM-09 市场后巷 09",
    status: "candidate",
    statusLabel: "候选待核验",
    note: "角标 OCR：市场后巷 · 名称匹配 2 个候选",
    tone: "color",
    quality: "good",
  },
  {
    id: "E03",
    timeLabel: "20:41:03",
    time: "2026-10-25 20:41:03",
    cameraId: null,
    cameraLabel: "区域：南巷北口（待定位）",
    status: "unlocated",
    statusLabel: "仅确定区域",
    note: "未匹配到唯一通道 · 不构造精确点位",
    tone: "gray",
    quality: "low",
  },
  {
    id: "E04",
    timeLabel: "20:52:18",
    time: "2026-10-25 20:52:18",
    cameraId: "CAM-12",
    cameraLabel: "CAM-12 北门街 12",
    status: "candidate",
    statusLabel: "待人工确认",
    note: "夜间画面 · 与 E01 时空关联未审核",
    tone: "gray",
    quality: "low",
  },
];

/* ------------------------------ 目标特征 ------------------------------ */

export const features: TargetFeature[] = [
  {
    id: "F-UPPER",
    attr: "上衣",
    value: "深色长款外套",
    proposed: "深蓝色连帽外套（候选）",
    status: "verified",
    statusLabel: "人工已核验",
    meta: "人工核验 · 20:18",
    sources: 3,
    confidence: 92,
    window: "20:12–20:33",
    conflict: { value: "浅灰色外套", reason: "夜间灰度画面，颜色不可靠" },
  },
  {
    id: "F-LOWER",
    attr: "下装",
    value: "无法辨色",
    status: "unknown",
    statusLabel: "未知",
    meta: "画质不足 · 未采信",
    sources: 0,
    window: "—",
  },
  {
    id: "F-SHOES",
    attr: "鞋",
    value: "浅色运动鞋",
    status: "review",
    statusLabel: "待复核",
    meta: "机器提议 · 2 条来源",
    sources: 2,
    confidence: 68,
    window: "20:19–20:29",
  },
  {
    id: "F-BAG",
    attr: "随身物",
    value: "单肩包（右肩）",
    status: "verified",
    statusLabel: "人工已核验",
    meta: "多次观察支持",
    sources: 4,
    confidence: 88,
    window: "20:12–20:44",
  },
  {
    id: "F-HEAD",
    attr: "头部 / 面部",
    value: "画质不足，无法辨认",
    status: "unknown",
    statusLabel: "未知",
    meta: "不得生成式补造细节",
    sources: 0,
    window: "—",
  },
  {
    id: "F-GAIT",
    attr: "体型 / 步态",
    value: "中等身形，步速偏快",
    status: "proposed",
    statusLabel: "机器提议",
    meta: "待复核 · 未经关联审核",
    sources: 1,
    confidence: 65,
    window: "20:19–20:41",
  },
];

export const featureStats = { pending: 2, conflicts: 1, refs: 6 };

/* ------------------------------ 时间线观测 ------------------------------ */

export const observations: Observation[] = [
  {
    id: "O-01",
    timeLabel: "18:42:11",
    kind: "observation",
    cameraId: "CAM-10",
    title: "候选观测 · 学校围墙 10",
    sub: "文件映射存在歧义，观察来源未审核",
    reliability: "detected",
    relation: "unknown",
    round: "R1",
    note: "学校北墙_20261025_180000_184000.mp4",
  },
  {
    id: "O-02",
    timeLabel: "20:08:24",
    kind: "observation",
    cameraId: "CAM-03",
    title: "候选观测 · 南侧道路 03",
    sub: "符合上装的男性行人 · 未确认身份关联",
    reliability: "review",
    relation: "pending",
    round: "R2",
  },
  {
    id: "O-03",
    timeLabel: "20:12:08",
    kind: "anchor",
    cameraId: "CAM-01",
    title: "起始锚点 E01 · 用户添加要素",
    sub: "CAM-01 东门入口 01 · 来源已确认",
    reliability: null,
    relation: null,
    round: "R2",
    elementId: "E01",
  },
  {
    id: "O-04",
    timeLabel: "20:17:41",
    kind: "observation",
    cameraId: "CAM-01",
    title: "候选观测 · 东门入口 01",
    sub: "同一镜头离开方向 · 检测框已人工确认",
    reliability: "verified",
    relation: "hypothesis",
    round: "R2",
  },
  {
    id: "O-05",
    timeLabel: "20:19:02",
    kind: "observation",
    cameraId: "CAM-03",
    title: "候选观测 · 南侧道路 03",
    sub: "衣着特征一致 · 跨镜头关联仍属工作假设",
    reliability: "verified",
    relation: "hypothesis",
    round: "R2",
  },
  {
    id: "O-06",
    timeLabel: "20:24:10",
    kind: "observation",
    cameraId: "CAM-06",
    title: "候选观测 · 巷口便利店 06",
    sub: "时间未知文件 · 关联无法判断",
    reliability: "review",
    relation: "indeterminate",
    round: "R2",
    note: "便利店_不定时_001.mp4",
  },
  {
    id: "O-07",
    timeLabel: "20:26:41",
    kind: "anchor",
    cameraId: "CAM-09",
    title: "起始锚点 E02 · 用户添加要素",
    sub: "CAM-09 市场后巷 09 · 候选待核验",
    reliability: null,
    relation: null,
    round: "R2",
    elementId: "E02",
  },
  {
    id: "O-08",
    timeLabel: "20:29:55",
    kind: "observation",
    cameraId: "CAM-09",
    title: "候选观测 · 市场后巷 09",
    sub: "背包特征一致 · 待人工复核",
    reliability: "review",
    relation: "pending",
    round: "R2",
  },
  {
    id: "O-09",
    timeLabel: "20:33:12",
    kind: "observation",
    cameraId: "CAM-05",
    title: "候选观测 · 地铁 A 口 05",
    sub: "人工确认的特定关联 · 审核人 李",
    reliability: "verified",
    relation: "confirmed",
    round: "R2",
  },
  {
    id: "O-10",
    timeLabel: "20:35:40",
    kind: "gap",
    cameraId: null,
    title: "未知盲区 · 南巷北口",
    sub: "20:31–20:38 无已索引录像，不默认存在可行路径",
    reliability: null,
    relation: null,
    round: "R2",
  },
  {
    id: "O-11",
    timeLabel: "20:41:03",
    kind: "anchor",
    cameraId: null,
    title: "起始锚点 E03 · 区域锚点",
    sub: "仅确定区域（南巷北口）· 不构造精确点位",
    reliability: null,
    relation: null,
    round: "R2",
    elementId: "E03",
  },
  {
    id: "O-12",
    timeLabel: "20:44:20",
    kind: "observation",
    cameraId: "CAM-12",
    title: "候选观测 · 北门街 12",
    sub: "夜间画面 · 颜色证据不可靠",
    reliability: "review",
    relation: "hypothesis",
    round: "R2",
  },
];

/* ------------------------------ 作业与进度 ------------------------------ */

export const jobs: Job[] = [
  {
    id: "job-1041",
    cameraId: "CAM-03",
    file: "南侧道路03_20261025_194500_202500.mp4",
    from: "19:45",
    to: "20:25",
    status: "detecting",
    statusLabel: "检测中",
    progress: 62,
    candidates: 3,
    undetected: 1,
  },
  {
    id: "job-1042",
    cameraId: "CAM-09",
    file: "市场后巷09_20261025_202000_203600.mp4",
    from: "20:20",
    to: "20:36",
    status: "review",
    statusLabel: "待人工复核",
    progress: 100,
    candidates: 2,
    undetected: 0,
    note: "候选已进入时间线，需人工确认关联",
  },
  {
    id: "job-1043",
    cameraId: "CAM-05",
    file: "地铁A口05_20261025_202000_204500.mp4",
    from: "20:20",
    to: "20:45",
    status: "assembling",
    statusLabel: "结果整理中",
    progress: 88,
    candidates: 1,
    undetected: 0,
  },
  {
    id: "job-1044",
    cameraId: "CAM-02",
    file: "西门通道02_20261025_201500_204000.mp4",
    from: "20:15",
    to: "20:40",
    status: "queued",
    statusLabel: "排队",
    progress: 0,
    candidates: 0,
    undetected: 0,
    note: "容器时长异常，排在人工核查之后",
  },
];

export const indexInfo = {
  dir: "/data/videos/case_001_western",
  status: "增量刷新中",
  progress: 93,
  lastRun: "20:42",
  files: 1284,
  parsed: 1196,
  mapped: 12,
  unmapped: 63,
  errors: 25,
  stages: ["文件枚举", "时间解析", "名称映射", "异常归集"],
};

export const roundInfo = {
  id: "R2",
  directionLabel: "向前倒查",
  anchor: "E01",
  stage: "跨镜头关联",
  progress: 41,
  queued: 96,
  done: 38,
  failed: 2,
  review: 2,
};

export const snapshot = {
  revision: "rev-2026.10.25-A",
  publishedAgo: "发布 2 小时前",
  crs: "EPSG:4547",
  cameras: cameras.length,
};

export const taskMeta = {
  name: "城中村西片 · 夜间轨迹研判",
  id: "TASK-2026-1025-04",
  authorized: "西片 3 路 · 18:00–23:00",
  owner: "李调查员",
  role: "研判员",
};

/* ------------------------------ 覆盖时间轴 ------------------------------ */

export const coverageDate = "2026-10-25";

export const coverageSummary = {
  cameras: cameras.length,
  withRecording: cameras.filter((c) => c.intervals.some((i) => i.status === "indexed")).length,
  noRecording: cameras.filter((c) => !c.intervals.some((i) => i.status === "indexed")).length,
  mappingIssues: cameras.filter((c) => c.notice === "mapping").length,
  unknownFiles: 25,
  lastIndex: "20:42",
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

/** 摄像头在查询时间窗内是否有已索引录像 */
export function hasCoverage(camera: Camera, window: TimeWindow): boolean {
  return camera.intervals.some((i) => i.status === "indexed" && i.s < window.end && i.e > window.start);
}

export function coverageMinutes(camera: Camera, window: TimeWindow): number {
  return camera.intervals
    .filter((i) => i.status === "indexed")
    .reduce((sum, i) => sum + Math.max(0, Math.min(i.e, window.end) - Math.max(i.s, window.start)), 0);
}

export function intersection(camera: Camera, window: TimeWindow): { s: number; e: number }[] {
  return camera.intervals
    .filter((i) => i.status === "indexed" && i.s < window.end && i.e > window.start)
    .map((i) => ({ s: Math.max(i.s, window.start), e: Math.min(i.e, window.end) }));
}
