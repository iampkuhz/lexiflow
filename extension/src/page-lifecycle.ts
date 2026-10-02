/** 页面身份、增强开关与页面级失效事件的唯一所有者。 */
export type LifecycleSource = {
  currentVideoId(): string | undefined;
  hasPlayer(): boolean;
  hasCaptionMotion(): boolean;
};
export type PageLifecycle = {
  get pageKey(): string;
  get videoId(): string | undefined;
  get generation(): number;
  get enabled(): boolean;
  get navigating(): boolean;
  get seeking(): boolean;
  get stopped(): boolean;
  isCurrent(generation: number, videoId: string | undefined): boolean;
  setEnabled(pageKey: string, value: boolean): boolean;
  refreshPage(): void;
  attach(): void;
  dispose(): void;
};

export function createPageLifecycle(source: LifecycleSource, invalidate: () => void, capture: () => void,
  onStateChange: (reason?: "disabled" | "source_hidden" | "navigation" | "interrupted") => void, createKey: () => string = () => crypto.randomUUID()): PageLifecycle {
  let pageKey = createKey(), videoId = source.currentVideoId(), generation = 0;
  let enabled = true, navigating = false, seeking = false, stopped = false;
  let lastDocumentHidden = document.hidden;
  let attached = false, disposed = false, motionFrame: number | undefined, motionDeadline = 0;
  let observer: MutationObserver | undefined;
  const listeners: Array<() => void> = [];
  const listen = (target: EventTarget, name: string, callback: EventListener, options?: AddEventListenerOptions | boolean) => {
    target.addEventListener(name, callback, options);
    listeners.push(() => target.removeEventListener(name, callback, options));
  };
  const cancelMotion = () => {
    if (motionFrame !== undefined) cancelAnimationFrame(motionFrame);
    motionFrame = undefined; motionDeadline = 0;
  };
  const notify = (reason?: "disabled" | "source_hidden" | "navigation" | "interrupted") => { try { onStateChange(reason); } catch { /* observations cannot alter lifecycle */ } };
  const resetForLocation = () => {
    generation++; enabled = true; seeking = false; stopped = false; cancelMotion();
    pageKey = createKey(); videoId = source.currentVideoId(); invalidate(); notify("navigation");
  };
  const motion = () => {
    motionFrame = undefined;
    if (disposed || document.hidden || !enabled || navigating || seeking || stopped) return;
    capture();
    if (performance.now() < motionDeadline && source.hasCaptionMotion()) motionFrame = requestAnimationFrame(motion);
  };
  const scheduleMotion = (event: Event) => {
    const target = event.target;
    if (!(target instanceof Element) || !target.closest("#ytp-caption-window-container")) return;
    motionDeadline = performance.now() + 2000;
    if (motionFrame === undefined) motionFrame = requestAnimationFrame(motion);
  };
  const endMotion = (event: Event) => {
    const target = event.target;
    if (target instanceof Element && target.closest("#ytp-caption-window-container")) capture();
  };
  const visibility = () => {
    const changed = lastDocumentHidden !== document.hidden;
    lastDocumentHidden = document.hidden;
    if (document.hidden) { cancelMotion(); generation++; invalidate(); notify(changed ? "source_hidden" : undefined); }
    else capture();
  };
  const pagehide = () => { cancelMotion(); stopped = true; generation++; invalidate(); notify("interrupted"); };
  const pageshow = () => { stopped = false; generation++; invalidate(); capture(); };
  const seekingStart = () => { cancelMotion(); seeking = true; generation++; invalidate(); notify("interrupted"); };
  const seekingEnd = () => { seeking = false; stopped = false; capture(); };
  const stopSource = () => { cancelMotion(); stopped = true; generation++; invalidate(); notify("interrupted"); };
  const resumeSource = () => { stopped = false; capture(); };
  const navigateStart = () => { if (navigating) return; navigating = true; resetForLocation(); };
  const navigateEnd = () => {
    const currentVideoId = source.currentVideoId();
    if (navigating && currentVideoId !== videoId) { videoId = currentVideoId; generation++; invalidate(); }
    navigating = false; capture();
  };
  const popstate = () => { if (navigating) return; resetForLocation(); capture(); };
  const resize = () => capture();
  const timeupdate = () => capture();
  function attach(): void {
    if (attached || disposed) return;
    attached = true;
    listen(document, "yt-navigate-start", navigateStart);
    listen(document, "yt-navigate-finish", navigateEnd);
    listen(window, "popstate", popstate);
    listen(window, "resize", resize);
    listen(document, "timeupdate", timeupdate, true);
    listen(document, "visibilitychange", visibility);
    listen(window, "pagehide", pagehide);
    listen(window, "pageshow", pageshow);
    listen(document, "seeking", seekingStart, true);
    listen(document, "seeked", seekingEnd, true);
    listen(document, "play", resumeSource, true);
    listen(document, "emptied", stopSource, true);
    listen(document, "ended", stopSource, true);
    for (const name of ["transitionrun", "animationstart"]) listen(document, name, scheduleMotion, true);
    for (const name of ["transitionend", "transitioncancel", "animationend", "animationcancel"]) listen(document, name, endMotion, true);
    observer = new MutationObserver(capture);
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ["class", "style", "hidden", "aria-hidden"] });
    capture();
  }
  function dispose(): void {
    if (disposed) return;
    disposed = true; stopped = true; enabled = false; generation++;
    observer?.disconnect(); observer = undefined;
    for (const remove of listeners.splice(0)) remove();
    cancelMotion(); attached = false; invalidate(); notify("interrupted");
  }
  return {
    get pageKey() { return pageKey; }, get videoId() { return videoId; }, get generation() { return generation; },
    get enabled() { return enabled; }, get navigating() { return navigating; }, get seeking() { return seeking; },
    get stopped() { return stopped; },
    isCurrent(expectedGeneration, expectedVideoId) {
      return !disposed && generation === expectedGeneration && videoId === expectedVideoId &&
        videoId === source.currentVideoId() && enabled && !navigating && !seeking && !stopped && !document.hidden && source.hasPlayer();
    },
    setEnabled(expectedPageKey, value) {
      if (disposed || expectedPageKey !== pageKey || typeof value !== "boolean") return false;
      if (enabled !== value) { enabled = value; generation++; invalidate(); notify(value ? undefined : "disabled"); }
      return true;
    },
    refreshPage() { resetForLocation(); }, attach, dispose
  };
}
