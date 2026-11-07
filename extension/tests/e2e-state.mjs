/** Only bounded structural facts; never captions, glosses, payloads, or console logs. */
export async function readE2EState(page) {
  return page.evaluate(() => {
    const host = document.querySelector('#lexiflow-caption-overlay');
    const video = document.querySelector('video');
    let diagnostics = null;
    try { diagnostics = JSON.parse(host?.getAttribute('data-lexiflow-diagnostics') ?? 'null'); } catch {}
    return {
      state: host?.getAttribute('data-lexiflow-state') ?? null,
      hidden: document.hidden,
      focused: document.hasFocus(),
      videoTime: video?.currentTime ?? null,
      videoEnded: video?.ended ?? null,
      sourceLength: document.querySelector('#ytp-caption-window-container')?.textContent?.length ?? 0,
      overlayLength: host?.shadowRoot?.querySelector('.line')?.textContent?.length ?? 0,
      masked: document.querySelector('#player')?.classList.contains('lexiflow-inline-active') ?? false,
      counts: diagnostics?.counts ?? null
    };
  });
}

export async function waitForE2EState(page, state) {
  try {
    await page.waitForFunction(
      expected => document.querySelector('#lexiflow-caption-overlay')?.getAttribute('data-lexiflow-state') === expected,
      state, { timeout: 10_000 }
    );
  } catch (error) {
    let observed;
    try { observed = await readE2EState(page); } catch { observed = { unavailable: true }; }
    if (error instanceof Error) {
      const diagnostic = `synthetic E2E state: ${JSON.stringify({ expected: state, ...observed })}`;
      error.message += `\n${diagnostic}`;
      error.stack += `\n${diagnostic}`;
    }
    throw error;
  }
}
