// Dev-only visual check of the column (beat) editor. Requires backend (:8000) +
// vite (:5173) running with a transcribed project. Screenshots to /tmp/edit-*.png.
import { chromium } from 'playwright';

const BASE = process.env.BASE ?? 'http://localhost:5173';
const VIDEO = process.env.VIDEO ?? 'z-mxBDuRaZ8';
const OUT = process.env.OUT ?? '/tmp/edit';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));

await page.goto(`${BASE}/#/p/${VIDEO}`, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#edit-toggle', { timeout: 15000 });
await page.waitForTimeout(4000); // wait for playback wiring so edit-toggle is live

async function shot(n) { await page.screenshot({ path: `${OUT}-${n}.png` }); console.log('shot', n); }

// Enter edit mode.
await page.click('#edit-toggle');
await page.waitForTimeout(600);

// Click into a groove bar (later bars have notes). Pick the 10th bar-svg and
// click ~20% across, mid-height — a column that should carry hi-hat + kick.
const bars = page.locator('.bar-svg');
const n = await bars.count();
const target = bars.nth(Math.min(9, n - 1));
const box = await target.boundingBox();
await page.mouse.click(box.x + box.width * 0.2, box.y + box.height * 0.45);
await page.waitForSelector('.col-popover', { timeout: 5000 }).catch(() => {});
await page.waitForTimeout(300);
await shot('01-beat-editor');

const info = {};
info.popoverOpen = await page.isVisible('.col-popover');
info.rows = await page.locator('.col-popover-row').count();
info.addButtons = await page.locator('.col-popover-add button').count();
info.hasHihatToggle = await page.locator('.col-seg').count();

// Add a crash (simultaneous hit on this column).
const addCrash = page.locator('.col-popover-add button', { hasText: 'Crash' });
if (await addCrash.count()) { await addCrash.first().click(); await page.waitForTimeout(300); }
await shot('02-added-crash');
info.rowsAfterAdd = await page.locator('.col-popover-row').count();

// Toggle hi-hat open, if a hi-hat row is present.
const openBtn = page.locator('.col-seg button', { hasText: 'open' });
if (await openBtn.count()) { await openBtn.first().click(); await page.waitForTimeout(300); }
await shot('03-hihat-open');
info.hihatOpenActive = await page.locator('.col-seg button.active', { hasText: 'open' }).count();

// Delete the first present note.
const del = page.locator('.col-popover-row button.danger');
info.rowsBeforeDelete = await page.locator('.col-popover-row').count();
if (await del.count()) { await del.first().click(); await page.waitForTimeout(300); }
info.rowsAfterDelete = await page.locator('.col-popover-row').count();
await shot('04-after-delete');

info.consoleErrors = errors.slice(0, 20);
console.log(JSON.stringify(info, null, 2));
await browser.close();
