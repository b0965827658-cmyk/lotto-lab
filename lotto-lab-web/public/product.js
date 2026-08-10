const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const locale = window.STAR_LOCALES?.['zh-TW'];
const pages = ['overview', 'tw539', 'fantasy5', 'brain', 'evidence', 'knowledge', 'notifications', 'system'];
let notificationConfig = {};
const legacyData = { tw: { game: 'tw539', history: [], journal: [] }, f5: { game: 'ca-fantasy5', history: [], journal: [] } };

function activate(page) {
  if (!pages.includes(page)) page = 'overview';
  $$('[data-page-panel]').forEach((node) => node.classList.toggle('active', node.dataset.pagePanel === page));
  $$('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.page === page));
  const label = locale.pages[page];
  $('#sectionIndex').textContent = label[0];
  $('#pageTitle').textContent = label[1];
  history.replaceState(null, '', `#${page}`);
  scrollTo({ top: 0, behavior: 'smooth' });
}

function displayDate(value) {
  if (!value) return '日期待更新';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value).replaceAll('-', '/');
  return new Intl.DateTimeFormat('zh-TW', { timeZone: locale.timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(parsed);
}

function setNumbers(id, numbers = []) {
  const element = $(id);
  if (!element) return;
  const shown = numbers.length ? numbers : ['--', '--', '--', '--', '--'];
  element.innerHTML = shown.map((number) => `<b>${String(number).padStart(2, '0')}</b>`).join('');
}

function setDraw(prefix, data) {
  const latest = data?.latest || {};
  const numbers = latest.numbers || [];
  const period = latest.period || '--';
  $(`#${prefix}Draw`).textContent = `最新期別 ${period}`;
  $(`#${prefix}Date`).textContent = displayDate(latest.date);
  setNumbers(`#${prefix}Numbers`, numbers);
  $(`#${prefix}FocusDraw`).textContent = `第 ${period} 期｜${displayDate(latest.date)}`;
  setNumbers(`#${prefix}FocusNumbers`, numbers);
  if (prefix === 'tw') {
    const ranking = latest.ranking || [];
    $('#twTop10').textContent = ranking.slice(0, 10).join('、') || '目前尚無排名資料';
    $('#twTop15').textContent = ranking.slice(0, 15).join('、') || '目前尚無排名資料';
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
}

function numberChips(numbers = [], matched = []) {
  const hits = new Set(matched.map(Number));
  return `<div class="number-row compact-row">${numbers.map((number) => `<b class="${hits.has(Number(number)) ? 'matched' : ''}">${String(number).padStart(2, '0')}</b>`).join('')}</div>`;
}

function renderHistory(prefix) {
  const rows = legacyData[prefix].history;
  const target = $(`#${prefix === 'tw' ? 'tw' : 'f5'}History`);
  if (!rows.length) {
    target.innerHTML = '<p class="empty-state">目前尚無可讀取的歷史開獎紀錄。</p>';
    return;
  }
  target.innerHTML = `<p class="history-count">顯示最近 ${Math.min(rows.length, 30)} 筆，共讀取 ${rows.length} 筆可信紀錄</p>` + rows.slice(0, 30).map((row) => `<article><div><span>第 ${escapeHtml(row.period || '--')} 期</span><small>${displayDate(row.date)}</small></div>${numberChips(row.numbers || [])}</article>`).join('');
}

function coldHotStats(rows, windowSize) {
  const used = rows.slice(0, windowSize);
  const counts = new Map(Array.from({ length: 39 }, (_, index) => [index + 1, 0]));
  const gaps = new Map(Array.from({ length: 39 }, (_, index) => [index + 1, used.length]));
  used.forEach((row, drawIndex) => (row.numbers || []).forEach((number) => {
    counts.set(Number(number), (counts.get(Number(number)) || 0) + 1);
    if (gaps.get(Number(number)) === used.length) gaps.set(Number(number), drawIndex);
  }));
  const ranked = [...counts].map(([number, count]) => ({ number, count, gap: gaps.get(number) })).sort((a, b) => b.count - a.count || a.number - b.number);
  return { used: used.length, hot: ranked.slice(0, 10), cold: [...ranked].sort((a, b) => a.count - b.count || b.gap - a.gap || a.number - b.number).slice(0, 10), overdue: [...ranked].sort((a, b) => b.gap - a.gap || a.number - b.number).slice(0, 10) };
}

function renderColdHot(prefix, windowSize = 30) {
  const target = $(`#${prefix === 'tw' ? 'tw' : 'f5'}ColdHot`);
  const stats = coldHotStats(legacyData[prefix].history, Number(windowSize));
  if (!stats.used) {
    target.innerHTML = '<p class="empty-state">目前尚無足夠資料可進行統計觀察。</p>';
    return;
  }
  const group = (title, items, value) => `<article><p class="overline">${title}</p><div class="stat-number-list">${items.map((item) => `<span><b>${String(item.number).padStart(2, '0')}</b><small>${value(item)}</small></span>`).join('')}</div></article>`;
  const usedRows = legacyData[prefix].history.slice(0, Number(windowSize));
  const values = usedRows.flatMap((row) => row.numbers || []).map(Number);
  const odd = values.filter((number) => number % 2).length;
  const tails = Array.from({ length: 10 }, (_, tail) => ({ tail, count: values.filter((number) => number % 10 === tail).length })).sort((a, b) => b.count - a.count || a.tail - b.tail);
  target.innerHTML = group('近期較常出現', stats.hot, (item) => `${item.count} 次`) + group('近期較少出現', stats.cold, (item) => `${item.count} 次`) + group('目前遺漏較久', stats.overdue, (item) => `${item.gap} 期`) + `<article class="shape-observation"><p class="overline">奇偶與尾數觀察</p><div class="shape-summary"><span>奇數<b>${odd}</b></span><span>偶數<b>${values.length - odd}</b></span><span>較常出現尾數<b>${tails.slice(0, 3).map((item) => `${item.tail} 尾`).join('、')}</b></span></div></article>`;
}

function settledRecords(prefix) {
  return legacyData[prefix].journal.filter((record) => record.status === 'closed' && record.outcome);
}

function renderValidation(prefix) {
  const all = legacyData[prefix].journal;
  const settled = settledRecords(prefix);
  const target = $(`#${prefix === 'tw' ? 'tw' : 'f5'}Validation`);
  if (!all.length) {
    target.innerHTML = '<p class="empty-state">目前尚無合法的前瞻預測紀錄。</p>';
    return;
  }
  const avg = (key) => settled.length ? (settled.reduce((sum, row) => sum + Number(row.outcome?.[key] || 0), 0) / settled.length).toFixed(2) : '—';
  target.innerHTML = `<article><span>前瞻預測紀錄</span><b>${all.length} 筆</b><small>Prediction-before-Actual</small></article><article><span>已結算</span><b>${settled.length} 筆</b><small>${settled.length ? '可供結果摘要' : '等待開獎結果'}</small></article><article><span>Top 5 平均命中</span><b>${avg('hits5')}</b><small>只計已結算紀錄</small></article><article><span>Top 15 平均命中</span><b>${avg('hits15')}</b><small>不代表未來結果</small></article><details><summary>查看進階驗證說明</summary><p>Walk-Forward、Baseline、Random 與證據等級沿用既有正式研究證據；本頁不啟動新回測，也不將研究沙盒公開為一般功能。</p></details>`;
}

function renderMatch(prefix) {
  const target = $(`#${prefix === 'tw' ? 'tw' : 'f5'}Match`);
  const record = settledRecords(prefix)[0] || legacyData[prefix].journal[0];
  if (!record) {
    target.innerHTML = '<p class="empty-state">目前尚無可比對的預測紀錄。</p>' + pickManagerHtml(prefix);
    bindPickManager(prefix);
    return;
  }
  const snapshot = record.snapshot || {};
  const outcome = record.outcome;
  if (!outcome) {
    target.innerHTML = `<div class="match-head"><div><span>目標日期</span><b>${displayDate(record.targetDate)}</b></div><strong>等待開獎結果</strong></div><p>預測已於開獎前保存；開獎資料尚未結算，不以 0 代替結果。</p><p class="overline">完整觀察 15 碼</p>${numberChips(snapshot.full15 || [])}` + pickManagerHtml(prefix);
    bindPickManager(prefix);
    return;
  }
  const actual = outcome.numbers || [];
  target.innerHTML = `<div class="match-head"><div><span>第 ${escapeHtml(outcome.period || '--')} 期</span><b>${displayDate(outcome.date)}</b></div><strong>已完成對號</strong></div><p class="overline">實際開獎號碼</p>${numberChips(actual)}<div class="hit-summary"><span>Top 5 命中<b>${Number(outcome.hits5 ?? 0)}</b></span><span>Top 10 命中<b>${Number(outcome.hits10 ?? 0)}</b></span><span>Top 15 命中<b>${Number(outcome.hits15 ?? 0)}</b></span></div><p class="overline">完整觀察 15 碼</p>${numberChips(snapshot.full15 || [], actual)}` + pickManagerHtml(prefix);
  bindPickManager(prefix);
}

function pickManagerHtml(prefix) {
  return `<details class="pick-manager"><summary>我的自選號碼</summary><p>最多選擇 5 碼，只儲存在這台裝置，不會改變模型或推薦。</p><div class="pick-grid">${Array.from({ length: 39 }, (_, index) => `<button type="button" data-pick="${prefix}" data-number="${index + 1}">${String(index + 1).padStart(2, '0')}</button>`).join('')}</div><div class="pick-actions"><button type="button" data-save-picks="${prefix}">儲存自選號碼</button><button type="button" data-clear-picks="${prefix}">清除</button><span data-pick-state="${prefix}">尚未選擇</span></div></details>`;
}

function bindPickManager(prefix) {
  const key = `star-picks-${prefix}`;
  let selected;
  try { selected = new Set(JSON.parse(localStorage.getItem(key) || '[]').map(Number)); } catch { selected = new Set(); }
  const root = $(`[data-pick="${prefix}"]`)?.closest('.pick-manager');
  if (!root) return;
  const refreshPicks = () => {
    $$(`[data-pick="${prefix}"]`, root).forEach((button) => button.classList.toggle('selected', selected.has(Number(button.dataset.number))));
    $(`[data-pick-state="${prefix}"]`, root).textContent = selected.size ? `已選 ${[...selected].sort((a,b) => a-b).join('、')}` : '尚未選擇';
  };
  $$(`[data-pick="${prefix}"]`, root).forEach((button) => { button.onclick = () => { const number = Number(button.dataset.number); if (selected.has(number)) selected.delete(number); else if (selected.size < 5) selected.add(number); refreshPicks(); }; });
  $(`[data-save-picks="${prefix}"]`, root).onclick = () => { localStorage.setItem(key, JSON.stringify([...selected].sort((a,b) => a-b))); refreshPicks(); };
  $(`[data-clear-picks="${prefix}"]`, root).onclick = () => { selected.clear(); localStorage.removeItem(key); refreshPicks(); };
  refreshPicks();
}

async function loadLegacyData(prefix) {
  const state = legacyData[prefix];
  if (state.loaded) return;
  const [history, journal] = await Promise.all([json(`/api/history-search?game=${state.game}&fromYear=1990&toYear=${new Date().getFullYear()}&limit=500`), json(`/api/prediction-journal?game=${state.game}&limit=100`)]);
  state.history = history.history || [];
  state.journal = journal.records || [];
  state.loaded = true;
  renderHistory(prefix);
  renderColdHot(prefix, 30);
  renderValidation(prefix);
  renderMatch(prefix);
}

function activateFeature(button) {
  const feature = button.dataset.feature;
  const page = button.closest('[data-page-panel]');
  $$('[data-feature]', page).forEach((node) => node.classList.toggle('active', node === button));
  $$('[data-feature-panel]', page).forEach((node) => node.classList.toggle('active', node.dataset.featurePanel === feature));
  if (feature.startsWith('tw-')) loadLegacyData('tw').catch((error) => { console.error('legacy data', error); ['twHistory', 'twColdHot', 'twValidation', 'twMatch'].forEach((id) => { const node = $(`#${id}`); if (node) node.innerHTML = '<p class="empty-state">資料暫時無法載入，請稍後再試。</p>'; }); });
  if (feature.startsWith('f5-')) loadLegacyData('f5').catch((error) => { console.error('legacy data', error); ['f5History', 'f5ColdHot', 'f5Validation', 'f5Match'].forEach((id) => { const node = $(`#${id}`); if (node) node.innerHTML = '<p class="empty-state">資料暫時無法載入，請稍後再試。</p>'; }); });
}

async function json(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

function base64ToUint8Array(value) {
  const padding = '='.repeat((4 - value.length % 4) % 4);
  const base64 = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

function permissionLabel(permission) {
  return { granted: '已允許', denied: '已封鎖', default: '尚未決定' }[permission] || '未知';
}

async function inspectClient() {
  if (!('Notification' in window)) {
    $('#permissionState').textContent = `瀏覽器權限：${locale.notification.unsupported}`;
    $('#notifyTestBtn').disabled = true;
    return;
  }
  $('#permissionState').textContent = `瀏覽器權限：${permissionLabel(Notification.permission)}`;
  $('#notificationHelp').hidden = Notification.permission !== 'denied';
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    $('#swState').textContent = `通知服務：${locale.notification.unsupported}`;
    $('#notifyTestBtn').disabled = true;
    return;
  }
  try {
    const registration = await navigator.serviceWorker.register('/sw.js?v=85');
    $('#swState').textContent = locale.notification.serviceRegistered;
    const subscription = await registration.pushManager.getSubscription();
    $('#pushState').textContent = subscription ? locale.notification.subscriptionPresent : locale.notification.subscriptionAbsent;
    if (subscription) $('#notifyTestBtn').textContent = locale.notification.enabled;
  } catch {
    $('#swState').textContent = locale.notification.serviceBroken;
  }
}

async function enableNotifications() {
  if (!notificationConfig.serverReady || !notificationConfig.publicKey) {
    $('#notificationReality').textContent = locale.notification.setupPending;
    return;
  }
  try {
    const permission = await Notification.requestPermission();
    $('#permissionState').textContent = `瀏覽器權限：${permissionLabel(permission)}`;
    if (permission !== 'granted') {
      $('#notificationReality').textContent = permission === 'denied' ? locale.notification.blocked : locale.notification.notGranted;
      $('#notificationHelp').hidden = permission !== 'denied';
      return;
    }
    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: base64ToUint8Array(notificationConfig.publicKey) });
    await json('/api/push-subscription', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'subscribe', subscription: subscription.toJSON(), game: 'all' }) });
    $('#pushState').textContent = locale.notification.subscriptionPresent;
    $('#notificationReality').textContent = '已開啟重要研究升級與系統異常通知';
    $('#notifyTestBtn').textContent = locale.notification.enabled;
    await refresh();
  } catch {
    $('#notificationReality').textContent = '通知暫時無法開啟，請稍後再試';
  }
}

async function refresh() {
  const button = $('#refreshBtn');
  button.disabled = true;
  button.textContent = '資料載入中…';
  try {
    const [tw539, fantasy5, config] = await Promise.all([json('/api/latest?game=tw539'), json('/api/latest?game=ca-fantasy5'), json('/api/config')]);
    setDraw('tw', tw539);
    setDraw('f5', fantasy5);
    notificationConfig = config.notifications || {};
    const ready = notificationConfig.serverReady && notificationConfig.queueReady;
    $('#notificationReality').textContent = ready ? `${locale.notification.ready}，目前 ${notificationConfig.subscriberCount || 0} 個裝置已開啟` : locale.notification.setupPending;
    $('#notificationMode').textContent = ready ? '服務已就緒' : '尚未就緒';
    $('#notifyTestBtn').textContent = locale.notification.enable;
    $('#dataUpdateState').textContent = '正常';
    $('#lastUpdated').textContent = `更新時間 ${new Intl.DateTimeFormat('zh-TW', { timeZone: locale.timezone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date())}`;
  } catch {
    $('#systemState').textContent = locale.errors.reconnecting;
    $('#dataUpdateState').textContent = locale.errors.load;
    $('#lastUpdated').textContent = '請稍後重新整理';
    $('#notificationReality').textContent = '通知狀態暫時無法載入';
  } finally {
    button.disabled = false;
    button.textContent = '重新整理';
  }
}

$$('.nav-item').forEach((button) => { button.onclick = () => activate(button.dataset.page); });
$$('[data-jump]').forEach((button) => { button.onclick = () => activate(button.dataset.jump); });
$$('[data-feature]').forEach((button) => { button.onclick = () => activateFeature(button); });
$$('[data-window]').forEach((select) => { select.onchange = () => renderColdHot(select.dataset.window, select.value); });
$$('[data-history-search]').forEach((input) => {
  input.oninput = () => {
    const prefix = input.dataset.historySearch;
    const query = input.value.trim().toLowerCase();
    const original = legacyData[prefix].history;
    $(`#${prefix === 'tw' ? 'tw' : 'f5'}History`).innerHTML = original.filter((row) => !query || String(row.period || '').includes(query) || String(row.date || '').includes(query) || (row.numbers || []).some((number) => String(number) === query)).slice(0, 30).map((row) => `<article><div><span>第 ${escapeHtml(row.period || '--')} 期</span><small>${displayDate(row.date)}</small></div>${numberChips(row.numbers || [])}</article>`).join('') || '<p class="empty-state">找不到符合條件的開獎紀錄。</p>';
  };
});
$('#refreshBtn').onclick = refresh;
$('#notifyTestBtn').onclick = enableNotifications;
$('#notificationHelp').onclick = () => alert('請在瀏覽器的網站設定中，將「通知」改為「允許」。iPhone 請先將網站加入主畫面，再從主畫面開啟。');
$('#twRankGrid').innerHTML = Array.from({ length: 39 }, (_, index) => `<span>${String(index + 1).padStart(2, '0')}</span>`).join('');
setInterval(() => { const clock = $('#localClock'); if (clock) clock.textContent = new Intl.DateTimeFormat('zh-TW', { timeZone: locale.timezone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date()); }, 1000);
activate(location.hash.slice(1) || 'overview');
refresh();
inspectClient();
