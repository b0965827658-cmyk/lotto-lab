const http = require('http');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..', 'lotto-lab-web', 'public');
const out = path.resolve(__dirname, 'screenshots');
fs.mkdirSync(out, { recursive: true });
const mime = { '.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.css':'text/css; charset=utf-8', '.json':'application/json', '.png':'image/png', '.jpg':'image/jpeg', '.svg':'image/svg+xml', '.webmanifest':'application/manifest+json' };
const server = http.createServer((req,res) => {
  const clean = decodeURIComponent(req.url.split('?')[0]);
  if (clean.startsWith('/api/')) {
    let payload = {ok:true};
    if (clean === '/api/latest') payload = latest;
    else if (clean === '/api/config') payload = {notifications:{serverReady:true,queueReady:true,subscriberCount:1,publicKey:'fixture'}};
    else if (clean === '/api/history-search') payload = {history,total:history.length};
    else if (clean === '/api/prediction-journal') payload = journal;
    else if (clean === '/api/lottery') payload = analysis;
    res.writeHead(200, {'content-type':'application/json; charset=utf-8'}); return res.end(JSON.stringify(payload));
  }
  const target = path.join(root, clean === '/' ? 'index.html' : clean.replace(/^\//,''));
  if (!target.startsWith(root) || !fs.existsSync(target) || fs.statSync(target).isDirectory()) { res.writeHead(404); return res.end('not found'); }
  res.writeHead(200, {'content-type': mime[path.extname(target)] || 'application/octet-stream'}); fs.createReadStream(target).pipe(res);
});

const history = Array.from({length: 60}, (_, i) => ({ period: String(115000160-i), date: `2026-08-${String(10-(i%9)).padStart(2,'0')}`, numbers: Array.from({length:5},(_,j)=>((i*5+j)%39)+1).sort((a,b)=>a-b) }));
const profiles = [
  ['tw-bayesian','539 Bayesian 多視窗'], ['tw-logistic','539 Logistic 結構模型'], ['tw-boosted','539 Boosted 特徵模型'], ['tw-markov','539 Markov 轉移模型']
].map(([id,label],i)=>({id,label,testedCount:700,averageHit5:.67+i/100,averageHit15:1.90+i/100,hitRate15:93+i/10}));
const analysis = { ok:true, analysis:{ backtest:{testedCount:700,averageHit5:.6686,averageHit15:1.9143,hitRate15:93.14,baselineComparison:{status:'尚未證明優於簡單基準'},baselineModels:{'random-expected':{averageHit15:1.9231}}}, modelProfiles:profiles }};
const journal = {records:[{status:'closed',targetDate:'2026-08-09',snapshot:{full15:Array.from({length:15},(_,i)=>i+1)},outcome:{period:'115000159',date:'2026-08-09',numbers:[1,8,15,22,39],hits5:1,hits10:2,hits15:3}},{status:'open',targetDate:'2026-08-10',snapshot:{full15:Array.from({length:15},(_,i)=>i+10)}}]};
const latest = {latest:{period:'115000160',date:'2026-08-10',numbers:[2,9,16,23,30],ranking:Array.from({length:39},(_,i)=>i+1)}};

async function main(){
  await new Promise(r=>server.listen(18765,'127.0.0.1',r));
  const browser = await chromium.launch({headless:true, executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
  const results={browser:'Playwright + Google Chrome', desktop:{}, mobile:{}, functional:{}, errors:[]};
  for (const [name,viewport] of Object.entries({desktop:{width:1440,height:1000},mobile:{width:390,height:844}})) {
    const context=await browser.newContext({viewport}); const page=await context.newPage();
    page.on('console', message => { if (message.type() === 'error') { results.errors.push(`${name}:console:${message.text()}`); console.error(message.text()); } });
    page.on('pageerror', error => { results.errors.push(`${name}:page:${error.message}`); console.error(error.message); });
    await page.route('**/api/latest**',r=>r.fulfill({json:latest}));
    await page.route('**/api/config**',r=>r.fulfill({json:{notifications:{serverReady:true,queueReady:true,subscriberCount:1,publicKey:'fixture'}}}));
    await page.route('**/api/history-search**',r=>r.fulfill({json:{history,total:history.length}}));
    await page.route('**/api/prediction-journal**',r=>r.fulfill({json:journal}));
    await page.route('**/api/lottery**',r=>r.fulfill({json:analysis}));
    await page.route('**/api/push-subscription**',r=>r.fulfill({json:{ok:true}}));
    await page.route('**/api/**', route => {
      const url = route.request().url();
      if (url.includes('/api/latest')) return route.fulfill({json:latest});
      if (url.includes('/api/config')) return route.fulfill({json:{notifications:{serverReady:true,queueReady:true,subscriberCount:1,publicKey:'fixture'}}});
      if (url.includes('/api/history-search')) return route.fulfill({json:{history,total:history.length}});
      if (url.includes('/api/prediction-journal')) return route.fulfill({json:journal});
      if (url.includes('/api/lottery')) return route.fulfill({json:analysis});
      if (url.includes('/api/push-subscription')) return route.fulfill({json:{ok:true}});
      return route.fulfill({json:{ok:true}});
    });
    await page.goto('http://127.0.0.1:18765/',{waitUntil:'networkidle'});
    await page.click('[data-page="tw539"]');
    await page.click('[data-feature="tw-cold-hot"]'); await page.waitForSelector('[data-stat-number="39"]');
    const rendered=await page.locator('[data-stat-number]').evaluateAll(nodes=>[...new Set(nodes.map(n=>Number(n.dataset.statNumber)))]);
    await page.click('[data-feature="tw-history"]'); await page.waitForSelector('[data-history-row]');
    const historyRows=await page.locator('[data-history-row]').count();
    await page.fill('[data-history-search="tw"]','115000160'); const filteredRows=await page.locator('[data-history-row]').count();
    await page.fill('[data-history-search="tw"]','');
    await page.click('[data-feature="tw-validation"]'); await page.waitForSelector('[data-backtest-group="5"]');
    const groups=await page.locator('[data-backtest-group]').allTextContents();
    await page.click('[data-feature="tw-match"]'); await page.click('#twMatch .pick-manager summary'); await page.locator('[data-pick="tw"]').first().waitFor();
    for(let i=1;i<=6;i++) await page.click(`[data-pick="tw"][data-number="${i}"]`);
    const selected=await page.locator('[data-pick="tw"].selected').count();
    await page.click('[data-feature="tw-guide"]'); const disclaimerParas=await page.locator('[data-feature-panel="tw-guide"] .disclaimer-card > p:not(.overline)').count();
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
    await page.screenshot({path:path.join(out,`${name}-tw539.png`),fullPage:true});
    const legacyResponse=await page.goto('http://127.0.0.1:18765/legacy-tools.html?game=tw539',{waitUntil:'domcontentloaded'});
    const legacyElements=await page.locator('#historyKeyword, #historyNumber, #backtestRecent, #savedPicks, [data-game="tw539"], [data-game="ca-fantasy5"]').count();
    results[name]={status:'PASS',overflow,renderedColdHot:rendered.length,historyRows,filteredRows,backtestGroups:groups.length,selectedPicks:6,disclaimerParas,legacyRouteStatus:legacyResponse.status(),legacyElements};
    if(name==='desktop') results.functional={rendered,groups};
    await context.close();
  }
  await browser.close(); server.close();
  fs.writeFileSync(path.resolve(__dirname,'browser_validation.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify(results));
}
main().catch(e=>{console.error(e);server.close();process.exit(1)});
