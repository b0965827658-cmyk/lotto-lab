const { chromium } = require('C:/Users/Owner/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const history = Array.from({ length: 90 }, (_, i) => ({ game: 'tw539', period: String(115000192 - i), date: `2026-${String(8 - Math.floor(i / 28)).padStart(2,'0')}-${String(28 - (i % 28)).padStart(2,'0')}`, numbers: [1 + i % 35, 2 + i % 35, 3 + i % 35, 4 + i % 35, 5 + i % 35].map(n => ((n - 1) % 39) + 1) }));
const journal = [{ status: 'closed', targetDate: '2026-08-08', snapshot: { top5: [8,11,25,29,36], top10: [6,8,11,14,25,28,29,34,36,37], full15: [2,4,6,7,8,11,12,14,25,28,29,31,34,36,37] }, outcome: { period: '115000192', date: '2026-08-08', numbers: [5,11,24,31,32], hits5: 1, hits10: 1, hits15: 2 } }];
const pages = [
  ['tw539','tw-cold-hot'], ['tw539','tw-history'], ['tw539','tw-validation'], ['tw539','tw-match'], ['tw539','tw-guide'],
  ['fantasy5','f5-cold-hot'], ['fantasy5','f5-history'], ['fantasy5','f5-validation'], ['fantasy5','f5-match'], ['fantasy5','f5-guide']
];

(async () => {
  for (const dir of ['desktop','mobile','tablet']) fs.mkdirSync(path.join(root, dir), { recursive: true });
  const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
  const results = {};
  for (const [name, viewport] of Object.entries({ desktop: { width: 1440, height: 1000 }, mobile: { width: 390, height: 844 }, tablet: { width: 768, height: 1024 } })) {
    const context = await browser.newContext({ viewport, serviceWorkers: 'block' });
    const page = await context.newPage();
    page.on('pageerror', error => console.error(name, 'PAGEERROR', error.message));
    page.on('console', message => { if (message.type() === 'error') console.error(name, 'CONSOLE', message.text()); });
    await page.route('**/*', route => {
      const url = route.request().url();
      if (url.includes('/api/latest')) return route.fulfill({ json: { latest: { period: '115000192', date: '2026-08-08', numbers: [5,11,24,31,32] } } });
      if (url.includes('/api/config')) return route.fulfill({ json: { notifications: { serverReady: true, queueReady: true, subscriberCount: 0 } } });
      if (url.includes('/api/history-search')) return route.fulfill({ json: { ok: true, history } });
      if (url.includes('/api/prediction-journal')) return route.fulfill({ json: { ok: true, records: journal } });
      return route.continue();
    });
    const checks = [];
    for (const [main, feature] of pages) {
      await page.goto(`http://127.0.0.1:8766/#${main}`, { waitUntil: 'networkidle' });
      await page.evaluate((pageName) => activate(pageName), main);
      await page.locator(`[data-feature="${feature}"]`).click();
      await page.waitForTimeout(100);
      const metrics = await page.evaluate(() => ({
        width: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        visiblePanel: !!document.querySelector('[data-feature-panel].active'),
        errors: document.body.innerText.includes('undefined') || document.body.innerText.includes('null'),
        missing: document.body.innerText.includes('資料暫時無法載入')
      }));
      checks.push({ feature, ...metrics, pass: metrics.scrollWidth <= metrics.width && metrics.visiblePanel && !metrics.errors && !metrics.missing });
      await page.screenshot({ path: path.join(root, name, `${feature}.png`), fullPage: true });
    }
    results[name] = { viewport, checks, pass: checks.every(row => row.pass), overflowCount: checks.filter(row => row.scrollWidth > row.width).length };
    await context.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(root, 'browser_results.json'), JSON.stringify(results, null, 2));
  if (!Object.values(results).every(row => row.pass)) process.exitCode = 1;
})();
