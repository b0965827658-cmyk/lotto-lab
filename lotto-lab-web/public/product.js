const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const pages = ["overview", "tw539", "fantasy5", "brain", "evidence", "knowledge", "system"];
let notificationConfig = {};

function activate(page) {
  if (!pages.includes(page)) page = "overview";
  $$('[data-page-panel]').forEach((node) => node.classList.toggle("active", node.dataset.pagePanel === page));
  $$('.nav-item').forEach((node) => node.classList.toggle("active", node.dataset.page === page));
  const labels = { overview: "Evidence Intelligence", tw539: "TW539 Intelligence", fantasy5: "Fantasy 5 Observation", brain: "Research Brain", evidence: "Evidence Ledger", knowledge: "Knowledge Library", system: "System & Audit" };
  $('#pageTitle').textContent = labels[page];
  $('#sectionIndex').textContent = `SYSTEM / ${String(pages.indexOf(page) + 1).padStart(2, '0')}`;
  history.replaceState(null, '', `#${page}`);
  scrollTo({ top: 0, behavior: 'smooth' });
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
  $(`#${prefix}Draw`).textContent = `Draw ${latest.period || '--'}`;
  $(`#${prefix}Date`).textContent = latest.date || '--';
  setNumbers(`#${prefix}Numbers`, numbers);
  $(`#${prefix}FocusDraw`).textContent = `Draw ${latest.period || '--'} / ${latest.date || '--'}`;
  setNumbers(`#${prefix}FocusNumbers`, numbers);
  if (prefix === 'tw') {
    const ranking = latest.ranking || numbers;
    $('#twTop10').textContent = ranking.slice(0, 10).join(' / ') || 'Read model unavailable';
    $('#twTop15').textContent = ranking.slice(0, 15).join(' / ') || 'Read model unavailable';
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

async function inspectClient() {
  if (!('Notification' in window)) {
    $('#permissionState').textContent = 'Permission: NOT SUPPORTED';
    return;
  }
  $('#permissionState').textContent = `Permission: ${Notification.permission.toUpperCase()}`;
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    $('#swState').textContent = 'Service Worker: NOT SUPPORTED';
    return;
  }
  try {
    const registration = await navigator.serviceWorker.register('/sw.js?v=85');
    $('#swState').textContent = 'Service Worker: REGISTERED';
    const subscription = await registration.pushManager.getSubscription();
    $('#pushState').textContent = `Push subscription: ${subscription ? 'PRESENT' : 'ABSENT'}`;
  } catch {
    $('#swState').textContent = 'Service Worker: BROKEN';
  }
}

async function enableNotifications() {
  if (!notificationConfig.serverReady || !notificationConfig.publicKey) {
    $('#notificationReality').textContent = 'Push Setup Pending';
    return;
  }
  const permission = await Notification.requestPermission();
  $('#permissionState').textContent = `Permission: ${permission.toUpperCase()}`;
  if (permission !== 'granted') {
    $('#notificationReality').textContent = permission === 'denied' ? 'Notifications blocked in browser settings' : 'Permission not granted';
    return;
  }
  const registration = await navigator.serviceWorker.ready;
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64ToUint8Array(notificationConfig.publicKey),
    });
  }
  await json('/api/push-subscription', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'subscribe', subscription: subscription.toJSON(), game: 'all' }),
  });
  $('#pushState').textContent = 'Push subscription: PRESENT';
  $('#notificationReality').textContent = 'Push enabled for approved safety and research events';
  await refresh();
}

async function refresh() {
  const button = $('#refreshBtn');
  button.disabled = true;
  try {
    const [tw539, fantasy5, config] = await Promise.all([
      json('/api/latest?game=tw539'),
      json('/api/latest?game=ca-fantasy5'),
      json('/api/config'),
    ]);
    setDraw('tw', tw539);
    setDraw('f5', fantasy5);
    notificationConfig = config.notifications || {};
    const ready = notificationConfig.serverReady && notificationConfig.queueReady;
    $('#notificationReality').textContent = ready
      ? `Push provider ready / ${notificationConfig.subscriberCount || 0} subscription(s)`
      : 'Push Setup Pending';
    $('#notificationMode').textContent = ready ? 'SERVER READY' : 'PUSH SETUP PENDING';
    $('#notifyTestBtn').textContent = 'Enable Notifications';
    $('#lastUpdated').textContent = `Updated ${new Date().toLocaleTimeString('zh-TW', { hour12: false })}`;
  } catch {
    $('#systemState').textContent = 'READ MODEL DEGRADED';
    $('#lastUpdated').textContent = 'Live data unavailable';
    $('#notificationReality').textContent = 'Notification state unavailable';
  } finally {
    button.disabled = false;
  }
}

$$('.nav-item').forEach((button) => { button.onclick = () => activate(button.dataset.page); });
$$('[data-jump]').forEach((button) => { button.onclick = () => activate(button.dataset.jump); });
$('#refreshBtn').onclick = refresh;
$('#notifyTestBtn').onclick = enableNotifications;
$('#twRankGrid').innerHTML = Array.from({ length: 39 }, (_, index) => `<span>${String(index + 1).padStart(2, '0')}</span>`).join('');
setInterval(() => { $('#localClock').textContent = new Date().toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false }); }, 1000);
activate(location.hash.slice(1) || 'overview');
refresh();
inspectClient();
