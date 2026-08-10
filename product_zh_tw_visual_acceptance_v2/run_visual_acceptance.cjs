const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/Owner/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

const root = 'D:/Lotto/lotto-lab-staging';
const output = path.join(root, 'product_zh_tw_visual_acceptance_v2');
const ui = 'file:///' + path.join(root, 'lotto-lab-web/public/index.html').replaceAll('\\', '/');
const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const tw = JSON.parse(fs.readFileSync(path.join(root, 'lotto-lab-web/public/taiwan_539_history.json'), 'utf8'))[0];
const fantasyRows = JSON.parse(fs.readFileSync(path.join(root, 'lotto-lab-web/data/ca_fantasy5_database.json'), 'utf8'));
const f5 = fantasyRows[fantasyRows.length - 1];
const views = {
  desktop: { viewport: { width: 1440, height: 1000 }, pages: ['overview','tw539','fantasy5','brain','evidence','knowledge','notifications','system'] },
  mobile: { viewport: { width: 390, height: 844 }, pages: ['overview','tw539','fantasy5','brain','notifications'] },
  tablet: { viewport: { width: 768, height: 1024 }, pages: ['overview','tw539','brain'] },
};
const result = { engine: 'Playwright + installed Google Chrome', rendered_at: new Date().toISOString(), views: {}, totals: {} };

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: chrome });
  for (const [kind, spec] of Object.entries(views)) {
    fs.mkdirSync(path.join(output, kind), { recursive: true });
    const context = await browser.newContext({ viewport: spec.viewport, locale: 'zh-TW', timezoneId: 'Asia/Taipei', serviceWorkers: 'block' });
    const page = await context.newPage();
    await page.route('**/api/latest?game=tw539', route => route.fulfill({ json: { latest: { ...tw, ranking: Array.from({length:39},(_,i)=>i+1) } } }));
    await page.route('**/api/latest?game=ca-fantasy5', route => route.fulfill({ json: { latest: f5 } }));
    await page.route('**/api/config', route => route.fulfill({ json: { notifications: { serverReady:true, queueReady:true, subscriberCount:0, publicKey:'validation-public-key' } } }));
    await page.goto(ui, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(350);
    result.views[kind] = [];
    for (const name of spec.pages) {
      await page.locator(`[data-page="${name}"]`).click();
      await page.waitForTimeout(120);
      const measurements = await page.evaluate(() => {
        const visible = el => { const s=getComputedStyle(el), r=el.getBoundingClientRect(); return s.display!=='none' && s.visibility!=='hidden' && r.width>0 && r.height>0; };
        const overflow = [...document.querySelectorAll('main *')].filter(el => visible(el) && el.scrollWidth > el.clientWidth + 1).map(el => ({tag:el.tagName,id:el.id,cls:el.className,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,text:(el.innerText||'').trim().slice(0,80)}));
        const clipped = [...document.querySelectorAll('h1,h2,h3,.nav-item,.status-grid b,.knowledge-grid h3,.knowledge-grid small,#notificationReality')].filter(el => visible(el) && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)).map(el => ({tag:el.tagName,id:el.id,text:(el.innerText||'').trim()}));
        const text = document.querySelector('.page.active')?.innerText || '';
        const enums = ['SLEEPING','PAUSED','OPEN_RQ','LOW_MATERIALITY','DATA_QUALITY_BLOCKED','NO_EDGE_FOUND'].filter(v => text.includes(v));
        const bodyStyle = getComputedStyle(document.body);
        const tofu = (text.match(/\uFFFD/g)||[]).length;
        return {scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,overflow,clipped,enums,fontFamily:bodyStyle.fontFamily,tofu,visibleText:text};
      });
      await page.screenshot({ path: path.join(output, kind, `${name}.png`), fullPage: true });
      result.views[kind].push({ page:name, ...measurements });
    }
    await context.close();
  }
  await browser.close();
  const all = Object.values(result.views).flat();
  result.totals = {
    pages: all.length,
    horizontal_overflow_pages: all.filter(x => x.scrollWidth > x.clientWidth).length,
    overflow_elements: all.reduce((n,x)=>n+x.overflow.length,0),
    clipped_elements: all.reduce((n,x)=>n+x.clipped.length,0),
    enum_residue: all.reduce((n,x)=>n+x.enums.length,0),
    tofu_characters: all.reduce((n,x)=>n+x.tofu,0),
  };
  fs.writeFileSync(path.join(output, 'raw_render_result.json'), JSON.stringify(result,null,2));
  console.log(JSON.stringify(result.totals));
})().catch(error => { console.error(error); process.exit(1); });
