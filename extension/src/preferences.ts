import { isStableId } from "./protocol";

export const PREFERENCE_KEY = "lexiflow.suppressed-entries";
export const MAX_SUPPRESSED_ENTRIES = 500;
/** Publishing a different dictionary version never inherits an old suppression implicitly. */
export function suppressionKey(entryId: string, lexiconVersion: number): string {
  return `${entryId}@${lexiconVersion}`;
}
function isSuppressionKey(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const [entryId, version, extra] = value.split("@");
  return isStableId(entryId) && extra === undefined && /^[1-9][0-9]*$/.test(version ?? "") &&
    Number.isSafeInteger(Number(version));
}
export type PreferenceAction = "read" | "suppress" | "restore-all";
export type PreferenceResult = { ok: true; entryKeys: string[] } |
  { ok: false; reason: "storage" | "limit" | "invalid-request" };
export type PreferenceStorage = { read: () => Promise<unknown>; write: (ids: string[]) => Promise<void> };

/** Explicit local choices only. Serial mutation prevents two tabs losing one another's changes. */
export class LocalPreferences {
  private queue: Promise<unknown> = Promise.resolve();
  constructor(private readonly storage: PreferenceStorage) {}

  execute(action: PreferenceAction, entryId?: string, lexiconVersion?: number): Promise<PreferenceResult> {
    const operation = this.queue.then(() => this.apply(action, entryId, lexiconVersion));
    this.queue = operation.catch(() => undefined);
    return operation;
  }

  private async apply(action: PreferenceAction, entryId?: string, lexiconVersion?: number): Promise<PreferenceResult> {
    if (!["read", "suppress", "restore-all"].includes(action) ||
        (action === "suppress" && (!isStableId(entryId) || !Number.isSafeInteger(lexiconVersion) || lexiconVersion! < 1))) return { ok: false, reason: "invalid-request" };
    try {
      if (action === "restore-all") { await this.storage.write([]); return { ok: true, entryKeys: [] }; }
      const value = await this.storage.read();
      if (value !== undefined && (!Array.isArray(value) || value.length > MAX_SUPPRESSED_ENTRIES ||
          !value.every(isSuppressionKey))) return { ok: false, reason: "storage" };
      const entries = new Set<string>(value as string[] | undefined);
      if (action === "suppress") {
        const key = suppressionKey(entryId!, lexiconVersion!);
        if (!entries.has(key) && entries.size >= MAX_SUPPRESSED_ENTRIES) return { ok: false, reason: "limit" };
        entries.add(key);
      }
      const entryKeys = [...entries].sort();
      if (action !== "read") await this.storage.write(entryKeys);
      return { ok: true, entryKeys };
    } catch { return { ok: false, reason: "storage" }; }
  }
}
