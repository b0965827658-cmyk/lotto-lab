const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const locale = window.STAR_LOCALES?.['zh-TW'];
const pages = ['overview', 'tw539', 'fantasy5', 'brain', 'evidence', 'knowledge', 'notifications', 'system'];
let notificationConfig = {};
const legacyData = { tw: { game: 'tw539', history: [], journal: [], analysis: null, latest: null }, f5: { game: 'ca-fantasy5', history: [], journal: [], analysis: null, latest: null } };

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
  legacyData[prefix].latest = latest;
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
  renderHistoryRows(prefix, rows);
}

function renderHistoryRows(prefix, rows) {
  const target = $(`#${prefix === 'tw' ? 'tw' : 'f5'}History`);
  target.innerHTML = rows.length ? `<p class="history-count">完整顯示 ${rows.length} 筆可信紀錄</p>` + rows.map((row) => `<article data-history-row><div><span>第 ${escapeHtml(row.period || '--')} 期</span><small>${displayDate(row.date)}</small></div>${numberChips(row.numbers || [])}</article>`).join('') : '<p class="empty-state">找不到符合條件的開獎紀錄。</p>';
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
  const coldRanked = [...ranked].sort((a, b) => a.count - b.count || b.gap - a.gap || a.number - b.number);
  const hotNumbers = new Set(ranked.slice(0, 10).map((item) => item.number));
  const coldNumbers = new Set(coldRanked.slice(0, 10).map((item) => item.number));
  const all = [...ranked].sort((a, b) => a.number - b.number).map((item) => ({ ...item, status: hotNumbers.has(item.number) ? '偏熱' : coldNumbers.has(item.number) ? '偏冷' : '一般' }));
  return { used: used.length, all, hot: ranked.slice(0, 10), cold: coldRanked.slice(0, 10), overdue: [...ranked].sort((a, b) => b.gap - a.gap || a.number - b.number).slice(0, 10) };
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
  const full = `<article class="full-number-analysis"><p class="overline">1–39 完整號碼分析</p><p class="feature-note">共 ${stats.all.length} 顆；每顆均保留出現次數、冷熱分類與遺漏期數。</p><div class="full-stat-grid">${stats.all.map((item) => `<div class="full-stat-row" data-stat-number="${item.number}"><b>${String(item.number).padStart(2, '0')}</b><span>${item.count} 次</span><span>${item.status}</span><span>遺漏 ${item.gap} 期</span></div>`).join('')}</div></article>`;
  target.innerHTML = group('近期較常出現', stats.hot, (item) => `${item.count} 次`) + group('近期較少出現', stats.cold, (item) => `${item.count} 次`) + group('目前遺漏較久', stats.overdue, (item) => `${item.gap} 期`) + full + `<article class="shape-observation"><p class="overline">奇偶與尾數觀察</p><div class="shape-summary"><span>奇數<b>${odd}</b></span><span>偶數<b>${values.length - odd}</b></span><span>較常出現尾數<b>${tails.slice(0, 3).map((item) => `${item.tail} 尾`).join('、')}</b></span></div></article>`;
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
  const analysis = legacyData[prefix].analysis || {};
  const backtest = analysis.backtest || {};
  const profiles = Array.isArray(analysis.modelProfiles) ? analysis.modelProfiles : [];
  const groups = prefix === 'tw' && backtest.testedCount ? [{ label: '多模型集成（整體）', testedCount: backtest.testedCount, averageHit5: backtest.averageHit5 ?? backtest.averageHit, averageHit15: backtest.averageHit15, hitRate15: backtest.hitRate15 }, ...profiles] : [];
  const groupMarkup = groups.length ? `<section class="backtest-groups"><div class="panel-title"><h3>舊版完整模型回測</h3><span>${groups.length} / 5 組</span></div>${groups.map((group, index) => `<article data-backtest-group="${index + 1}"><span>${escapeHtml(group.label || group.id || `模型 ${index + 1}`)}</span><b>Top 15 平均 ${Number(group.averageHit15 ?? 0).toFixed(4)}</b><small>${Number(group.testedCount || 0)} 期｜Top 5 平均 ${Number(group.averageHit5 ?? group.averageHit ?? 0).toFixed(4)}｜Top 15 命中率 ${Number(group.hitRate15 || 0).toFixed(2)}%</small></article>`).join('')}<details><summary>Baseline／Random 比較</summary><p>${escapeHtml(backtest.baselineComparison?.status || '依既有合法回測資料顯示；不在此頁重新計算。')}</p><p>隨機基準 Top 15 理論平均：${Number(backtest.baselineModels?.['random-expected']?.averageHit15 ?? 1.9231).toFixed(4)}</p></details></section>` : '<p class="empty-state">模型回測資料尚未載入；開啟此分頁時會讀取既有分析結果。</p>';
  target.innerHTML = `<article><span>前瞻預測紀錄</span><b>${all.length} 筆</b><small>Prediction-before-Actual</small></article><article><span>已結算</span><b>${settled.length} 筆</b><small>${settled.length ? '可供結果摘要' : '等待開獎結果'}</small></article><article><span>Top 5 平均命中</span><b>${avg('hits5')}</b><small>只計已結算紀錄</small></article><article><span>Top 15 平均命中</span><b>${avg('hits15')}</b><small>不代表未來結果</small></article>${groupMarkup}`;
}

async function loadValidationAnalysis(prefix) {
  const state = legacyData[prefix];
  if (state.analysis || state.analysisLoading || prefix !== 'tw') return;
  state.analysisLoading = true;
  try {
    const payload = await json(`/api/lottery?game=${state.game}&limit=365`);
    state.analysis = payload.analysis || payload.result || payload;
  } finally {
    state.analysisLoading = false;
    renderValidation(prefix);
  }
}

function renderMatch(prefix) {
  const target = $(`#${prefix === 'tw' ? 'tw' : 'f5'}Match`);
  const record = settledRecords(prefix)[0] || legacyData[prefix].journal[0];
  if (!record) {
    target.innerHTML = '<p class="empty-state">目前尚無可比對的系統預測紀錄。</p>' + savedMatchSummary(prefix);
    return;
  }
  const snapshot = record.snapshot || {};
  const outcome = record.outcome;
  if (!outcome) {
    target.innerHTML = `<div class="match-head"><div><span>目標日期</span><b>${displayDate(record.targetDate)}</b></div><strong>等待開獎結果</strong></div><p>預測已於開獎前保存；開獎資料尚未結算，不以 0 代替結果。</p><p class="overline">完整觀察 15 碼</p>${numberChips(snapshot.full15 || [])}` + savedMatchSummary(prefix);
    return;
  }
  const actual = outcome.numbers || [];
  target.innerHTML = `<div class="match-head"><div><span>第 ${escapeHtml(outcome.period || '--')} 期</span><b>${displayDate(outcome.date)}</b></div><strong>已完成對號</strong></div><p class="overline">實際開獎號碼</p>${numberChips(actual)}<div class="hit-summary"><span>Top 5 命中<b>${Number(outcome.hits5 ?? 0)}</b></span><span>Top 10 命中<b>${Number(outcome.hits10 ?? 0)}</b></span><span>Top 15 命中<b>${Number(outcome.hits15 ?? 0)}</b></span></div><p class="overline">完整觀察 15 碼</p>${numberChips(snapshot.full15 || [], actual)}` + savedMatchSummary(prefix);
}

const savedNumberLabels = { ACTIVE: '本期使用中', WAITING_DRAW: '等待開獎', SETTLED: '已完成對號', ARCHIVED: '歷史紀錄', HIDDEN: '已隱藏' };

function savedRecords(prefix) {
  const service = window.StarSavedNumbers;
  return service ? service.load().filter((record) => record.lottery === legacyData[prefix].game && record.status !== service.STATUS.HIDDEN) : [];
}

function savedMatchSummary(prefix) {
  const records = savedRecords(prefix);
  const pending = records.filter((record) => ['ACTIVE', 'WAITING_DRAW'].includes(record.status));
  const finished = records.filter((record) => ['SETTLED', 'ARCHIVED'].includes(record.status)).slice(0, 3);
  return `<section class="member-match-summary"><p class="overline">我的號碼對號</p>${pending.length ? pending.map((record) => `<article><span>第 ${escapeHtml(record.target_draw_id || '待指定')} 期</span>${numberChips(record.numbers)}<b>等待開獎</b></article>`).join('') : '<p class="empty-state">目前沒有等待開獎的自選號碼。</p>'}${finished.map((record) => `<article><span>${record.migration_status === 'LEGACY_UNASSIGNED' ? '舊版未指定期別' : `第 ${escapeHtml(record.target_draw_id || '--')} 期`}</span>${numberChips(record.numbers, record.actual_numbers || [])}<b>${record.hit_count == null ? '無法判定對號結果' : `命中 ${Number(record.hit_count)} 顆`}</b></article>`).join('')}</section>`;
}

function targetDrawId(prefix) {
  const open = legacyData[prefix].journal.find((record) => !record.outcome && !['closed', 'completed', 'settled'].includes(String(record.status || '').toLowerCase()));
  return String(open?.draw_id || open?.drawId || open?.target_draw_id || open?.targetDrawId || open?.targetDate || '');
}

function savedRecordCard(record, editable) {
  const legacy = record.migration_status === 'LEGACY_UNASSIGNED';
  const outcome = legacy
    ? '<p>舊版紀錄無法判定目標期別，不會冒充正式對號。</p>'
    : record.actual_numbers?.length
      ? `<p>開獎號碼</p>${numberChips(record.actual_numbers)}<p>命中 ${Number(record.hit_count)} 顆：${(record.matched_numbers || []).map((number) => String(number).padStart(2, '0')).join('、') || '無'}</p>`
      : '<p>等待開獎；尚未結算，不以 0 代替結果。</p>';
  return `<article class="saved-record" data-saved-record="${escapeHtml(record.record_id)}"><header><span>${savedNumberLabels[record.status] || '歷史紀錄'}</span><b>${legacy ? '舊版未指定期別' : `第 ${escapeHtml(record.target_draw_id || '--')} 期`}</b></header>${numberChips(record.numbers, record.actual_numbers || [])}${outcome}<footer><small>${displayDate(record.created_at)}</small>${editable ? `<button type="button" data-edit-saved="${escapeHtml(record.record_id)}">編輯</button><button type="button" data-delete-saved="${escapeHtml(record.record_id)}">刪除</button>` : `<button type="button" data-hide-saved="${escapeHtml(record.record_id)}">從我的紀錄隱藏</button>`}</footer></article>`;
}

function renderSavedNumbers(prefix) {
  const service = window.StarSavedNumbers;
  const target = $(`#${prefix === 'tw' ? 'tw' : 'f5'}SavedNumbers`);
  if (!service || !target) return;
  service.reconcile(legacyData[prefix].game, legacyData[prefix].history);
  const records = savedRecords(prefix);
  const active = records.filter((record) => ['ACTIVE', 'WAITING_DRAW'].includes(record.status));
  const settled = records.filter((record) => record.status === 'SETTLED');
  const archived = records.filter((record) => record.status === 'ARCHIVED');
  const drawId = targetDrawId(prefix);
  target.innerHTML = `<section class="saved-create"><h4>新增本期號碼</h4><p>${drawId ? `將綁定第 ${escapeHtml(drawId)} 期` : '目前沒有可安全綁定的開獎期別，暫時不能新增。'}</p><div class="pick-grid">${Array.from({ length: 39 }, (_, index) => `<button type="button" data-saved-pick="${prefix}" data-number="${index + 1}">${String(index + 1).padStart(2, '0')}</button>`).join('')}</div><div class="pick-actions"><button type="button" data-save-record="${prefix}" ${drawId ? '' : 'disabled'}>儲存這組號碼</button><button type="button" data-clear-record="${prefix}">清除選擇</button><span data-saved-state="${prefix}">尚未選擇</span></div></section><section data-saved-section="active"><h4>本期號碼</h4>${active.length ? active.map((record) => savedRecordCard(record, true)).join('') : '<p class="empty-state">目前沒有等待開獎的自選號碼。</p>'}</section><section data-saved-section="settled"><h4>已完成對號</h4>${settled.length ? settled.map((record) => savedRecordCard(record, false)).join('') : '<p class="empty-state">目前沒有剛完成的對號結果。</p>'}</section><section data-saved-section="archived"><h4>歷史紀錄</h4>${archived.length ? archived.map((record) => savedRecordCard(record, false)).join('') : '<p class="empty-state">目前尚無歷史紀錄。</p>'}</section>`;
  bindSavedNumberWorkspace(prefix);
}

function bindSavedNumberWorkspace(prefix) {
  const service = window.StarSavedNumbers;
  const root = $(`#${prefix === 'tw' ? 'tw' : 'f5'}SavedNumbers`);
  if (!service || !root) return;
  let selected = new Set();
  let editing = null;
  const refresh = (message) => {
    $$(`[data-saved-pick="${prefix}"]`, root).forEach((button) => button.classList.toggle('selected', selected.has(Number(button.dataset.number))));
    $(`[data-saved-state="${prefix}"]`, root).textContent = message || (selected.size ? `已選 ${[...selected].sort((a, b) => a - b).join('、')}` : '尚未選擇');
  };
  $$(`[data-saved-pick="${prefix}"]`, root).forEach((button) => { button.onclick = () => { const number = Number(button.dataset.number); selected.has(number) ? selected.delete(number) : selected.add(number); refresh(); }; });
  $(`[data-clear-record="${prefix}"]`, root).onclick = () => { selected.clear(); editing = null; refresh(); };
  $(`[data-save-record="${prefix}"]`, root).onclick = () => {
    const result = editing ? service.updateActive(editing, [...selected]) : service.save({ lottery: legacyData[prefix].game, target_draw_id: targetDrawId(prefix), numbers: [...selected] });
    if (!result.ok) { refresh(result.reason === 'DUPLICATE' ? '這組號碼已儲存' : '請先選擇號碼'); return; }
    renderSavedNumbers(prefix); renderMatch(prefix);
  };
  $$('[data-edit-saved]', root).forEach((button) => { button.onclick = () => { const record = service.load().find((item) => item.record_id === button.dataset.editSaved); if (!record) return; editing = record.record_id; selected = new Set(record.numbers); refresh('編輯中；完成後請按儲存'); root.scrollIntoView({ behavior: 'smooth', block: 'start' }); }; });
  $$('[data-delete-saved]', root).forEach((button) => { button.onclick = () => { service.removeActive(button.dataset.deleteSaved); renderSavedNumbers(prefix); renderMatch(prefix); }; });
  $$('[data-hide-saved]', root).forEach((button) => { button.onclick = () => { service.hide(button.dataset.hideSaved); renderSavedNumbers(prefix); renderMatch(prefix); }; });
  refresh();
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
  window.StarSavedNumbers?.migrateLegacy();
  window.StarSavedNumbers?.reconcile(state.game, state.history);
  renderSavedNumbers(prefix);
  renderMatch(prefix);
}

function activateFeature(button) {
  const feature = button.dataset.feature;
  const page = button.closest('[data-page-panel]');
  $$('[data-feature]', page).forEach((node) => node.classList.toggle('active', node === button));
  $$('[data-feature-panel]', page).forEach((node) => node.classList.toggle('active', node.dataset.featurePanel === feature));
  if (feature.startsWith('tw-')) loadLegacyData('tw').then(() => { if (feature === 'tw-saved') renderSavedNumbers('tw'); }).catch((error) => { console.error('legacy data', error); ['twHistory', 'twColdHot', 'twValidation', 'twMatch', 'twSavedNumbers'].forEach((id) => { const node = $(`#${id}`); if (node) node.innerHTML = '<p class="empty-state">資料暫時無法載入，請稍後再試。</p>'; }); });
  if (feature === 'tw-validation') loadValidationAnalysis('tw').catch((error) => { console.error('model validation data', error); });
  if (feature.startsWith('f5-')) loadLegacyData('f5').then(() => { if (feature === 'f5-saved') renderSavedNumbers('f5'); }).catch((error) => { console.error('legacy data', error); ['f5History', 'f5ColdHot', 'f5Validation', 'f5Match', 'f5SavedNumbers'].forEach((id) => { const node = $(`#${id}`); if (node) node.innerHTML = '<p class="empty-state">資料暫時無法載入，請稍後再試。</p>'; }); });
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
    renderHistoryRows(prefix, original.filter((row) => !query || String(row.period || '').includes(query) || String(row.date || '').includes(query) || (row.numbers || []).some((number) => String(number) === query)));
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
