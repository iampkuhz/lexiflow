import { bounded } from "./message-deadline";
import { DEFAULT_ORIGIN, loopbackOrigin } from "./backend-target";
import { mismatchText, type RuntimeStatus } from "./runtime-status";

type Reply = ({ ok: true; status: RuntimeStatus } | { ok: false; reason: string; expected?: string; actual?: string }) & { origin?: string };

function shortSoftwareVersion(version: string): string {
  const match = /^(\d+\.\d+\.\d+)(-SNAPSHOT\..+)?$/.exec(version);
  return match ? `${match[1]}${match[2] ? " · 开发版" : ""}` : "版本未知";
}

function errorText(reason?: string): string {
  switch (reason) {
    case "protocol-mismatch": return "接口版本不匹配，请更新扩展或后端。";
    case "IDENTITY_MISMATCH": case "SCHEMA_MISMATCH":
      return "后端词库校验未通过。";
    case "INITIALIZING": case "NO_PUBLISHED_DATA": case "DEPENDENCY_UNAVAILABLE":
      return "服务尚未就绪，请稍后再试。";
    case "storage": return "地址未能保存，请重试。";
    case "busy": return "正在修改连接，请稍后重试。";
    case "invalid-response": return "该地址未返回可用的 LexiFlow 状态。";
    default: return "无法连接服务，请检查地址和端口。";
  }
}

export function attachManagementConnection(onChange: () => void): void {
  const input = document.querySelector<HTMLInputElement>("#connection-url")!;
  const form = document.querySelector<HTMLFormElement>("#connection-form")!;
  const save = document.querySelector<HTMLButtonElement>("#save-connection")!;
  const state = document.querySelector<HTMLElement>("#connection-status")!;
  const target = document.querySelector<HTMLElement>("#connection-target")!;
  const identity = document.querySelector<HTMLElement>("#connection-identity")!;
  const copy = document.querySelector<HTMLButtonElement>("#copy-diagnostics")!;
  const copyStatus = document.querySelector<HTMLElement>("#copy-status")!;
  let reply: Reply | undefined, origin = DEFAULT_ORIGIN, dirty = false, pending = false, generation = 0;
  input.value = origin;
  function report(text: string, kind = ""): void { state.textContent = text; state.dataset.kind = kind; }
  function render(value?: Reply): void {
    reply = value;
    origin = loopbackOrigin(value?.origin) ?? origin;
    if (!dirty) input.value = origin;
    target.textContent = `${value?.ok ? "已连接" : "未连接"} · ${origin.slice(7)}`;
    identity.textContent = value?.ok ? `服务版本：${shortSoftwareVersion(value.status.softwareVersion)}` : "";
    if (!value?.ok) report(value?.reason === "protocol-mismatch" ? mismatchText(value) : errorText(value?.reason), "error");
    else if (value.status.mode === "demo") report("演示模式 · 非正式就绪", "pending");
    else if (!value.status.ready) report(errorText(value.status.reason), "pending");
    else report("服务已就绪。", "success");
  }
  input.addEventListener("input", () => {
    dirty = true;
    input.setAttribute("aria-invalid", "false");
    if (!pending) report("");
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (pending) return;
    const next = loopbackOrigin(input.value);
    if (!next) {
      input.setAttribute("aria-invalid", "true");
      report("请输入本机 HTTP 地址，例如 http://127.0.0.1:18081。", "error");
      input.focus();
      return;
    }
    ++generation;
    pending = save.disabled = input.readOnly = true;
    form.setAttribute("aria-busy", "true");
    report("正在检查连接…", "pending");
    try {
      const value = await bounded(chrome.runtime.sendMessage({ type: "backend-connection", origin: next })) as Reply | undefined;
      if (value?.ok && value.origin === next) {
        dirty = false;
        render(value);
        report("已保存连接。", "success");
        onChange();
      } else { report(value?.ok === false && value.reason === "protocol-mismatch" ? mismatchText(value) : errorText(value?.ok === false ? value.reason : undefined), "error"); input.focus(); }
    } catch { report("未能确认是否已切换。请重新打开管理页核对。", "error"); input.focus(); }
    finally {
      pending = save.disabled = input.readOnly = false;
      form.setAttribute("aria-busy", "false");
    }
  });
  copy.addEventListener("click", async () => {
    copy.disabled = true;
    const runtime = reply?.ok ? reply.status : null;
    try {
      const dataset = runtime?.publishedDataset;
      await bounded(navigator.clipboard.writeText(JSON.stringify({
        backend: origin,
        extensionVersion: chrome.runtime.getManifest().version_name ?? chrome.runtime.getManifest().version,
        status: reply?.ok ? runtime?.reason : reply?.reason ?? "network",
        runtime: runtime ? {
          softwareVersion: runtime.softwareVersion, apiContract: runtime.apiContract,
          mode: runtime.mode, ready: runtime.ready, reason: runtime.reason, datasetVersion: runtime.datasetVersion,
          publishedDataset: dataset ? { releaseId: dataset.releaseId, preparationId: dataset.preparationId,
            ruleId: dataset.ruleId, sqlVersion: dataset.sqlVersion, sha256: dataset.sha256 } : null,
        } : null,
      }, null, 2)));
      copyStatus.textContent = "已复制诊断信息。";
    } catch { copyStatus.textContent = "复制失败，请重试。"; }
    finally { copy.disabled = false; }
  });
  const initial = ++generation;
  void (async () => {
    try {
      const value = await bounded(chrome.runtime.sendMessage({ type: "runtime-status" })) as Reply | undefined;
      if (initial === generation) render(value);
    } catch { if (initial === generation) render(); }
  })();
}
