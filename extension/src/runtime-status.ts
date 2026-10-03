import { API_URL, REQUEST_TIMEOUT_MS } from "./protocol";

export const API_CONTRACT = "caption-hints.v1";
export const RUNTIME_STATUS_URL = API_URL.replace(/\/caption-hints$/, "/runtime-status");
export type RuntimeStatus = { softwareVersion: string; apiContract: string; mode: "formal" | "demo" | "invalid";
  ready: boolean; reason: "OK" | "DEMO_MODE" | "NO_PUBLISHED_DATA" | "DEPENDENCY_UNAVAILABLE" | "SCHEMA_MISMATCH" | "PREWARM_DEGRADED";
  datasetVersion: number | null };
export type StatusRead = { ok: true; status: RuntimeStatus } | { ok: false; reason: "network" | "timeout" | "invalid-response" | "protocol-mismatch" };
const reasons = new Set(["OK", "DEMO_MODE", "NO_PUBLISHED_DATA", "DEPENDENCY_UNAVAILABLE", "SCHEMA_MISMATCH", "PREWARM_DEGRADED"]);
function validVersion(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (!/^(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})(?:-SNAPSHOT\.g[a-f0-9]{7,64}(?:\.dirty\.[a-f0-9]{12,64})?)?$/.test(value)) return false;
  const numeric = value.split("-")[0];
  const parts = numeric.split(".");
  return parts.length === 3 && parts.every(part => /^(0|[1-9]\d{0,4})$/.test(part) && Number(part) <= 65535) && numeric !== "0.0.0";
}
export function parseRuntimeStatus(value: unknown): RuntimeStatus | undefined {
  if (!value || typeof value !== "object") return;
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== 6 || !["softwareVersion", "apiContract", "mode", "ready", "reason", "datasetVersion"].every(key => Object.hasOwn(item, key))) return;
  if (!validVersion(item.softwareVersion) || item.apiContract !== API_CONTRACT ||
      typeof item.mode !== "string" || !["formal", "demo", "invalid"].includes(item.mode) || typeof item.ready !== "boolean" ||
      typeof item.reason !== "string" || !reasons.has(item.reason) ||
      !(item.datasetVersion === null || (Number.isSafeInteger(item.datasetVersion) && (item.datasetVersion as number) >= 0))) return;
  const mode = item.mode as RuntimeStatus["mode"], reason = item.reason as RuntimeStatus["reason"];
  if ((mode === "demo" && (item.ready || reason !== "DEMO_MODE" || item.datasetVersion !== null)) ||
      (mode === "formal" && (reason === "DEMO_MODE" || (item.ready !== (reason === "OK" || reason === "PREWARM_DEGRADED")))) ||
      (mode === "invalid" && (item.ready || reason !== "SCHEMA_MISMATCH" || item.datasetVersion !== null)) ||
      (item.ready && (item.datasetVersion as number) < 1) ||
      ((reason === "OK" || reason === "PREWARM_DEGRADED") && (item.datasetVersion as number) < 1) ||
      (reason === "NO_PUBLISHED_DATA" && (mode !== "formal" || item.ready || item.datasetVersion !== 0)) ||
      (reason === "SCHEMA_MISMATCH" && mode === "formal" && item.datasetVersion !== null) ||
      (reason === "DEPENDENCY_UNAVAILABLE" && item.datasetVersion !== null && (item.datasetVersion as number) < 1)) return;
  return { softwareVersion: item.softwareVersion, apiContract: item.apiContract, mode, ready: item.ready, reason,
    datasetVersion: item.datasetVersion as number | null };
}
export async function readRuntimeStatus(signal?: AbortSignal): Promise<StatusRead> {
  if (signal?.aborted) return { ok: false, reason: "network" };
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  let timedOut = false;
  const timeout = signal ? undefined : setTimeout(() => { timedOut = true; controller.abort(); }, REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(RUNTIME_STATUS_URL, { method: "GET", cache: "no-store",
      redirect: "error", credentials: "omit", referrerPolicy: "no-referrer", signal: controller.signal });
    if (controller.signal.aborted) return { ok: false, reason: "network" };
    if (!response.ok) return { ok: false, reason: "network" };
    let data: unknown;
    try { data = await response.json(); } catch { return { ok: false, reason: "invalid-response" }; }
    if (controller.signal.aborted) return { ok: false, reason: "network" };
    if (data && typeof data === "object" && "apiContract" in data && (data as { apiContract?: unknown }).apiContract !== API_CONTRACT)
      return { ok: false, reason: "protocol-mismatch" };
    const status = parseRuntimeStatus(data);
    return status ? { ok: true, status } : { ok: false, reason: "invalid-response" };
  } catch { return { ok: false, reason: timedOut ? "timeout" : "network" }; }
  finally { if (timeout !== undefined) clearTimeout(timeout); signal?.removeEventListener("abort", abort); }
}
