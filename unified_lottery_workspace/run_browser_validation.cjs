const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..', 'lotto-lab-web', 'public');
const out = path.resolve(__dirname, 'screenshots');
fs.mkdirSync(out, { recursive: true });
const mime = { '.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.css':'text/css; charset=utf-8', '.json':'application/json', '.svg':'image/svg+xml', '.png':'image/png' };
const history = Array.from({length:60}, (_, i) => ({ period:String(115000160-i), date:`2026-08-${String(10-(i%9)).padStart(2,'0')}`, numbers:Array.from({length:5},(_,j)=>((i*5+j)%39)+1).sort((a,b)=>a-b) }));
const profiles = [['tw-bayesian','539 Bayesian 多視窗'],['tw-logistic','539 Logistic 結構模型'],['tw-boosted','539 Boosted 特徵模型'],['tw-markov','539 Markov 轉移模型']].map(([id,label],i)=>({id,label,testedCount:700,averageHit5:.67+i/100,averageHit15:1.90+i/100,hitRate15:93+i/10}));
const analysis = {ok:true,analysis:{backtest:{testedCount:700,averageHit5:.6686,averageHit15:1.9143,hitRate15:93.14,baselineComparison:{status:'尚未證明優於簡單基準'},baselineModels:{'random-expected':{averageHit15:1.9231}}},modelProfiles:profiles}};
const journal = {records:[{status:'closed',targetDate:'2026-08-09',snapshot:{full15:Array.from({length:15},(_,i)=>i+1)},outcome:{period:'115000159',date:'2026-08-09',numbers:[1,8,15,22,39],hits5:1,hits10:2,hits15:3}},{status:'open',draw_id:'NEXT-1',targetDate:'2026-08-11',snapshot:{full15:Array.from({length:15},(_,i)=>i+10)}}]};
const latest = {latest:{period:'115000160',date:'2026-08-10',numbers:[2,9,16,23,30],ranking:Array.from({length:39},(_,i)=>i+1)}};

const server = http.createServer((req,res) => {
  const clean = decodeURIComponent(req.url.split('?')[0]);
  if (clean.startsWith('/api/')) {
    const payload = clean === '/api/latest' ? latest : clean === '/api/config' ? {notifications:{serverReady:true,queueReady:true,subscriberCount:1,publicKey:'fixture'}} : clean === '/api/history-search' ? {history,total:history.length} : clean === '/api/prediction-journal' ? journal : clean === '/api/lottery' ? analysis : {ok:true};
    res.writeHead(200, {'content-type':'application/json; charset=utf-8'}); return res.end(JSON.stringify(payload));
  }
  const target = path.join(root, clean === '/' ? 'index.html' : clean.replace(/^\//,''));
  if (!target.startsWith(root) || !fs.existsSync(target) || fs.statSync(target).isDirectory()) { res.writeHead(404); return res.end('not found'); }
  res.writeHead(200, {'content-type':mime[path.extname(target)] || 'application/octet-stream'}); fs.createReadStream(target).pipe(res);
});

async function clickAll(page, prefix) {
  const selectors = prefix === 'tw' ? ['tw-analysis','tw-cold-hot','tw-validation','tw-history','tw-match','tw-saved','tw-research'] : ['f5-status','f5-cold-hot','f5-validation','f5-history','f5-match','f5-saved','f5-research'];
  for (const id of selectors) { await page.click(`[data-feature="${id}"]`); await page.waitForTimeout(30); }
  return selectors;
}

async function main() {
  await new Promise(resolve => server.listen(18766,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const results = {browser:'Playwright + Google Chrome', desktop:{}, mobile:{}, lifecycle:{}, errors:[]};
  for (const [mode, viewport] of Object.entries({desktop:{width:1440,height:1000},mobile:{width:390,height:844}})) {
    const context = await browser.newContext({viewport}); const page = await context.newPage();
    page.on('pageerror', error => results.errors.push(`${mode}:${error.message}`));
    await page.addInitScript(() => { localStorage.setItem('star-picks-tw', JSON.stringify([1,2,3,4,5])); });
    await page.goto('http://127.0.0.1:18766/', {waitUntil:'networkidle'});
    await page.click('[data-page="tw539"]');
    const tw = await clickAll(page,'tw');
    await page.click('[data-feature="tw-cold-hot"]'); await page.waitForSelector('[data-stat-number="39"]');
    const twUniverse = await page.locator('[data-stat-number]').evaluateAll(nodes => new Set(nodes.map(node => node.dataset.statNumber)).size);
    await page.click('[data-feature="tw-validation"]'); await page.waitForSelector('[data-backtest-group="5"]');
    const backtests = await page.locator('[data-backtest-group]').count();
    await page.click('[data-feature="tw-saved"]'); await page.waitForSelector('#twSavedNumbers .saved-create');
    const legacyMigrated = await page.locator('#twSavedNumbers').getByText('舊版未指定期別').count();
    for (const number of [1,7,13,21,39]) await page.click(`[data-saved-pick="tw"][data-number="${number}"]`);
    await page.click('[data-save-record="tw"]');
    const pendingText = await page.locator('#twSavedNumbers [data-saved-section="active"]').innerText();
    for (const number of [1,7,13,21,39]) await page.click(`[data-saved-pick="tw"][data-number="${number}"]`);
    await page.click('[data-save-record="tw"]');
    const duplicateText = await page.locator('[data-saved-state="tw"]').innerText();
    const activeBefore = await page.locator('#twSavedNumbers [data-saved-section="active"] .saved-record').count();
    const lifecycle = await page.evaluate(() => {
      const s = window.StarSavedNumbers;
      s.reconcile('tw539',[{period:'NEXT-1',numbers:[1,7,15,21,30]}]);
      const settled = s.load().find(r => r.target_draw_id === 'NEXT-1');
      s.reconcile('tw539',[{period:'NEXT-2',numbers:[2,8,16,22,31]},{period:'NEXT-1',numbers:[1,7,15,21,30]}]);
      const archived = s.load().find(r => r.target_draw_id === 'NEXT-1');
      return {settledStatus:settled.status,hitCount:settled.hit_count,matched:settled.matched_numbers,archivedStatus:archived.status,historyPreserved:Boolean(archived.actual_numbers.length && archived.settled_at && archived.archived_at)};
    });
    await page.click('[data-page="fantasy5"]'); const f5 = await clickAll(page,'f5');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const legacyEntry = await page.getByText('完整分析工具',{exact:true}).count();
    const researchLeak = await page.evaluate(() => JSON.stringify(localStorage).includes('research'));
    await page.screenshot({path:path.join(out,`${mode}-workspace.png`),fullPage:true});
    results[mode] = {status:overflow === 0 && tw.length === 7 && f5.length === 7 ? 'PASS':'FAIL',twEntrypoints:tw.length,f5Entrypoints:f5.length,twUniverse,backtests,legacyMigrated,activeBefore,pendingDoesNotUseZero:!pendingText.includes('命中 0'),duplicateGuard:duplicateText.includes('這組號碼已儲存'),overflow,legacyIndependentEntry:legacyEntry,researchStorageLeak:researchLeak};
    if (mode === 'desktop') results.lifecycle = lifecycle;
    await context.close();
  }
  await browser.close(); server.close();
  const pass = !results.errors.length && results.desktop.status === 'PASS' && results.mobile.status === 'PASS' && results.desktop.twUniverse === 39 && results.desktop.backtests === 5 && results.lifecycle.settledStatus === 'SETTLED' && results.lifecycle.archivedStatus === 'ARCHIVED';
  results.status = pass ? 'PASS':'FAIL';
  fs.writeFileSync(path.resolve(__dirname,'browser_validation.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify(results));
  if (!pass) process.exitCode = 1;
}
main().catch(error => { console.error(error); server.close(); process.exit(1); });
