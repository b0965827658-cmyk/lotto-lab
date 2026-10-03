'use strict';
const conversation = document.querySelector('#conversation');
const quick = document.querySelector('#quick-replies');
const input = document.querySelector('#command');
let busy = false;
function bubble(text, user = false) {
  const p = document.createElement('p');
  p.className = 'bubble' + (user ? ' user' : '');
  p.textContent = text;
  conversation.append(p);
  while (conversation.children.length > 20) conversation.firstElementChild.remove();
  conversation.scrollTop = conversation.scrollHeight;
}
async function run(command) {
  command = command.trim();
  if (!command || busy) return;
  busy = true;
  document.querySelector('#send').disabled = true;
  bubble(command, true);
  quick.replaceChildren();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch('/api/community/chat-preview?text=' + encodeURIComponent(command), {cache:'no-store', signal:controller.signal});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '預覽暫時無法使用');
    bubble(data.message.text);
    document.querySelector('#mode').textContent = data.lineEnabled ? '這是驗收預覽，不會傳送 LINE 訊息。伺服器新版聊天室回覆已啟用；圖文選單需另外設定。' : '這是驗收預覽，不會傳送 LINE 訊息。實際新版聊天室回覆與圖文選單尚未啟用。';
    for (const item of data.message.quickReply?.items || []) {
      const action = item.action;
      if (action.type === 'message') {
        const button = document.createElement('button');
        button.type = 'button'; button.textContent = action.label;
        button.addEventListener('click', () => run(action.text)); quick.append(button);
      } else if (action.type === 'uri') {
        const url = new URL(action.uri);
        if (!['https://www.youtube.com','https://www.mytvsuper.com'].includes(url.origin)) continue;
        const link = document.createElement('a');
        link.href = url.href; link.textContent = action.label + ' ↗';
        link.target = '_blank'; link.rel = 'noopener noreferrer'; quick.append(link);
      }
    }
  } catch (error) {
    bubble(error.name === 'AbortError' ? '查詢逾時，請稍後重試。' : error.message);
  } finally {
    clearTimeout(timer); busy = false; document.querySelector('#send').disabled = false;
  }
}
document.querySelectorAll('[data-command]').forEach(button => button.addEventListener('click', () => run(button.dataset.command)));
document.querySelector('#command-form').addEventListener('submit', event => {event.preventDefault(); const text = input.value; input.value = ''; run(text);});
fetch('/line-assets/welcome.txt').then(r => {if (!r.ok) throw new Error(); return r.text();}).then(text => document.querySelector('#welcome').textContent = text).catch(() => document.querySelector('#welcome').textContent = '請使用下方連結查看歡迎文案。');
run('使用說明');
