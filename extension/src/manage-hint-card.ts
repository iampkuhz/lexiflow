/** 仅渲染两处列表共享的展示字段；调用方负责统计来源与操作。 */
export function createHintCard(word: string, gloss: string, frequencyRank: number | null, count: number) {
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
  term.append(english, chinese);
  const rail = document.createElement("div");
  rail.className = "hint-rail";
  const frequency = document.createElement("span");
  frequency.className = "number hint-count"; frequency.textContent = `${count} 次`;
  rail.append(frequency);
  card.append(rank, term, rail);
  return { card, rail };
}
