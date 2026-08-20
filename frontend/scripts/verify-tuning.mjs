// Dev-only visual check of the tuning panel (not part of the build/test suite).
// Requires the backend (:8000) + vite (:5173) running. Screenshots to /tmp.
import { chromium } from 'playwright';

const BASE = process.env.BASE ?? 'http://localhost:5173';
const VIDEO = process.env.VIDEO ?? 'z-mxBDuRaZ8';
const OUT = process.env.OUT ?? '/tmp/tuning';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(`${BASE}/#/p/${VIDEO}`, { waitUntil: 'domcontentloaded' });

// Wait for the score + top bar to render (project view is up).
await page.waitForSelector('#tune-toggle', { timeout: 15000 });
// The Tune toggle is wired only after playback setup resolves; give it a beat.
await page.waitForTimeout(4000);

async function shot(name) {
  await page.screenshot({ path: `${OUT}-${name}.png`, fullPage: false });
  console.log(`shot ${name}`);
}

await shot('01-closed');

// Open the panel.
await page.click('#tune-toggle');
await page.waitForSelector('#tuning-panel:not([hidden])', { timeout: 5000 }).catch(() => {});
await page.waitForTimeout(800); // diagnostics fetch
const panelVisibleOpen = await page.isVisible('#tuning-panel');
await shot('02-open');

// Drag the first slider to ~mid to check usability + overridden highlight.
const slider = page.locator('.knob-ctl input[type="range"]').first();
if (await slider.count()) {
  const box = await slider.boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width * 0.2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height / 2, { steps: 8 });
    await page.mouse.up();
  }
}
await shot('03-slider-moved');

// Reset-all clears the override highlight.
await page.click('button:has-text("Reset all")').catch(() => {});
await page.waitForTimeout(200);
await shot('04-reset');

// Close via the × ; confirm it actually hides.
await page.click('.tuning-close');
await page.waitForTimeout(300);
const panelVisibleAfterClose = await page.isVisible('#tuning-panel');

// Re-open then close via the Tune toggle to confirm that path too.
await page.click('#tune-toggle');
await page.waitForTimeout(200);
await page.click('#tune-toggle');
await page.waitForTimeout(200);
const panelVisibleAfterToggleClose = await page.isVisible('#tuning-panel');

console.log(JSON.stringify({
  panelVisibleOpen,
  panelVisibleAfterClose,
  panelVisibleAfterToggleClose,
  consoleErrors: errors.slice(0, 20),
}, null, 2));

await browser.close();
