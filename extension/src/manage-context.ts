export type VideoContext = { tabId: number; pageKey: string };

export function isYouTubeVideoId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{11}$/u.test(value);
}

export function normalizeVideoTitle(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > 300) return undefined;
  return value.replace(/\s+/gu, " ").trim() || undefined;
}

export function managementUrl(url: string, context?: VideoContext): string {
  return context ? `${url}#${new URLSearchParams({ tab: String(context.tabId), page: context.pageKey })}` : url;
}

export function videoContext(hash: string): VideoContext | undefined {
  const fields = new URLSearchParams(hash.replace(/^#/u, ""));
  const raw = fields.get("tab"), pageKey = fields.get("page");
  if (!raw || !/^[1-9][0-9]*$/u.test(raw) || !pageKey || !/^[A-Za-z0-9_-]{1,128}$/u.test(pageKey) ||
      fields.size !== 2 || !Number.isSafeInteger(Number(raw))) return;
  return { tabId: Number(raw), pageKey };
}

export function isManagementUrl(url: string | undefined, manage: string): boolean {
  if (url === manage) return true;
  if (!url?.startsWith(`${manage}#`)) return false;
  return !!videoContext(url.slice(manage.length));
}
