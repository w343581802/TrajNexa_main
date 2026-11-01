import { reactive } from "vue";

/**
 * M5 experiment choices shared only by model management and the independent test lab.
 * Main investigation uses a server-defined default profile, NEVER these browser IDs.
 * Only model IDs are persisted locally; no credentials, files or evidence.
 * Actual models and authority are always validated by the backend catalogs.
 */
const SMALL_KEY = "trajnexa.m5.selectedSmallModels";
const VISION_KEY = "trajnexa.m5.selectedVisionModel";

function readSmallModels(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(SMALL_KEY) || "[]");
    return Array.isArray(saved) ? saved.filter((v): v is string =>
      typeof v === "string" && v.length <= 128).slice(0, 5) : [];
  } catch {
    return [];
  }
}

function readVisionModel(): string {
  try {
    const value = localStorage.getItem(VISION_KEY);
    return value && /^[a-z][a-z0-9_-]{0,47}$/.test(value) ? value : "";
  } catch {
    return "";
  }
}

export const modelChoices = reactive({
  smallModelIds: readSmallModels(),
  visionModelId: readVisionModel(),
});

export function setSmallModelIds(ids: string[]) {
  modelChoices.smallModelIds = [...new Set(ids)].slice(0, 5);
  try { localStorage.setItem(SMALL_KEY, JSON.stringify(modelChoices.smallModelIds)); }
  catch { /* private browsing may disallow persistence */ }
}

export function setVisionModelId(id: string) {
  modelChoices.visionModelId = id;
  try { localStorage.setItem(VISION_KEY, id); }
  catch { /* preference is kept for this session */ }
}
