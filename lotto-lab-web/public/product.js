const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const locale = window.STAR_LOCALES?.['zh-TW'];
const pages = ['overview', 'tw539', 'fantasy5', 'brain', 'evidence', 'knowledge', 'notifications', 'system'];
let notificationConfig = {};

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
    const ranking = latest.ranking || numbers;
    $('#twTop10').textContent = ranking.slice(0, 10).join('、') || '目前尚無排名資料';
    $('#twTop15').textContent = ranking.slice(0, 15).join('、') || '目前尚無排名資料';
  }
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
$('#refreshBtn').onclick = refresh;
$('#notifyTestBtn').onclick = enableNotifications;
$('#notificationHelp').onclick = () => alert('請在瀏覽器的網站設定中，將「通知」改為「允許」。iPhone 請先將網站加入主畫面，再從主畫面開啟。');
$('#twRankGrid').innerHTML = Array.from({ length: 39 }, (_, index) => `<span>${String(index + 1).padStart(2, '0')}</span>`).join('');
setInterval(() => { const clock = $('#localClock'); if (clock) clock.textContent = new Intl.DateTimeFormat('zh-TW', { timeZone: locale.timezone, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date()); }, 1000);
activate(location.hash.slice(1) || 'overview');
refresh();
inspectClient();
