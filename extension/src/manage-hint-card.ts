/** 统一词条展示；次数、资格和操作由对应标签提供。 */
export function createHintCard(word: string, gloss: string | null, frequencyRank: number | null, count?: number) {
  const card = document.createElement("li");
  card.className = "hint-card";
  const rank = document.createElement("span");
  rank.className = "hint-rank";
  if (frequencyRank !== null) {
    rank.textContent = `#${frequencyRank}`;
    rank.setAttribute("aria-label", `词频第 ${frequencyRank} 名`);
  }
  const term = document.createElement("div");
  term.className = "hint-term";
  const english = document.createElement("span");
  english.className = "hint-word"; english.lang = "en"; english.textContent = word;
  const chinese = document.createElement("span");
  chinese.className = "hint-gloss"; chinese.textContent = gloss || "—";
  term.append(english, " ", chinese);
  const rail = document.createElement("div");
  rail.className = "hint-rail";
  if (count !== undefined) {
    const frequency = document.createElement("span");
    frequency.className = "number hint-count"; frequency.textContent = `${count} 次`;
    rail.append(frequency);
  }
  card.append(rank, term, rail);
  return { card, rail };
}
