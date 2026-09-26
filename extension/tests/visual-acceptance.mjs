import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const OVERLAY = "#lexiflow-caption-overlay";
const PLAYER = "#player";
const VISUAL_STYLE_ID = "lexiflow-visual-acceptance-style";
const FULLSCREEN_BUTTON_ID = "lexiflow-visual-acceptance-fullscreen";

const normalCaption = "We need reliable captions.";
const narrowCaption = "We need reliable captions in a narrow player.";
const fullscreenCaption = "A reliable caption remains readable in full screen.";
const zoomCaption = "Reliable wording remains readable with enlarged text.";
const longCaption = [
  "We need reliable captions that preserve the original English wording while keeping a helpful explanation nearby.",
  "This intentionally long synthetic sentence verifies wrapping without changing the published caption text."
].join(" ");
const mockCaption = "Reliable context needs published captions.";

const mockHints = [
  ["Reliable context", "可靠的语境"],
  ["published", "已发布的"],
  ["captions", "字幕"]
];

function safeCaseId(value) {
  return value.replace(/[^a-z0-9-]/gi, "-");
}

/**
 * Exercises only the routed local HTML fixture.  Actual API cases retain the parent test's
 * service-worker fetch routing; the three-hint case is explicitly a renderer-contract mock.
 */
export async function runVisualAcceptance({ page, serviceWorker, apiBase, artifactRoot, setCaption, waitForState, overlayText }) {
  assert.ok(page && serviceWorker && apiBase && artifactRoot, "visual acceptance requires the routed fixture and artifact root");
  assert.equal(typeof setCaption, "function");
  assert.equal(typeof waitForState, "function");
  assert.equal(typeof overlayText, "function");

  const initialViewport = page.viewportSize();
  const initialFixtureState = await page.evaluate(() => ({
    caption: document.querySelector(".ytp-caption-segment")?.textContent ?? "",
    time: Number(document.querySelector("video")?.currentTime ?? 0),
    overlayState: document.querySelector("#lexiflow-caption-overlay")?.getAttribute("data-lexiflow-state")
  }));
  const cases = [];
  let styleInstalled = false;
  let fullscreenEntered = false;
  let fetchMockInstalled = false;

  await mkdir(artifactRoot, { recursive: true });
  try {
    await installFixturePresentation(page);
    styleInstalled = true;

    await runCase({
      id: "normal-window", page, artifactRoot, setCaption, waitForState, overlayText, cases,
      viewport: { width: 1280, height: 800 }, caption: normalCaption, time: 20, expectedHints: 1, inputMode: "actual-local-api"
    });
    await assertControlsUsable(page);

    await runCase({
      id: "narrow-window", page, artifactRoot, setCaption, waitForState, overlayText, cases,
      viewport: { width: 480, height: 800 }, caption: narrowCaption, time: 21, expectedHints: 1, inputMode: "actual-local-api"
    });

    await enterFixtureFullscreen(page);
    fullscreenEntered = true;
    await runCase({
      id: "fullscreen", page, artifactRoot, setCaption, waitForState, overlayText, cases,
      caption: fullscreenCaption, time: 22, expectedHints: 1, inputMode: "actual-local-api", fullscreen: true
    });
    await page.evaluate(async () => { if (document.fullscreenElement) await document.exitFullscreen(); });
    await page.waitForFunction(() => !document.fullscreenElement);
    fullscreenEntered = false;

    await page.evaluate(() => document.documentElement.dataset.lexiflowVisualTextScale = "150");
    await runCase({
      id: "text-zoom", page, artifactRoot, setCaption, waitForState, overlayText, cases,
      viewport: { width: 960, height: 760 }, caption: zoomCaption, time: 23, expectedHints: 1, inputMode: "actual-local-api", minimumFontSize: 30
    });
    await page.evaluate(() => { delete document.documentElement.dataset.lexiflowVisualTextScale; });

    await runCase({
      id: "long-caption", page, artifactRoot, setCaption, waitForState, overlayText, cases,
      viewport: { width: 960, height: 760 }, caption: longCaption, time: 24, expectedHints: 1, inputMode: "actual-local-api"
    });

    await installThreeHintRendererMock(serviceWorker, apiBase, mockCaption, mockHints);
    fetchMockInstalled = true;
    await runCase({
      id: "three-renderer-hints", page, artifactRoot, setCaption, waitForState, overlayText, cases,
      viewport: { width: 960, height: 760 }, caption: mockCaption, time: 25, expectedHints: 3,
      inputMode: "renderer-contract-mock", expectedTerms: mockHints.map(([term]) => term)
    });
    await restoreThreeHintRendererMock(serviceWorker);
    fetchMockInstalled = false;

    return {
      cases,
      limitations: [
        "All cases use the existing route-fulfilled local HTML fixture; no real website or user browser is accessed.",
        "normal-window, narrow-window, fullscreen, text-zoom, and long-caption use the existing local API route.",
        "three-renderer-hints is a service-worker renderer-contract mock because the built-in API only safely supplies the reliable hint; it is not real API multi-hint coverage.",
        "Screenshots contain only synthetic captions and are written under the caller-provided ignored artifact root."
      ]
    };
  } finally {
    if (fetchMockInstalled) await restoreThreeHintRendererMock(serviceWorker);
    if (initialFixtureState.caption && (initialFixtureState.overlayState === "ready" || initialFixtureState.overlayState === "no-pending")) {
      await setCaption(page, initialFixtureState.caption, initialFixtureState.time);
      await waitForState(page, initialFixtureState.overlayState);
    }
    if (fullscreenEntered) {
      await page.evaluate(async () => { if (document.fullscreenElement) await document.exitFullscreen(); });
    }
    if (styleInstalled) {
      await page.evaluate(({ styleId, buttonId }) => {
        document.getElementById(styleId)?.remove();
        document.getElementById(buttonId)?.remove();
        delete document.documentElement.dataset.lexiflowVisualTextScale;
      }, { styleId: VISUAL_STYLE_ID, buttonId: FULLSCREEN_BUTTON_ID });
    }
    if (initialViewport) await page.setViewportSize(initialViewport);
  }
}

async function installFixturePresentation(page) {
  await page.evaluate(({ styleId, buttonId }) => {
    document.getElementById(styleId)?.remove();
    document.getElementById(buttonId)?.remove();
    const style = document.createElement("style");
    style.id = styleId;
    style.textContent = `
      #player { width:min(960px, calc(100vw - 16px)) !important; height:auto !important; aspect-ratio:16 / 9; margin:0 auto; }
      #player:fullscreen { width:100vw !important; height:100vh !important; aspect-ratio:auto; }
      html[data-lexiflow-visual-text-scale="150"] #ytp-caption-window-container { font-size:36px !important; }
      #${buttonId} { position:absolute; right:8px; top:8px; z-index:2147483647; }
    `;
    document.head.append(style);
    const player = document.querySelector("#player");
    if (!player) throw new Error("visual-fixture-player-missing");
    const button = document.createElement("button");
    button.id = buttonId;
    button.type = "button";
    button.textContent = "Visual acceptance full screen";
    button.addEventListener("click", () => {
      void player.requestFullscreen().catch(error => { button.dataset.fullscreenError = String(error?.name ?? error); });
    });
    player.append(button);
  }, { styleId: VISUAL_STYLE_ID, buttonId: FULLSCREEN_BUTTON_ID });
}

async function enterFixtureFullscreen(page) {
  await page.locator(`#${FULLSCREEN_BUTTON_ID}`).click();
  await page.waitForFunction(() => Boolean(document.fullscreenElement), undefined, { timeout: 10_000 });
  const isFixturePlayer = await page.evaluate(() => document.fullscreenElement === document.querySelector("#player"));
  assert.equal(isFixturePlayer, true, "the synthetic fixture player must own fullscreen");
}

async function runCase({
  id, page, artifactRoot, setCaption, waitForState, overlayText, cases, viewport, caption, time, expectedHints, inputMode, expectedTerms, fullscreen = false,
  minimumFontSize = 0
}) {
  if (viewport) await page.setViewportSize(viewport);
  await setCaption(page, caption, time);
  await waitForState(page, "ready");
  const evidence = await collectGeometry(page, caption, expectedHints, expectedTerms, minimumFontSize);
  assert.equal(evidence.englishText, caption, `${id}: original English must remain complete`);
  assert.equal(evidence.glosses.length, expectedHints, `${id}: hint count`);
  assert.equal(evidence.adjacentGlosses, true, `${id}: Chinese must immediately follow its English segment`);
  assert.deepEqual(evidence.markedTerms, evidence.glosses.map(gloss => gloss.term), `${id}: each complete hinted span, including phrase spaces, must be underlined`);
  assert.equal(evidence.allHintsUnderlined, true, `${id}: hinted spans must retain visible underline styling`);
  assert.equal(evidence.withinPlayer, true, `${id}: overlay must stay within the player`);
  assert.equal(evidence.noControlCollision, true, `${id}: overlay must not collide with controls`);
  assert.equal(evidence.noGlossCollision, true, `${id}: glosses must not overlap each other`);
  assert.equal(evidence.sourceMasked, true, `${id}: original caption must be clipped while inline overlay is active`);
  assert.equal(evidence.controls.visible, true, `${id}: controls must remain visible`);
  assert.equal(evidence.controls.enabled, true, `${id}: controls must remain enabled`);
  assert.ok(evidence.fontSize >= minimumFontSize, `${id}: expected text scale was not applied`);
  if (fullscreen) assert.equal(evidence.fullscreen, true, `${id}: expected fullscreen fixture`);

  const screenshot = resolve(artifactRoot, `${safeCaseId(id)}.png`);
  await page.locator(PLAYER).screenshot({ path: screenshot });
  const renderedText = await overlayText(page);
  assert.equal(renderedText, evidence.overlayText, `${id}: overlay reader must agree with DOM evidence`);
  cases.push({ id, inputMode, viewport: await page.viewportSize(), fullscreen: evidence.fullscreen, overlays: expectedHints, geometry: evidence, screenshot });
}

async function collectGeometry(page, caption, expectedHints, expectedTerms, minimumFontSize) {
  return page.evaluate(({ captionValue, expectedHintCount, terms, minimumSize }) => {
    const asRect = (value) => ({
      x: Math.round(value.x), y: Math.round(value.y), width: Math.round(value.width), height: Math.round(value.height),
      top: Math.round(value.top), right: Math.round(value.right), bottom: Math.round(value.bottom), left: Math.round(value.left)
    });
    const player = document.querySelector("#player");
    const host = document.querySelector("#lexiflow-caption-overlay");
    const source = document.querySelector(".ytp-caption-segment");
    const controls = document.querySelector("#lexiflow-controls");
    if (!(player instanceof HTMLElement) || !(host instanceof HTMLElement) || !(source instanceof HTMLElement) || !(controls instanceof HTMLElement)) {
      throw new Error("visual-fixture-overlay-or-controls-missing");
    }
    const root = host.shadowRoot;
    const controlRoot = controls.shadowRoot;
    const line = root?.querySelector(".line");
    const summary = controlRoot?.querySelector("summary");
    const buttons = [...(controlRoot?.querySelectorAll("button") ?? [])];
    if (!(line instanceof HTMLElement) || !(summary instanceof HTMLElement)) throw new Error("visual-shadow-content-missing");
    const children = [...line.children];
    const glosses = children.filter(child => child.classList.contains("gloss"));
    const markedTerms = children.filter(child => child.classList.contains("hint-term")).map(child => child.textContent ?? "");
    const allHintsUnderlined = children.filter(child => child.classList.contains("hint-term"))
      .every(term => getComputedStyle(term).borderBottomStyle === "solid" && parseFloat(getComputedStyle(term).borderBottomWidth) > 0);
    const englishText = children.filter(child => !child.classList.contains("gloss")).map(child => child.textContent ?? "").join("");
    const termsFromTitle = glosses.map(gloss => /^不再提示「(.+?)」/.exec(gloss.getAttribute("title") ?? "")?.[1] ?? "");
    const adjacentGlosses = glosses.every((gloss, index) => {
      const previous = gloss.previousElementSibling;
      const term = terms?.[index] ?? termsFromTitle[index];
      return previous?.tagName === "SPAN" && Boolean(term) && (previous.textContent ?? "").endsWith(term);
    });
    const playerRect = asRect(player.getBoundingClientRect());
    const lineRect = asRect(line.getBoundingClientRect());
    const controlRect = asRect(controls.getBoundingClientRect());
    const glossRects = glosses.map(gloss => asRect(gloss.getBoundingClientRect()));
    const inside = (box) => box.left >= playerRect.left - 1 && box.right <= playerRect.right + 1 &&
      box.top >= playerRect.top - 1 && box.bottom <= playerRect.bottom + 1;
    const disjoint = (left, right) => left.right <= right.left || right.right <= left.left || left.bottom <= right.top || right.bottom <= left.top;
    const visible = (element) => {
      const box = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return box.width > 0 && box.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    return {
      caption: captionValue,
      overlayText: line.textContent?.trim() ?? "",
      englishText,
      glosses: glosses.map((gloss, index) => ({ term: terms?.[index] ?? termsFromTitle[index], text: gloss.textContent ?? "", rect: glossRects[index] })),
      markedTerms,
      allHintsUnderlined,
      player: playerRect,
      line: lineRect,
      controls: { rect: controlRect, visible: visible(summary), enabled: !buttons.some(button => button.disabled) },
      sourceMasked: /inset/.test(getComputedStyle(source.parentElement ?? source).clipPath),
      adjacentGlosses,
      withinPlayer: [lineRect, controlRect, ...glossRects].every(inside),
      noControlCollision: disjoint(lineRect, controlRect),
      noGlossCollision: glossRects.every((box, index) => glossRects.slice(index + 1).every(other => disjoint(box, other))),
      expectedHints: expectedHintCount,
      fontSize: Number.parseFloat(getComputedStyle(line).fontSize) || 0,
      fullscreen: document.fullscreenElement === player,
      minimumFontSize: minimumSize
    };
  }, { captionValue: caption, expectedHintCount: expectedHints, terms: expectedTerms, minimumSize: minimumFontSize });
}

async function assertControlsUsable(page) {
  await page.evaluate(() => {
    const details = document.querySelector("#lexiflow-controls")?.shadowRoot?.querySelector("details");
    if (details instanceof HTMLDetailsElement) details.open = false;
  });
  await page.getByLabel("LexiFlow 设置与诊断").click();
  const opened = await page.evaluate(() => document.querySelector("#lexiflow-controls")?.shadowRoot?.querySelector("details")?.open === true);
  assert.equal(opened, true, "visual controls must respond to a user click");
  await page.getByLabel("LexiFlow 设置与诊断").click();
}

async function installThreeHintRendererMock(serviceWorker, apiBase, caption, terms) {
  const hints = terms.map(([term, chineseGloss], index) => ({
    startOffset: caption.indexOf(term), endOffset: caption.indexOf(term) + term.length, chineseGloss,
    lexiconEntryId: `00000000-0000-0000-0000-00000000000${index + 1}`,
    lexiconVersion: 1,
    senseId: `00000000-0000-0000-0000-00000000001${index + 1}`
  }));
  assert.equal(hints.length, 3);
  assert.ok(hints.every(hint => hint.startOffset >= 0), "mock terms must occur in the synthetic caption");
  await serviceWorker.evaluate(({ base, syntheticCaption, syntheticHints }) => {
    if (globalThis.__lexiflowVisualAcceptanceRestoreFetch) throw new Error("visual-fetch-mock-already-installed");
    const originalFetch = globalThis.fetch;
    const target = `${base}/api/v1/caption-hints`;
    globalThis.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input?.url;
      if (url === target || String(url ?? "").includes("/api/v1/caption-hints")) {
        let requestedCaption;
        try { requestedCaption = JSON.parse(typeof init?.body === "string" ? init.body : "{}").caption; } catch { /* invalid requests retain normal parser behavior */ }
        const body = requestedCaption === syntheticCaption
          ? { caption: syntheticCaption, state: "READY", hints: syntheticHints }
          : { caption: typeof requestedCaption === "string" && requestedCaption ? requestedCaption : syntheticCaption, state: "NO_PENDING", hints: [] };
        return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return originalFetch(input, init);
    };
    globalThis.__lexiflowVisualAcceptanceRestoreFetch = () => {
      globalThis.fetch = originalFetch;
      delete globalThis.__lexiflowVisualAcceptanceRestoreFetch;
    };
  }, { base: apiBase, syntheticCaption: caption, syntheticHints: hints });
}

async function restoreThreeHintRendererMock(serviceWorker) {
  await serviceWorker.evaluate(() => globalThis.__lexiflowVisualAcceptanceRestoreFetch?.());
}
