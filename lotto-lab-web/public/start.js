const statusText = document.querySelector('#status');
const draw = document.querySelector('#draw');
const retry = document.querySelector('#retry');
async function loadResult() {
  if (retry.disabled) return;
  retry.disabled = true;
  draw.hidden = true;
  document.querySelector('#numbers').replaceChildren();
  statusText.textContent = '正在查詢官方資料…';
  try {
    const response = await fetch('/api/latest?game=tw539', {cache:'no-store', signal:AbortSignal.timeout(20000)});
    if (!response.ok) throw new Error('unavailable');
    const data = await response.json();
    const latest = data.latest;
    if (data.dataStatus?.validated !== true || !data.ok || latest?.game !== 'tw539' || !/^\d{4}-\d{2}-\d{2}$/.test(latest.date) || !latest.period || !Array.isArray(latest.numbers) || latest.numbers.length !== 5 || new Set(latest.numbers).size !== 5 || !latest.numbers.every(n=>Number.isInteger(n)&&n>=1&&n<=39)) throw new Error('invalid');
    document.querySelector('#date').textContent = `開獎日期 ${latest.date}`;
    document.querySelector('#period').textContent = `第 ${latest.period} 期`;
    for (const number of latest.numbers) {
      const ball = document.createElement('li');
      ball.textContent = String(number).padStart(2,'0');
      document.querySelector('#numbers').append(ball);
    }
    document.querySelector('#fetched').textContent = `本次查詢：${new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',dateStyle:'short',timeStyle:'short'}).format(new Date())}（台灣時間）`;
    statusText.textContent = latest.officialHistoryFallback ? '官方歷史最近一期；最新總表暫時無回應。' : '已取得官方來源最近一筆資料';
    const today = new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    if (latest.date !== today) statusText.textContent += '；這是先前一期，今天的官方結果尚未取得。';
    draw.hidden = false;
  } catch (_) {
    statusText.textContent = '暫時無法取得通過驗證的資料，請稍後重新查詢。';
  } finally { retry.disabled = false; }
}
retry.addEventListener('click',loadResult);
loadResult();

setInterval(()=>{if(!document.hidden)loadResult();},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadResult();});
