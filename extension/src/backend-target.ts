declare const __LEXIFLOW_API_PORT__: number;
declare const __LEXIFLOW_DEFAULT_ORIGIN__: string;

const apiPort = typeof __LEXIFLOW_API_PORT__ === "number" ? __LEXIFLOW_API_PORT__ : 18080;
export const TEST_CHANNEL = apiPort !== 18080;
export const DEFAULT_ORIGIN = typeof __LEXIFLOW_DEFAULT_ORIGIN__ === "string" ? __LEXIFLOW_DEFAULT_ORIGIN__ : `http://127.0.0.1:${apiPort}`;
export const CONNECTION_KEY = "backendConnection";
export type Connection = { mode: "custom"; origin: string };

// 不接受 URL 解析器会等价化的 IP 写法、路径、凭据、查询、重定向或任意域名。
export function loopbackOrigin(input: unknown): string | undefined {
  if (typeof input !== "string") return;
  const match = /^http:\/\/(127\.0\.0\.1|localhost):([1-9][0-9]{0,4})\/?$/u.exec(input.trim());
  if (!match || Number(match[2]) > 65535) return;
  return `http://${match[1]}:${match[2]}`;
}
export function parseConnection(input: unknown): Connection | undefined {
  if (!input || typeof input !== "object" || Array.isArray(input)) return;
  const { mode, origin } = input as { mode?: unknown; origin?: unknown };
  if (mode !== "custom" || Object.keys(input).length !== 2) return;
  const verified = loopbackOrigin(origin);
  return verified ? { mode, origin: verified } : undefined;
}
export const endpoint = (origin: string, path: string): string => `${origin}/api/v1/${path}`;
