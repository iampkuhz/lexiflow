import { isEntryId } from "./protocol";

export type HiddenPreference = { entryId: string; lemma: string; gloss: string };
export type Preferences = { commonWordCutoff: 1000 | 1500 | 2000; hidden: HiddenPreference[] };
export type PreferenceAction = "read" | "setCutoff" | "hide" | "restore";
export type PreferenceResult = ({ ok: true } & Preferences) | { ok: false; reason: string };
export type RecentItem = HiddenPreference & { count: number };
export const isCutoff = (value: unknown): value is Preferences["commonWordCutoff"] =>
  value === 1000 || value === 1500 || value === 2000;
export function isHiddenPreference(value: unknown): value is HiddenPreference {
  if (!value || typeof value !== "object") return false;
  const item = value as HiddenPreference;
  return isEntryId(item.entryId) && typeof item.lemma === "string" && item.lemma.length > 0 && item.lemma.length <= 128 &&
    typeof item.gloss === "string" && item.gloss.length > 0 && item.gloss.length <= 24;
}
export function parsePreferences(value: unknown): Preferences | undefined {
  if (!value || typeof value !== "object") return;
  const state = value as Preferences;
  if (!isCutoff(state.commonWordCutoff) || !Array.isArray(state.hidden) || state.hidden.length > 1000 ||
      !state.hidden.every(isHiddenPreference)) return;
  const ids = new Set(state.hidden.map(item => item.entryId));
  if (ids.size !== state.hidden.length) return;
  return { commonWordCutoff: state.commonWordCutoff, hidden: state.hidden.map(item => ({ ...item })) };
}
