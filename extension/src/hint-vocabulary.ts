import { isFrequencyRank } from "./protocol";

export type VocabularyRow = { english: string; chinese: string; frequencyRank: number | null; hintCount: number };
export type VocabularyObservation = Omit<VocabularyRow, "hintCount"> & {
  entryId: string; version: number; segmentKey: string; occurrence: string; displayed: boolean;
};

/** 只保留本页提示词与计数；已经离场的片段不再持有出现位置。 */
export class HintVocabulary {
  private rows = new Map<string, VocabularyRow>();
  private counted = new Map<string, Set<string>>();
  private version?: number;

  observe(activeSegments: ReadonlySet<string>, observations: readonly VocabularyObservation[]): void {
    for (const segment of this.counted.keys()) if (!activeSegments.has(segment)) this.counted.delete(segment);
    for (const { entryId, version, segmentKey, occurrence, displayed, english, chinese, frequencyRank } of observations) {
      if (this.version !== version) {
        this.reset();
        this.version = version;
      }
      let row = this.rows.get(entryId);
      if (!row) {
        row = { english, chinese, frequencyRank, hintCount: 0 };
        this.rows.set(entryId, row);
      }
      if (displayed) {
        let seen = this.counted.get(segmentKey);
        if (!seen) { seen = new Set(); this.counted.set(segmentKey, seen); }
        if (!seen.has(occurrence)) { seen.add(occurrence); row.hintCount++; }
      }
    }
  }

  snapshot(): VocabularyRow[] {
    return [...this.rows.values()].sort((a, b) =>
      b.hintCount - a.hintCount ||
      a.english.localeCompare(b.english, "en") || a.chinese.localeCompare(b.chinese, "zh-CN"))
      .map(row => ({ ...row }));
  }

  reset(): void { this.rows.clear(); this.counted.clear(); this.version = undefined; }
}

/** 管理页只消费目标页返回的单词级数据。 */
export function isVocabularyRow(item: unknown): item is VocabularyRow {
  if (!item || typeof item !== "object") return false;
  const row = item as VocabularyRow;
  return typeof row.english === "string" && row.english.length > 0 && row.english.length <= 500 &&
    typeof row.chinese === "string" && row.chinese.length > 0 && row.chinese.length <= 48 &&
    isFrequencyRank(row.frequencyRank) && Number.isSafeInteger(row.hintCount) && row.hintCount >= 0;
}
