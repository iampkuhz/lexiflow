interface ReadResponse {
  ok: true;
  enabled: boolean;
  pageKey: string;
}

interface ReadFailure {
  ok: false;
}

type PageEnhancementRead = ReadResponse | ReadFailure;

interface SetResponse {
  ok: true;
  enabled: boolean;
  pageKey: string;
}

interface SetFailure {
  ok: false;
}

type PageEnhancementSet = SetResponse | SetFailure;

let currentPageKey: string | null = null;
let currentTabId: number | undefined;

function getElements() {
  return {
    checkbox: document.getElementById("enhance-toggle") as HTMLInputElement,
    status: document.getElementById("status") as HTMLDivElement,
  };
}

function disableWithMessage(message: string): void {
  const { checkbox, status } = getElements();
  checkbox.disabled = true;
  status.textContent = message;
  status.hidden = false;
}

async function readState(): Promise<void> {
  const { checkbox, status } = getElements();
  checkbox.disabled = true;
  status.hidden = true;

  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    if (!tab || typeof tab.id !== "number") {
      disableWithMessage("没有可用标签页，请打开 YouTube 视频页面。");
      return;
    }

    const response = await chrome.tabs.sendMessage(
      tab.id,
      { type: "page-enhancement", action: "read" },
    ) as PageEnhancementRead | undefined;

    if (!response || !response.ok || typeof response.enabled !== "boolean" || typeof response.pageKey !== "string" || !response.pageKey) {
      disableWithMessage("当前页面不可用，请刷新 YouTube 视频页面后重试。");
      return;
    }

    currentTabId = tab.id;
    currentPageKey = response.pageKey;
    checkbox.checked = response.enabled;
    checkbox.disabled = false;
    status.hidden = true;
  } catch {
    disableWithMessage("当前页面不可用，请刷新 YouTube 视频页面后重试。");
  }
}

async function handleToggle(): Promise<void> {
  const { checkbox, status } = getElements();

  if (!currentPageKey || currentTabId === undefined) return;

  const desired = checkbox.checked;
  checkbox.disabled = true;

  try {
    const response = await chrome.tabs.sendMessage(
      currentTabId,
      { type: "page-enhancement", action: "set", enabled: desired, pageKey: currentPageKey },
    ) as PageEnhancementSet | undefined;

    if (!response || !response.ok || typeof response.enabled !== "boolean" || typeof response.pageKey !== "string" || !response.pageKey) {
      checkbox.checked = !desired;
      disableWithMessage("设置未成功，请重新打开弹窗后重试。");
      return;
    }

    currentPageKey = response.pageKey;
    checkbox.checked = response.enabled;
    checkbox.disabled = false;
    status.hidden = true;
  } catch {
    checkbox.checked = !desired;
    disableWithMessage("设置未成功，请重新打开弹窗后重试。");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const { checkbox } = getElements();
  checkbox.addEventListener("change", () => { void handleToggle(); });
  void readState();
});
