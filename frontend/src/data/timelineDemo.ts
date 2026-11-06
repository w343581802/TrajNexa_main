import type { TimelineCard, TimelineViewCard } from "../services/mainApi";

/**
 * 主视图演示卡：真实案件当前只有 1 条 human_confirmed 候选，且没有逐帧轨迹，
 * 无法生成真正的“已确认轨迹”卡。这里提供三张明确标注“演示样本”的卡片，
 * 仅用于页面结构与详情抽屉验收，不写入任何接口、不代表真实身份确认；
 * 轨迹段数与帧数只是界面结构示意，不是这些片段的追踪结果。
 * 仅在 Vite 开发环境、且该目标没有真实已确认轨迹时生效。
 */
const DEMO_CASE_ID = "4da6cac0-5047-488e-af49-087e1513eb17";
const DEMO_TARGET_ID = "65e49f14-8f1e-48e9-9884-33067fbfb4bc";
const DEMO_LABEL = "演示样本";

type DemoSpec = {
  index: number;
  place: string;
  camera: string;
  clipId: string;
  offsetSeconds: number;
  observedAt: string;
  frames: number;
};

const SPECS: DemoSpec[] = [
  {
    index: 1,
    place: "桂康路松山巷 附近",
    camera: "1-LT-桂康路松山巷",
    clipId: "6d2e8d6c-73bd-4ece-86ba-aa9a36246836",
    offsetSeconds: 3.2,
    observedAt: "2026-10-23T15:00:03",
    frames: 31,
  },
  {
    index: 2,
    place: "桂康路正通路口 附近",
    camera: "1-LT-桂康路正通路口-向西",
    clipId: "9e11b553-b899-42a2-9dda-0cfdaeff22e0",
    offsetSeconds: 4.4,
    observedAt: "2026-10-23T15:00:04",
    frames: 22,
  },
  {
    index: 3,
    place: "步行街桂康路 附近",
    camera: "1-LT-步行街桂康路-向东",
    clipId: "2043d3ad-ca66-49f5-9c9f-e643aec4e367",
    offsetSeconds: 4.6,
    observedAt: "2026-10-23T15:00:05",
    frames: 17,
  },
];

function techCard(spec: DemoSpec): TimelineCard {
  const segmentId = `demo-segment-${spec.index}`;
  return {
    cardId: segmentId,
    kind: "track_segment",
    title: `演示轨迹段 ${spec.index}`,
    subtitle: `${spec.camera} · 演示片段`,
    observedAt: spec.observedAt,
    analysisAt: null,
    targetId: DEMO_TARGET_ID,
    cameraLabel: spec.camera,
    identityStatus: "human_confirmed",
    spatialQuality: "experimental_estimate",
    degradeReason: null,
    position: null,
    displayHint: "no_position",
    playback: {
      clipId: spec.clipId,
      offsetSeconds: spec.offsetSeconds,
      timeBasis: "clip_start_local",
      timeQuality: "uncalibrated_demo",
    },
    evidence: {
      trackId: `DEMO-T${spec.index}`,
      state: "demo_confirmed",
      frames: spec.frames,
      points: spec.frames,
      retrievalScore: null,
      marginTop1Top2: null,
      evidenceRefs: [],
      demo: true,
    },
    segment: {
      segmentId,
      trackId: `DEMO-T${spec.index}`,
      window: [spec.offsetSeconds, spec.offsetSeconds + 4],
      frames: spec.frames,
      breakReason: null,
    },
    presentation: "confirmed_track",
  };
}

function viewCard(spec: DemoSpec): TimelineViewCard {
  const segmentId = `demo-segment-${spec.index}`;
  return {
    cardId: `demo-confirmed-${spec.index}`,
    kind: "confirmed_track",
    title: `演示已确认轨迹 ${spec.index}`,
    place: spec.place,
    subtitle: `1 段 · ${spec.frames} 帧 · ${DEMO_LABEL}`,
    observedAt: spec.observedAt,
    observedAtSource: "event_time",
    timeQuality: "uncalibrated_demo",
    identityStatus: "human_confirmed",
    spatialQuality: "experimental_estimate",
    detailCardIds: [segmentId],
    candidate: { count: 1, pending: 0, cardIds: [segmentId] },
    region: null,
    labels: [DEMO_LABEL],
  };
}

export function demoConfirmedCards(
  caseId: string | null | undefined,
  targetId: string | null | undefined,
): { cards: TimelineViewCard[]; techCards: TimelineCard[]; active: boolean } {
  if (!import.meta.env.DEV || caseId !== DEMO_CASE_ID || targetId !== DEMO_TARGET_ID) {
    return { cards: [], techCards: [], active: false };
  }
  return { cards: SPECS.map(viewCard), techCards: SPECS.map(techCard), active: true };
}

export { DEMO_LABEL };