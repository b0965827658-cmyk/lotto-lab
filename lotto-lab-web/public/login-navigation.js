'use strict';
(() => {
  const login = document.querySelector('#login');
  const update = () => {
    if (login) login.href = '/auth/line/start?next=' + encodeURIComponent(location.pathname + location.hash);
  };
  update();
  addEventListener('hashchange', update);
  login?.addEventListener('click', update);
  if (new URLSearchParams(location.search).get('login') === 'failed') {
    const note = document.createElement('p');
    note.id = 'login-feedback';
    note.setAttribute('role', 'status');
    note.className = 'notice';
    note.textContent = 'LINE 登入未完成或已逾時，你已回到原頁。請按 LINE 登入重試；尚未送出任何投稿。';
    if (login) login.before(note);
    else document.querySelector('main').prepend(note);
  }
})();
