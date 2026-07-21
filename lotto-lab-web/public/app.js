const state = {
  game: "tw539",
  limit: 90,
  backtestLimit: 24,
  flagshipLimit: 120,
  plan: "free",
  subscription: null,
  savedSelection: [],
  analysisFocus: "balanced",
  latest: null,
  latestByGame: {
    tw539: null,
    "ca-fantasy5": null,
  },
  analysis: null,
  history: [],
  displayHistory: [],
  flagshipHistory: [],
  flagshipHistoryLoading: false,
  flagshipHistoryLatestKey: "",
  coreCandidateSelection: [],
  requestId: 0,
  latestRequestId: 0,
  latestRefreshInFlight: false,
  autoRefreshTimer: null,
  activeTab: "latest",
  apiCache: new Map(),
  candidateCache: new Map(),
  backtestCache: new Map(),
  modelRenderTimer: null,
  countdownTimer: null,
  notifications: {
    supported: false,
    serverReady: false,
    publicKey: "",
    subscriberCount: 0,
    autoNotifyIntervalSeconds: 30,
  },
  serviceWorkerRegistration: null,
  pushSubscription: null,
  modelWeights: {
    heat: 30,
    overdue: 25,
    spread: 25,
    backtest: 20,
  },
  historySearch: {
    keyword: "",
    number: "",
  },
};

const STORAGE_KEY = "lotto-lab-saved-picks";
const MODEL_STORAGE_KEY = "lotto-lab-model-weights";
const FOCUS_STORAGE_KEY = "lotto-lab-analysis-focus";
const PLAN_STORAGE_KEY = "lotto-lab-plan-preview";
const MODEL_SNAPSHOT_STORAGE_KEY = "lotto-lab-model-snapshots";
const API_CACHE_STORAGE_KEY = "lotto-lab-api-cache-v3";
const LAST_SEEN_DRAW_STORAGE_KEY = "lotto-lab-last-seen-draw";
const DAILY_COMPARISON_STORAGE_KEY = "lotto-lab-daily-comparison-v1";
const BACKTEST_LIMIT_STORAGE_KEY = "lotto-lab-backtest-limit";
const FLAGSHIP_LIMIT_STORAGE_KEY = "lotto-lab-flagship-limit";
const POLL_INTERVAL_MS = 30 * 1000;
const LATEST_FETCH_TIMEOUT_MS = 15000;
const FETCH_TIMEOUT_MS = 60000;
const MAX_BACKTEST_CACHE_SIZE = 600;
const MODEL_RENDER_DEBOUNCE_MS = 120;
const CANDIDATE_ATTEMPTS = 72;
const BACKTEST_PRESETS = [7, 14, 21, 24, 28, 35, 60, 90, 180, 365];

const FOCUS_PRESETS = {
  balanced: {
    label: "綜合",
    weights: { heat: 28, overdue: 22, spread: 25, backtest: 25 },
    description: "熱度、遺漏、版型與回測一起看。",
  },
  classic: {
    label: "熱遺平衡",
    weights: { heat: 45, overdue: 27, spread: 18, backtest: 10 },
    description: "回到曾經 539 回測中 4 的樸素熱度加遺漏邏輯，少做尾數限制。",
  },
  hot: {
    label: "追熱",
    weights: { heat: 48, overdue: 8, spread: 18, backtest: 26 },
    description: "偏近期常出與高頻號，再用回測過濾。",
  },
  overdue: {
    label: "追冷",
    weights: { heat: 12, overdue: 45, spread: 18, backtest: 25 },
    description: "偏久未開號，避免整組太集中。",
  },
  pattern: {
    label: "版路",
    weights: { heat: 18, overdue: 18, spread: 42, backtest: 22 },
    description: "優先看區間、奇偶、大小與尾數分散。",
  },
  interval: {
    label: "區間",
    weights: { heat: 16, overdue: 18, spread: 38, backtest: 28 },
    description: "優先抓近期集中落點區間，再混合熱號、拖牌與回測。",
  },
  backtest: {
    label: "回測",
    weights: { heat: 16, overdue: 16, spread: 18, backtest: 50 },
    description: "優先挑過去 90 期回測較能碰到邊的組合。",
  },
};

const MODE_SNAPSHOT_KEYS = ["balanced", "classic", "hot", "overdue", "interval", "pattern", "backtest"];
const CANDIDATE_LOGIC_PRESETS = [
  {
    key: "hot",
    label: "熱號追擊",
    description: "優先追蹤近期熱度較高的號碼，再用回測與分散條件篩選。",
  },
  {
    key: "interval",
    label: "區間追擊",
    description: "鎖定近期較集中的落點區間，兼顧區間分布與號碼熱度。",
  },
  {
    key: "pattern",
    label: "版路追擊",
    description: "參考連號、拖牌、鄰近號與奇偶大小等近期版路訊號。",
  },
  {
    key: "overdue",
    label: "冷號回補",
    description: "挑選遺漏較久且仍通過核心條件的號碼，避免單純追最冷。",
  },
  {
    key: "balanced",
    label: "綜合推理",
    description: "整合熱度、區間、版路、遺漏與回測，作為整體參考。",
  },
];
const VALIDATION_RECORDS = [
  {
    game: "tw539",
    mode: "區間舊版截圖",
    source: "已補登",
    date: "2026-07-08",
    period: "115000165",
    pick: [1, 6, 11, 30, 34],
    actual: [1, 11, 23, 30, 34],
  },
];

const $ = (selector) => document.querySelector(selector);

const els = {
  dashboard: $("#dashboard"),
  status: $("#status"),
  refresh: $("#refreshBtn"),
  limit: $("#limitSelect"),
  gameName: $("#gameName"),
  period: $("#period"),
  date: $("#date"),
  latestBalls: $("#latestBalls"),
  secondaryGameName: $("#secondaryGameName"),
  secondaryPeriod: $("#secondaryPeriod"),
  secondaryDate: $("#secondaryDate"),
  secondaryLatestBalls: $("#secondaryLatestBalls"),
  secondaryLatestStatus: $("#secondaryLatestStatus"),
  countdownTime: $("#countdownTime"),
  countdownBadge: $("#countdownBadge"),
  countdownGame: $("#countdownGame"),
  countdownDrawAt: $("#countdownDrawAt"),
  countdownHint: $("#countdownHint"),
  pickBalls: $("#pickBalls"),
  pickMeta: $("#pickMeta"),
  flagshipBalls: $("#flagshipBalls"),
  flagshipMeta: $("#flagshipMeta"),
  coreCandidateBalls: $("#coreCandidateBalls"),
  coreCandidateMeta: $("#coreCandidateMeta"),
  coreCandidateSave: $("#coreCandidateSave"),
  adaptiveBalls: $("#adaptiveBalls"),
  adaptiveMeta: $("#adaptiveMeta"),
  note: $("#analysisNote"),
  hot: $("#hotList"),
  cold: $("#coldList"),
  overdue: $("#overdueList"),
  history: $("#historyRows"),
  drawCount: $("#drawCount"),
  plans: $("#planGrid"),
  savedForm: $("#savedForm"),
  savedPicker: $("#savedNumberPicker"),
  savedSelectionMeta: $("#savedSelectionMeta"),
  clearSavedNumbers: $("#clearSavedNumbers"),
  savedList: $("#savedList"),
  usePick: $("#usePickBtn"),
  generate: $("#generateBtn"),
  candidates: $("#candidateList"),
  modeSnapshots: $("#modeSnapshotList"),
  flagshipHistoryList: $("#flagshipHistoryList"),
  flagshipHistoryRefresh: $("#flagshipHistoryRefresh"),
  modelInputs: Array.from(document.querySelectorAll("[data-weight]")),
  focusButtons: Array.from(document.querySelectorAll("[data-focus]")),
  modelSummary: $("#modelSummary"),
  resetModel: $("#resetModelBtn"),
  historyKeyword: $("#historyKeyword"),
  historyNumber: $("#historyNumber"),
  clearHistorySearch: $("#clearHistorySearch"),
  historyCount: $("#historyCount"),
  historyFromYear: $("#historyFromYear"),
  historyToYear: $("#historyToYear"),
  crossYearSearch: $("#crossYearSearch"),
  historyScope: $("#historyScope"),
  recentScope: $("#recentScope"),
  backtestBadge: $("#backtestBadge"),
  backtestSelect: $("#backtestLimitSelect"),
  backtestInput: $("#backtestLimitInput"),
  backtestApply: $("#backtestLimitApply"),
  flagshipLimitSelect: $("#flagshipLimitSelect"),
  flagshipLimitApply: $("#flagshipLimitApply"),
  avgHit: $("#avgHit"),
  threePlusRate: $("#threePlusRate"),
  bestHit: $("#bestHit"),
  backtestRecent: $("#backtestRecent"),
  backtestMethod: $("#backtestMethod"),
  patternModel: $("#patternModel"),
  patternRepeat: $("#patternRepeat"),
  patternGrid: $("#patternGrid"),
  patternLines: $("#patternLines"),
  recentPatternAutoBadge: $("#recentPatternAutoBadge"),
  recentPatternAutoSummary: $("#recentPatternAutoSummary"),
  recentPatternAutoSignals: $("#recentPatternAutoSignals"),
  recentPatternAutoWindows: $("#recentPatternAutoWindows"),
  recentPatternAutoNote: $("#recentPatternAutoNote"),
  tailAnalysisBadge: $("#tailAnalysisBadge"),
  tailAnalysisSummary: $("#tailAnalysisSummary"),
  tailHotList: $("#tailHotList"),
  tailAvoidList: $("#tailAvoidList"),
  tailRecommendationBalls: $("#tailRecommendationBalls"),
  tailAnalysisMeta: $("#tailAnalysisMeta"),
  tailAnalysisNote: $("#tailAnalysisNote"),
  notifyBadge: $("#notifyBadge"),
  notifyText: $("#notifyText"),
  notifyToggle: $("#notifyToggleBtn"),
  notifyTest: $("#notifyTestBtn"),
  proPanels: Array.from(document.querySelectorAll('[data-tier="pro"]')),
  flagshipPanels: Array.from(document.querySelectorAll('[data-tier="flagship"]')),
  tabButtons: Array.from(document.querySelectorAll("[data-tab]")),
  tabPanels: Array.from(document.querySelectorAll("[data-tab-panel]")),
};

function pad(n) {
  return String(n).padStart(2, "0");
}

function normalizedPick(value, count = 5) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(Number).filter((number) => Number.isInteger(number) && number >= 1 && number <= 39))]
    .sort((left, right) => left - right)
    .slice(0, count);
}

function hashString(value) {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function createRng(seed) {
  let value = hashString(seed) || 1;
  return () => {
    value += 0x6d2b79f5;
    let next = Math.imul(value ^ (value >>> 15), value | 1);
    next ^= next + Math.imul(next ^ (next >>> 7), next | 61);
    return ((next ^ (next >>> 14)) >>> 0) / 4294967296;
  };
}

function balls(numbers) {
  return numbers.map((n) => `<span class="ball">${pad(n)}</span>`).join("");
}

function miniBalls(numbers, winners = []) {
  const winnerSet = new Set(winners.map(Number));
  return numbers
    .map((n) => {
      const hit = winnerSet.has(Number(n));
      return `<span class="mini-ball ${hit ? "hit" : ""}"${hit ? ' title="命中號碼" aria-label="命中號碼"' : ""}>${pad(n)}${hit ? '<b class="hit-mark" aria-hidden="true">✓</b>' : ""}</span>`;
    })
    .join("");
}

function compareHistoryDraws(left, right) {
  const dateCompare = String(left?.date || "").localeCompare(String(right?.date || ""));
  if (dateCompare !== 0) return dateCompare;
  return String(left?.period || "").localeCompare(String(right?.period || ""), undefined, { numeric: true });
}

function buildHistoryMarkerIndex(draws = []) {
  const markerIndex = new Map();
  const ordered = draws
    .filter((draw) => draw && Array.isArray(draw.numbers) && draw.numbers.length)
    .slice()
    .sort(compareHistoryDraws);
  const lastSeen = new Map();

  ordered.forEach((draw, drawIndex) => {
    const numbers = [...new Set(draw.numbers.map(Number))].sort((left, right) => left - right);
    const previousNumbers = new Set((ordered[drawIndex - 1]?.numbers || []).map(Number));
    const consecutiveNumbers = new Set();
    for (let index = 0; index < numbers.length - 1; index += 1) {
      if (numbers[index + 1] === numbers[index] + 1) {
        consecutiveNumbers.add(numbers[index]);
        consecutiveNumbers.add(numbers[index + 1]);
      }
    }

    const drawMarkers = new Map();
    numbers.forEach((number) => {
      const gap = lastSeen.has(number) ? drawIndex - lastSeen.get(number) - 1 : null;
      drawMarkers.set(number, {
        returning: gap !== null && gap >= 20,
        repeat: previousNumbers.has(number),
        consecutive: consecutiveNumbers.has(number),
        gap,
      });
      lastSeen.set(number, drawIndex);
    });
    markerIndex.set(drawKey(draw), drawMarkers);
  });
  return markerIndex;
}

function markerClasses(marker = {}) {
  return [
    marker.returning ? "marker-return" : "",
    marker.repeat ? "marker-repeat" : "",
    marker.consecutive ? "marker-consecutive" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function markerTitle(marker = {}) {
  const labels = [];
  if (marker.returning) labels.push(`20 期以上未出後回補${marker.gap !== null ? `（${marker.gap} 期）` : ""}`);
  if (marker.repeat) labels.push("連莊");
  if (marker.consecutive) labels.push("連號");
  return labels.join("・");
}

function markedHistoryBalls(draw, markerIndex = new Map(), winners = []) {
  const winnerSet = new Set(winners.map(Number));
  const drawMarkers = markerIndex.get(drawKey(draw)) || new Map();
  return (draw.numbers || [])
    .map((number) => {
      const marker = drawMarkers.get(Number(number)) || {};
      const classes = ["mini-ball", "history-ball", winnerSet.has(Number(number)) ? "hit" : "", markerClasses(marker)]
        .filter(Boolean)
        .join(" ");
      const title = markerTitle(marker);
      return `<span class="${classes}"${title ? ` title="${title}"` : ""}>${pad(number)}</span>`;
    })
    .join("");
}

function zonedParts(date, timeZone) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  return Object.fromEntries(
    formatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)])
  );
}

function zonedDate(timeZone, year, month, day, hour, minute = 0, second = 0) {
  const guess = Date.UTC(year, month - 1, day, hour, minute, second);
  const parts = zonedParts(new Date(guess), timeZone);
  const rendered = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
  return new Date(guess - (rendered - guess));
}

function zonedDayIndex(date, timeZone) {
  const parts = zonedParts(date, timeZone);
  return new Date(Date.UTC(parts.year, parts.month - 1, parts.day)).getUTCDay();
}

function addDaysInZone(timeZone, date, days) {
  const parts = zonedParts(date, timeZone);
  const base = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + days));
  return zonedDate(timeZone, base.getUTCFullYear(), base.getUTCMonth() + 1, base.getUTCDate(), 0, 0, 0);
}

function nextDrawForGame(game, now = new Date()) {
  const schedule =
    game === "ca-fantasy5"
      ? {
          gameName: "加州天天樂",
          timeZone: "America/Los_Angeles",
          hour: 18,
          minute: 30,
          drawDays: [0, 1, 2, 3, 4, 5, 6],
          localLabel: "加州每日 18:30 後",
          hint: "已換算成你目前裝置時間。",
        }
      : {
          gameName: "今彩 539",
          timeZone: "Asia/Taipei",
          hour: 20,
          minute: 30,
          drawDays: [1, 2, 3, 4, 5, 6],
          localLabel: "台灣週一至週六 20:30",
          hint: "週日休市，倒數會自動跳到週一。",
        };

  for (let offset = 0; offset < 10; offset += 1) {
    const dayStart = addDaysInZone(schedule.timeZone, now, offset);
    const parts = zonedParts(dayStart, schedule.timeZone);
    const candidate = zonedDate(schedule.timeZone, parts.year, parts.month, parts.day, schedule.hour, schedule.minute, 0);
    if (schedule.drawDays.includes(zonedDayIndex(candidate, schedule.timeZone)) && candidate > now) {
      return { ...schedule, at: candidate };
    }
  }
  return { ...schedule, at: new Date(now.getTime() + 24 * 60 * 60 * 1000) };
}

function formatCountdown(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const time = [hours, minutes, seconds].map(pad).join(":");
  return days > 0 ? `${days}天 ${time}` : time;
}

function renderCountdown() {
  if (!els.countdownTime) return;
  const next = nextDrawForGame(state.game);
  const diff = next.at.getTime() - Date.now();
  const localDrawAt = new Intl.DateTimeFormat("zh-Hant-TW", {
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(next.at);
  els.countdownTime.textContent = formatCountdown(diff);
  els.countdownBadge.textContent = diff <= 0 ? "更新中" : "倒數";
  els.countdownGame.textContent = next.gameName;
  els.countdownDrawAt.textContent = `${localDrawAt} 開獎`;
  els.countdownHint.textContent = `${next.localLabel}，${next.hint}`;
}

function startCountdown() {
  renderCountdown();
  if (state.countdownTimer) window.clearInterval(state.countdownTimer);
  state.countdownTimer = window.setInterval(renderCountdown, 1000);
}

function rankRows(items, mode) {
  const max = Math.max(...items.map((item) => item.count ?? item.gap), 1);
  return items
    .map((item) => {
      const value = item.count ?? item.gap;
      const label = mode === "gap" ? `${value} 期` : `${value} 次`;
      const width = Math.max(6, Math.round((value / max) * 100));
      return `
        <div class="rank">
          <span class="mini-ball">${pad(item.number)}</span>
          <span class="bar"><span style="width:${width}%"></span></span>
          <span class="rank-value">${label}</span>
        </div>
      `;
    })
    .join("");
}

function drawWeekday(value) {
  const match = String(value || "").match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
  if (!match) return "";
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12);
  const labels = ["日", "一", "二", "三", "四", "五", "六"];
  return `星期${labels[date.getDay()]}`;
}

function historyRows(draws) {
  if (!draws.length) {
    return `
      <tr>
        <td colspan="3" class="empty-cell">查無符合條件的開獎紀錄</td>
      </tr>
    `;
  }
  const markerIndex = buildHistoryMarkerIndex(state.displayHistory);
  return draws
    .map(
      (draw) => `
        <tr>
          <td class="history-date">
            <strong>${draw.date || "-"}</strong>
            <span>${drawWeekday(draw.date)}</span>
          </td>
          <td class="history-period">${draw.period || "-"}</td>
          <td class="number-text">
            <div class="history-balls" aria-label="開獎號碼 ${draw.numbers.map(pad).join("、")}">
              ${markedHistoryBalls(draw, markerIndex)}
            </div>
          </td>
        </tr>
      `,
    )
    .join("");
}

function filteredHistory() {
  const keyword = state.historySearch.keyword.trim().toLowerCase();
  const number = Number(state.historySearch.number);
  return state.displayHistory.filter((draw) => {
    const text = `${draw.date} ${draw.period} ${draw.numbers.map(pad).join(" ")}`.toLowerCase();
    const keywordMatch = !keyword || text.includes(keyword);
    const numberMatch = !state.historySearch.number || draw.numbers.includes(number);
    return keywordMatch && numberMatch;
  });
}

function renderHistory() {
  const rows = filteredHistory();
  els.history.innerHTML = historyRows(rows);
  els.historyCount.textContent = `${rows.length} / ${state.displayHistory.length} 期`;
}

function flagshipHistoryNumbers(items = []) {
  return items.map((item) => pad(item)).join("、") || "資料不足";
}

function renderFlagshipHistory() {
  if (!els.flagshipHistoryList) return;
  if (!isFlagshipPlan()) {
    els.flagshipHistoryList.innerHTML = `<div class="empty-state">升級量化旗艦版後可查看歷史推理紀錄。</div>`;
    return;
  }
  if (state.flagshipHistoryLoading) {
    els.flagshipHistoryList.innerHTML = `<div class="empty-state">正在載入旗艦分析紀錄...</div>`;
    return;
  }
  if (!state.flagshipHistory.length) {
    els.flagshipHistoryList.innerHTML = `<div class="empty-state">目前還沒有保存的旗艦分析；完成一次數據分析後會自動建立紀錄。</div>`;
    return;
  }
  const markerIndex = buildHistoryMarkerIndex(state.displayHistory);
  els.flagshipHistoryList.innerHTML = state.flagshipHistory
    .map((record) => {
      const reasoning = record.reasoning || {};
      const summary = reasoning.backtestSummary || {};
      const components = (record.components || [])
        .map((item) => `${item.label || item.id} ${item.weight || 0}%`)
        .join(" · ");
      const actualAvailable = record.actualPeriod && Array.isArray(record.actualNumbers) && record.actualNumbers.length;
      const actualDraw = actualAvailable
        ? state.displayHistory.find(
            (draw) => String(draw.period || "") === String(record.actualPeriod || "") || draw.date === record.actualDate,
          )
        : null;
      const outcome = record.hitCount === null || record.hitCount === undefined ? "待下一期開出" : `${record.hitCount} 中`;
      const statusNote = actualAvailable ? "已補上實際開獎與命中結果" : "開獎後自動補上命中結果";
      const backtestText = summary.testedCount
        ? `回測 ${summary.testedCount} 期 · 均中 ${summary.averageHit ?? 0} · 最高 ${summary.bestHit ?? 0} 中`
        : "回測資料累積中";
      return `
        <article class="flagship-history-item">
          <div class="flagship-history-head">
            <div>
              <strong>${record.latestDate || "-"}</strong>
              <span>分析期 ${record.latestPeriod || "-"} · 穩定核心邏輯</span>
            </div>
          </div>
          <div class="flagship-history-status ${actualAvailable ? "completed" : "pending"}">
            <div>
              <span class="flagship-history-status-label">開獎狀態</span>
              <strong>${outcome}</strong>
            </div>
            <span class="flagship-history-status-note">${statusNote}</span>
          </div>
          <div class="flagship-history-balls">${miniBalls(record.numbers, actualAvailable ? record.actualNumbers : [])}</div>
          <div class="flagship-history-meta">
            <span>${record.method || "六維綜合推理"}</span>
            <span>邏輯 ${record.profile || "綜合"}</span>
            <span>${components || coreWeightSummary()}</span>
          </div>
          <div class="flagship-history-reasoning">
            <span>同一套核心分析：近期熱度、長期熱度、遺漏平衡、區間分布</span>
            <span>${backtestText}</span>
          </div>
          ${actualAvailable ? `<div class="flagship-history-actual">後續開獎 ${record.actualDate || "-"}／${record.actualPeriod || "-"}：<div class="flagship-history-actual-balls">${markedHistoryBalls(actualDraw || { ...record, numbers: record.actualNumbers }, markerIndex)}</div></div>` : ""}
        </article>
      `;
    })
    .join("");
}

async function loadFlagshipHistory(options = {}) {
  if (!els.flagshipHistoryList || !isFlagshipPlan()) {
    renderFlagshipHistory();
    return;
  }
  const force = Boolean(options.force);
  const latestKey = drawKey(state.latest);
  if (!force && state.flagshipHistoryLatestKey === latestKey && state.flagshipHistory.length) return;
  state.flagshipHistoryLoading = true;
  renderFlagshipHistory();
  if (els.flagshipHistoryRefresh) els.flagshipHistoryRefresh.disabled = true;
  try {
    const payload = await fetchJsonWithTimeout(
      `/api/flagship-history?game=${state.game}&limit=30&t=${Date.now()}`,
      { timeoutMs: 15000 },
    );
    if (!payload.ok) throw new Error(payload.error || "旗艦歷史載入失敗");
    state.flagshipHistory = Array.isArray(payload.history) ? payload.history : [];
    state.flagshipHistoryLatestKey = latestKey;
    renderFlagshipHistory();
    if (!options.silent) setStatus(`已載入 ${state.flagshipHistory.length} 筆旗艦分析紀錄。`);
  } catch (error) {
    renderFlagshipHistory();
    if (!options.silent) setStatus(error.name === "AbortError" ? "旗艦歷史載入逾時，請稍後再試。" : error.message, true);
  } finally {
    state.flagshipHistoryLoading = false;
    if (els.flagshipHistoryRefresh) els.flagshipHistoryRefresh.disabled = false;
    renderFlagshipHistory();
  }
}

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.classList.toggle("error", isError);
}

function isProPlan() {
  return state.plan === "pro" || state.plan === "flagship";
}

function isFlagshipPlan() {
  return state.plan === "flagship";
}

function requirePro(feature) {
  if (isProPlan()) return true;
  setStatus(`${feature} 是 Pro 訂閱版功能。可以先按「預覽 Pro」查看完整介面。`, true);
  activateTab("subscription");
  return false;
}

function requireFlagship(feature) {
  if (isFlagshipPlan()) return true;
  setStatus(`${feature} 是「摘星狙擊手｜量化旗艦版」專屬功能。`, true);
  activateTab("subscription");
  return false;
}

function activateTab(tabName) {
  state.activeTab = tabName;
  els.tabButtons.forEach((button) => {
    const active = button.dataset.tab === tabName;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  els.tabPanels.forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.tabPanel === tabName);
  });
  if ((tabName === "model" || tabName === "sniper") && state.analysis) {
    renderModelOutput({ heavy: tabName === "model" });
    loadFlagshipHistory({ silent: true });
  }
}

function organizeAnalysisPanels() {
  const latestPanel = document.querySelector('[data-tab-panel="latest"]');
  const recentPanel = document.querySelector('[data-tab-panel="recent"]');
  const sniperPanel = document.querySelector('[data-tab-panel="sniper"]');
  const modelPanel = document.querySelector('[data-tab-panel="model"]');
  if (!latestPanel || !recentPanel || !sniperPanel || !modelPanel) return;

  const panelById = (id) => document.getElementById(id)?.closest(".panel");
  const movePanels = (destination, source, selectors) => {
    const movable = selectors
      .map((selector) => source.querySelector(selector))
      .filter((panel, index, list) => panel && panel.closest("[data-tab-panel]") === source && list.indexOf(panel) === index);
    if (!movable.length) return;
    const fragment = document.createDocumentFragment();
    movable.forEach((panel) => fragment.appendChild(panel));
    destination.insertBefore(fragment, destination.firstElementChild);
  };

  movePanels(modelPanel, latestPanel, [".countdown-panel", ".backtest-panel", ".pattern-panel"]);
  movePanels(sniperPanel, modelPanel, [".tail-analysis-panel", ".core-candidate-panel", ".flagship-history-panel"]);
  movePanels(sniperPanel, latestPanel, [".pick-panel", ".flagship-panel"]);
  const recentPanels = [
    panelById("hotList"),
    panelById("coldList"),
    panelById("overdueList"),
  ].filter(
    (panel, index, list) => panel && panel.closest("[data-tab-panel]") === latestPanel && list.indexOf(panel) === index,
  );
  if (recentPanels.length) {
    const fragment = document.createDocumentFragment();
    recentPanels.forEach((panel) => fragment.appendChild(panel));
    recentPanel.appendChild(fragment);
  }
}

function loadPlanPreview() {
  const saved = localStorage.getItem(PLAN_STORAGE_KEY);
  return saved === "flagship" || saved === "pro" ? saved : "free";
}

function savePlanPreview() {
  localStorage.setItem(PLAN_STORAGE_KEY, state.plan);
}

function normalizeBacktestLimit(value) {
  const number = Number(value);
  if (!Number.isInteger(number)) return 24;
  return Math.max(7, Math.min(365, number));
}

function loadBacktestLimit() {
  return normalizeBacktestLimit(localStorage.getItem(BACKTEST_LIMIT_STORAGE_KEY) || 24);
}

function saveBacktestLimit() {
  localStorage.setItem(BACKTEST_LIMIT_STORAGE_KEY, String(state.backtestLimit));
}

function normalizeFlagshipLimit(value) {
  return 120;
}

function loadFlagshipLimit() {
  return 120;
}

function saveFlagshipLimit() {
  localStorage.setItem(FLAGSHIP_LIMIT_STORAGE_KEY, String(state.flagshipLimit));
}

function syncBacktestControls() {
  if (!els.backtestSelect || !els.backtestInput) return;
  const value = String(state.backtestLimit);
  els.backtestInput.value = value;
  els.backtestSelect.value = BACKTEST_PRESETS.includes(state.backtestLimit) ? value : "custom";
}

function syncFlagshipControls() {
  if (!els.flagshipLimitSelect) return;
  els.flagshipLimitSelect.value = String(state.flagshipLimit);
}

function applyPlanAccess() {
  document.body.dataset.plan = state.plan;
  const pro = isProPlan();
  const flagship = isFlagshipPlan();
  els.proPanels.forEach((panel) => {
    panel.classList.toggle("locked", !pro);
    panel.setAttribute("aria-disabled", String(!pro));
  });
  els.flagshipPanels.forEach((panel) => {
    panel.classList.toggle("locked", !flagship);
    panel.setAttribute("aria-disabled", String(!flagship));
  });
  [els.backtestSelect, els.backtestInput, els.backtestApply].filter(Boolean).forEach((control) => {
    control.disabled = !pro;
  });
  [els.flagshipLimitSelect, els.flagshipLimitApply].filter(Boolean).forEach((control) => {
    control.disabled = !flagship;
  });
  Array.from(els.limit.options).forEach((option) => {
    option.disabled = !pro && Number(option.value) > 90;
  });
  if (!pro && state.limit > 90) {
    state.limit = 90;
    els.limit.value = "90";
  }
  els.focusButtons.forEach((button) => {
    button.disabled = !pro;
  });
  els.modelInputs.forEach((input) => {
    input.disabled = !pro;
  });
  els.resetModel.disabled = !pro;
  els.generate.disabled = !pro;
  els.crossYearSearch.classList.toggle("pro-required", !pro);
  updateNotificationUi();
  if (state.analysis) {
    renderFlagshipPick();
    renderFlagshipHistory();
    renderTailAnalysis(state.analysis.tailAnalysis);
    renderModelOutput({ heavy: state.activeTab === "model" });
    if (flagship && (state.activeTab === "model" || state.activeTab === "sniper")) {
      loadFlagshipHistory({ silent: true });
    }
  }
}

function loadSavedPicks() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveSavedPicks(picks) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(picks));
  } catch {
    setStatus("號碼已暫存於目前頁面，但瀏覽器拒絕長期儲存。", true);
  }
}

function loadModelSnapshots() {
  try {
    return JSON.parse(localStorage.getItem(MODEL_SNAPSHOT_STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveModelSnapshots(snapshots) {
  localStorage.setItem(MODEL_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshots.slice(0, 160)));
}

function loadApiCacheStore() {
  try {
    return JSON.parse(localStorage.getItem(API_CACHE_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveApiCacheStore(store) {
  try {
    localStorage.setItem(API_CACHE_STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Storage can be full or blocked in private browsing. In-memory cache still works.
  }
}

function readCachedPayload(cacheKey) {
  const memory = state.apiCache.get(cacheKey);
  if (memory) return memory;
  const store = loadApiCacheStore();
  const record = store[cacheKey];
  if (!record?.payload) return null;
  state.apiCache.set(cacheKey, record.payload);
  return record.payload;
}

function writeCachedPayload(cacheKey, payload) {
  state.apiCache.set(cacheKey, payload);
  const store = loadApiCacheStore();
  store[cacheKey] = { savedAt: Date.now(), payload };
  Object.entries(store)
    .sort((a, b) => (b[1].savedAt || 0) - (a[1].savedAt || 0))
    .slice(12)
    .forEach(([key]) => {
      delete store[key];
    });
  saveApiCacheStore(store);
}

function drawKey(draw) {
  if (!draw) return "";
  return `${draw.name || state.game}|${draw.period || ""}|${draw.date || ""}|${(draw.numbers || []).join(".")}`;
}

function taiwanDayKey(value = new Date()) {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (typeof value === "string" && !value.trim()) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Taipei",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(value);
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
    return values.year && values.month && values.day ? `${values.year}-${values.month}-${values.day}` : "";
  } catch {
    return "";
  }
}

function readDailyComparison() {
  try {
    const value = JSON.parse(localStorage.getItem(DAILY_COMPARISON_STORAGE_KEY) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

function ensureDailyComparisonReset() {
  const today = taiwanDayKey();
  const current = readDailyComparison();
  if (current.day === today && current.games && typeof current.games === "object") return false;
  try {
    localStorage.setItem(DAILY_COMPARISON_STORAGE_KEY, JSON.stringify({ day: today, games: {} }));
  } catch {
    // Ignore blocked storage; the page can still compare while it remains open.
  }
  return true;
}

function markDailyComparison(latest) {
  ensureDailyComparisonReset();
  if (!latest || taiwanDayKey(latest.date) !== taiwanDayKey()) return;
  const current = readDailyComparison();
  const games = current.games && typeof current.games === "object" ? current.games : {};
  games[state.game] = drawKey(latest);
  try {
    localStorage.setItem(DAILY_COMPARISON_STORAGE_KEY, JSON.stringify({ day: taiwanDayKey(), games }));
  } catch {
    // Ignore blocked storage; the current render still has the latest draw.
  }
}

function dailyComparisonReady(latest) {
  if (!latest || taiwanDayKey(latest.date) !== taiwanDayKey()) return false;
  const current = readDailyComparison();
  return current.day === taiwanDayKey() && current.games?.[state.game] === drawKey(latest);
}

function readLastSeenDraw() {
  try {
    return JSON.parse(localStorage.getItem(LAST_SEEN_DRAW_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function writeLastSeenDraw(game, latest) {
  const store = readLastSeenDraw();
  store[game] = drawKey(latest);
  try {
    localStorage.setItem(LAST_SEEN_DRAW_STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Ignore blocked storage; notifications still work while the page is open.
  }
}

async function fetchJsonWithTimeout(url, options = {}) {
  const { timeoutMs = FETCH_TIMEOUT_MS, ...fetchOptions } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        "Cache-Control": "no-cache",
        ...(options.headers || {}),
      },
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const error = new Error(
        payload?.error || (response.status === 429 ? "請求太頻繁，請稍後再試。" : `伺服器回應 ${response.status}`),
      );
      error.status = response.status;
      error.retryAfter = response.headers.get("Retry-After");
      throw error;
    }
    if (!payload || typeof payload !== "object") throw new Error("伺服器回傳格式不正確。");
    return payload;
  } finally {
    window.clearTimeout(timer);
  }
}

function loadModelWeights() {
  try {
    const saved = JSON.parse(localStorage.getItem(MODEL_STORAGE_KEY) || "{}");
    return { ...state.modelWeights, ...saved };
  } catch {
    return state.modelWeights;
  }
}

function loadAnalysisFocus() {
  const saved = localStorage.getItem(FOCUS_STORAGE_KEY);
  return FOCUS_PRESETS[saved] ? saved : "balanced";
}

function saveModelWeights() {
  localStorage.setItem(MODEL_STORAGE_KEY, JSON.stringify(state.modelWeights));
  state.candidateCache.clear();
}

function saveAnalysisFocus() {
  localStorage.setItem(FOCUS_STORAGE_KEY, state.analysisFocus);
  state.candidateCache.clear();
}

function normalizedWeights() {
  const raw = state.modelWeights;
  const total = Object.values(raw).reduce((sum, value) => sum + Number(value || 0), 0) || 1;
  return {
    heat: raw.heat / total,
    overdue: raw.overdue / total,
    spread: raw.spread / total,
    backtest: raw.backtest / total,
  };
}

function coreWeightSummary(analysis = state.analysis) {
  const components = Array.isArray(analysis?.flagshipComponents) ? analysis.flagshipComponents : [];
  const text = components
    .map((item) => {
      const weight = Number(item.weight ?? item.baseWeight);
      return item?.label && Number.isFinite(weight) ? `${item.label} ${weight}%` : null;
    })
    .filter(Boolean)
    .join("・");
  return text || "近期熱度 45%・長期熱度 20%・遺漏平衡 15%・區間分布 20%";
}

function renderModelControls() {
  const adaptive = state.analysis?.adaptiveRecentPattern;
  els.modelSummary.textContent = adaptive
    ? `近期版路會依逐期回測自動微調：${coreWeightSummary()}。新一期資料進來後才重新校準。`
    : "核心分析會依近期逐期回測微調權重；回測只作驗證，不代表預測或保證中獎。";
}

function gameLabel(game) {
  return game === "ca-fantasy5" ? "加州天天樂" : "今彩 539";
}

function parseSavedInputs() {
  const numbers = [...new Set(state.savedSelection.map(Number))]
    .filter((number) => Number.isInteger(number) && number >= 1 && number <= 39)
    .sort((left, right) => left - right);
  if (!numbers.length) throw new Error("請先點選至少 1 顆號碼球。");
  return numbers;
}

function fillSavedInputs(numbers) {
  state.savedSelection = [...new Set((numbers || []).map(Number))]
    .filter((number) => Number.isInteger(number) && number >= 1 && number <= 39)
    .sort((left, right) => left - right)
    .slice(0, 39);
  renderSavedNumberPicker();
}

function renderSavedNumberPicker() {
  if (!els.savedPicker) return;
  const selected = new Set(state.savedSelection);
  els.savedPicker.innerHTML = Array.from({ length: 39 }, (_, index) => index + 1)
    .map((number) => {
      const active = selected.has(number);
      return `<button class="saved-number-ball${active ? " is-selected" : ""}" type="button" data-saved-number="${number}" aria-pressed="${active}" aria-label="${pad(number)}號">${pad(number)}</button>`;
    })
    .join("");
  if (els.savedSelectionMeta) {
    els.savedSelectionMeta.textContent = state.savedSelection.length
      ? `已選 ${state.savedSelection.length} 顆：${state.savedSelection.map(pad).join("、")}`
      : "已選 0 顆，請點選號碼球";
  }
  if (els.clearSavedNumbers) els.clearSavedNumbers.disabled = state.savedSelection.length === 0;
}

function matchCount(numbers, winners) {
  const winnerSet = new Set(winners || []);
  return numbers.filter((n) => winnerSet.has(n)).length;
}

function historyCacheKey() {
  const first = state.history[0];
  const last = state.history[state.history.length - 1];
  return [
    state.game,
    state.limit,
    state.history.length,
    first?.period || first?.date || "",
    last?.period || last?.date || "",
  ].join("|");
}

function rememberBacktestResult(key, result) {
  state.backtestCache.set(key, result);
  if (state.backtestCache.size <= MAX_BACKTEST_CACHE_SIZE) return;
  const overflow = state.backtestCache.size - MAX_BACKTEST_CACHE_SIZE;
  Array.from(state.backtestCache.keys())
    .slice(0, overflow)
    .forEach((oldKey) => state.backtestCache.delete(oldKey));
}

function backtestPick(numbers) {
  const cacheKey = `${historyCacheKey()}|${numbers.join(",")}`;
  const cached = state.backtestCache.get(cacheKey);
  if (cached) return cached;

  const distribution = { 0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  let bestHit = 0;
  let bestDraw = null;
  let recentGoodDraw = null;

  state.history.forEach((draw) => {
    const hits = matchCount(numbers, draw.numbers);
    distribution[hits] += 1;
    if (hits > bestHit) {
      bestHit = hits;
      bestDraw = draw;
    }
    if (!recentGoodDraw && hits >= 3) {
      recentGoodDraw = { ...draw, hits };
    }
  });

  const result = {
    distribution,
    bestHit,
    bestDraw,
    recentGoodDraw,
    testedCount: state.history.length,
    profitableCount: distribution[3] + distribution[4] + distribution[5],
  };
  rememberBacktestResult(cacheKey, result);
  return result;
}

function backtestBars(distribution, testedCount) {
  const max = Math.max(...Object.values(distribution), 1);
  return [0, 1, 2, 3, 4, 5]
    .map((hits) => {
      const count = distribution[hits] || 0;
      const width = Math.max(4, Math.round((count / max) * 100));
      const percent = testedCount ? Math.round((count / testedCount) * 100) : 0;
      return `
        <div class="backtest-row">
          <span>${hits} 中</span>
          <span class="bar"><span style="width:${width}%"></span></span>
          <strong>${count} 次</strong>
          <em>${percent}%</em>
        </div>
      `;
    })
    .join("");
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function scorePick(numbers, backtest) {
  const frequencyRows = state.analysis?.frequency || [];
  const stats = new Map(frequencyRows.map((row) => [row.number, row]));
  const maxCount = Math.max(...frequencyRows.map((row) => row.count), 1);
  const maxGap = Math.max(...frequencyRows.map((row) => row.gap), 1);
  const avgCount = numbers.reduce((sum, n) => sum + (stats.get(n)?.count || 0), 0) / numbers.length;
  const avgGap = numbers.reduce((sum, n) => sum + (stats.get(n)?.gap || 0), 0) / numbers.length;
  const heat = clamp(Math.round((avgCount / maxCount) * 100), 0, 100);
  const overdue = clamp(Math.round((avgGap / maxGap) * 100), 0, 100);

  const sorted = [...numbers].sort((a, b) => a - b);
  const span = sorted[sorted.length - 1] - sorted[0];
  const oddCount = numbers.filter((n) => n % 2 === 1).length;
  const zones = new Set(numbers.map((n) => Math.floor((n - 1) / 10))).size;
  const tailCount = new Set(numbers.map((n) => n % 10)).size;
  const repeatCount = state.latest?.numbers ? matchCount(numbers, state.latest.numbers) : 0;
  const balancePenalty = Math.abs(oddCount - 2.5) * 7;
  const repeatPenalty = repeatCount > 2 ? 12 : 0;
  const spread = clamp(
    Math.round((span / 38) * 46 + (zones / 4) * 28 + (tailCount / 5) * 20 + (100 - balancePenalty - repeatPenalty) * 0.06),
    0,
    100,
  );

  const tested = backtest.testedCount || 1;
  const twoPlus = backtest.distribution[2] + backtest.distribution[3] + backtest.distribution[4] + backtest.distribution[5];
  const threePlus = backtest.profitableCount;
  const backtestScore = clamp(
    Math.round((twoPlus / tested) * 72 + (threePlus / tested) * 220 + (backtest.bestHit / 5) * 20),
    0,
    100,
  );

  const hints = patternHints();
  const pairBonus = hints.pairs.some((pair) => pair.every((number) => numbers.includes(number))) ? 5 : 0;
  const dragBonus = Math.min(4, numbers.filter((number) => hints.dragTargets.includes(number)).length * 2);
  const repeatBonus = Math.min(3, numbers.filter((number) => hints.repeatNumbers.includes(number)).length * 1.5);
  const intervalHits = hints.intervals.map((range) => numbers.filter((number) => number >= range.start && number <= range.end).length);
  const intervalBonus = Math.min(5, Math.max(0, ...intervalHits) * 1.6);
  const multiWindowNumbers = new Set((state.analysis?.patterns?.multiWindowNumbers || []).slice(0, 8).map((item) => item.number));
  const signalLeaders = new Set((state.analysis?.patterns?.signalLeaders || []).slice(0, 8).map((item) => item.number));
  const shortTermLeaders = new Set((state.analysis?.shortTermConsensus?.leaders || []).slice(0, 8).map((item) => item.number));
  const crossSignalBonus = Math.min(
    6,
    numbers.filter((number) => multiWindowNumbers.has(number)).length * 0.8 +
      numbers.filter((number) => signalLeaders.has(number)).length * 0.65 +
      numbers.filter((number) => shortTermLeaders.has(number)).length * 0.55,
  );
  const shortCycleBonus =
    state.game === "ca-fantasy5"
      ? Math.min(
          8,
          numbers.filter((number) => hints.shortCycle.aroundNumbers.includes(number)).length * 2.2 +
            numbers.filter((number) => hints.shortCycle.edgeNumbers.includes(number)).length * 1.6 +
            numbers.filter((number) => hints.shortCycle.anchorNumbers.includes(number)).length * 0.9,
        )
      : 0;
  const patternBonus = pairBonus + dragBonus + repeatBonus + intervalBonus + shortCycleBonus + crossSignalBonus;
  const weights = normalizedWeights();
  const total = clamp(
    Math.round(heat * weights.heat + overdue * weights.overdue + spread * weights.spread + backtestScore * weights.backtest + patternBonus),
    0,
    100,
  );
  const label = total >= 75 ? "高追蹤" : total >= 55 ? "可觀察" : "保守";
  return {
    total,
    heat,
    overdue,
    spread,
    backtest: backtestScore,
    pattern: Math.round(patternBonus),
    interval: Math.round(intervalBonus),
    shortCycle: Math.round(shortCycleBonus),
    crossSignal: Math.round(crossSignalBonus),
    label,
  };
}

function scoreDetails(score) {
  return [
    ["熱度", score.heat],
    ["遺漏", score.overdue],
    ["分散", score.spread],
    ["回測", score.backtest],
    ["區間", score.interval || 0],
    ["版路", score.pattern || 0],
    ["交叉", score.crossSignal || 0],
  ]
    .map(
      ([label, value]) => `
        <div class="score-row">
          <span>${label}</span>
          <span class="bar"><span style="width:${value}%"></span></span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join("");
}

function hotTailProfile() {
  const recentDraws = state.history.slice(0, Math.min(state.history.length, 30));
  const counts = Array.from({ length: 10 }, (_, tail) => ({ tail, count: 0 }));
  recentDraws.forEach((draw) => {
    draw.numbers.forEach((number) => {
      counts[number % 10].count += 1;
    });
  });
  const sorted = counts.sort((a, b) => b.count - a.count || a.tail - b.tail);
  const size = state.analysisFocus === "pattern" || state.analysisFocus === "interval" ? 6 : 7;
  const hotTails = sorted.slice(0, size).map((item) => item.tail);
  return {
    hotTails,
    label: hotTails.map((tail) => `${tail}尾`).join("、"),
  };
}

function shouldFilterByHotTail() {
  if (state.game === "ca-fantasy5") return false;
  return ["hot", "pattern", "interval"].includes(state.analysisFocus);
}

function shortCycleProfile() {
  if (state.game !== "ca-fantasy5") {
    return { aroundNumbers: [], edgeNumbers: [], anchorNumbers: [], label: "" };
  }
  const recent = state.history.slice(0, 10);
  const aroundScore = new Map();
  const anchorScore = new Map();
  recent.forEach((draw, drawIndex) => {
    const recencyWeight = drawIndex < 3 ? 3 : drawIndex < 6 ? 2 : 1;
    draw.numbers.forEach((number) => {
      anchorScore.set(number, (anchorScore.get(number) || 0) + recencyWeight);
      [-2, -1, 1, 2].forEach((offset) => {
        const nearby = number + offset;
        if (nearby >= 1 && nearby <= 39) {
          const distanceWeight = Math.abs(offset) === 1 ? 1 : 0.65;
          aroundScore.set(nearby, (aroundScore.get(nearby) || 0) + recencyWeight * distanceWeight);
        }
      });
    });
  });
  const edgeNumbers = Array.from({ length: 39 }, (_, index) => index + 1).filter((number) => number <= 5 || number >= 35);
  const sortByScore = (scoreMap) =>
    [...scoreMap.entries()]
      .sort((a, b) => b[1] - a[1] || a[0] - b[0])
      .map(([number]) => number);
  const aroundNumbers = sortByScore(aroundScore).filter((number) => !anchorScore.has(number)).slice(0, 16);
  const anchorNumbers = sortByScore(anchorScore).slice(0, 12);
  const label = `近10期環繞 ${aroundNumbers.slice(0, 6).map(pad).join("、")}；邊線 ${edgeNumbers.slice(0, 5).map(pad).join("、")}/${edgeNumbers.slice(-5).map(pad).join("、")}`;
  return { aroundNumbers, edgeNumbers, anchorNumbers, label };
}

function patternHints() {
  const patterns = state.analysis?.patterns || {};
  const pairs = (patterns.pairCombos || []).map((item) => item.numbers || []).filter((pair) => pair.length === 2);
  const dragTargets = [...new Set((patterns.dragCards || []).map((item) => item.follow).filter(Boolean))];
  const intervals = (patterns.intervals || []).slice(0, 3).filter((item) => item.start && item.end);
  const intervalNumbers = [
    ...new Set(intervals.flatMap((item) => Array.from({ length: item.end - item.start + 1 }, (_, index) => item.start + index))),
  ];
  const repeatNumbers = [
    ...new Set(
      (patterns.repeatCandidates || [])
        .filter((item) => item.count > 0 || item.rate > 0)
        .slice(0, 3)
        .map((item) => item.number),
    ),
  ];
  const windowNumbers = (patterns.multiWindowNumbers || []).slice(0, 8).map((item) => item.number);
  const signalNumbers = (patterns.signalLeaders || []).slice(0, 8).map((item) => item.number);
  const shortTermNumbers = (state.analysis?.shortTermConsensus?.leaders || []).slice(0, 8).map((item) => item.number);
  const shortCycle = shortCycleProfile();
  return { pairs, dragTargets, intervalNumbers, intervals, repeatNumbers, windowNumbers, signalNumbers, shortTermNumbers, shortCycle };
}

function candidatePool() {
  const rows = state.analysis?.frequency || [];
  if (!rows.length) return Array.from({ length: 39 }, (_, i) => i + 1);
  const hotSize = state.analysisFocus === "hot" ? 22 : 14;
  const overdueSize = state.analysisFocus === "overdue" ? 22 : 14;
  const balancedSize = state.analysisFocus === "pattern" || state.analysisFocus === "interval" ? 28 : 20;
  const { hotTails } = hotTailProfile();
  const hotTailSet = new Set(hotTails);
  const tailFilteredUniverse = Array.from({ length: 39 }, (_, i) => i + 1).filter((number) => hotTailSet.has(number % 10));
  const filterByTail = shouldFilterByHotTail();
  const numberAllowed = (number) => !filterByTail || hotTailSet.has(number % 10);
  const hot = [...rows].sort((a, b) => b.count - a.count || a.number - b.number).slice(0, hotSize).map((row) => row.number);
  const overdue = [...rows].sort((a, b) => b.gap - a.gap || a.number - b.number).slice(0, overdueSize).map((row) => row.number);
  const balanced = [...rows]
    .map((row) => ({ ...row, weight: row.count * 0.55 + row.gap * 0.45 }))
    .sort((a, b) => b.weight - a.weight || a.number - b.number)
    .slice(0, balancedSize)
    .map((row) => row.number);
  const hints = patternHints();
  const shortCycleNumbers =
    state.game === "ca-fantasy5"
      ? [...hints.shortCycle.aroundNumbers, ...hints.shortCycle.edgeNumbers, ...hints.shortCycle.anchorNumbers]
      : [];
  const patternNumbers = [
    ...hints.pairs.flat(),
    ...hints.dragTargets,
    ...hints.intervalNumbers,
    ...hints.repeatNumbers,
    ...hints.windowNumbers,
    ...hints.signalNumbers,
    ...hints.shortTermNumbers,
    ...shortCycleNumbers,
  ];
  const pool = [...new Set([...hot, ...overdue, ...balanced, ...patternNumbers, ...tailFilteredUniverse])].filter(numberAllowed);
  if (pool.length >= 12) return pool;
  return filterByTail ? tailFilteredUniverse : Array.from({ length: 39 }, (_, i) => i + 1);
}

function randomChoice(items, rng = Math.random) {
  return items[Math.floor(rng() * items.length)];
}

function buildCandidate(pool, rng = Math.random) {
  if (!Array.isArray(pool) || pool.length < 5) return [];
  const numbers = new Set();
  const frequencyRows = state.analysis?.frequency || [];
  const stats = new Map(frequencyRows.map((row) => [row.number, row]));
  const poolSet = new Set(pool);
  const hints = patternHints();
  const pairChoices = hints.pairs.filter((pair) => pair.every((number) => poolSet.has(number)));
  const dragTargets = hints.dragTargets.filter((number) => poolSet.has(number));
  const intervalNumbers = hints.intervalNumbers.filter((number) => poolSet.has(number));
  const repeatNumbers = hints.repeatNumbers.filter((number) => poolSet.has(number));
  const shortAround = hints.shortCycle.aroundNumbers.filter((number) => poolSet.has(number));
  const shortEdges = hints.shortCycle.edgeNumbers.filter((number) => poolSet.has(number));
  const shortAnchors = hints.shortCycle.anchorNumbers.filter((number) => poolSet.has(number));
  const classicList = [...frequencyRows]
    .map((row) => ({ n: row.number, score: row.count * 0.45 + row.gap * 0.27 + rng() * 5 }))
    .filter((item) => poolSet.has(item.n))
    .sort((a, b) => b.score - a.score || a.n - b.n)
    .slice(0, 22)
    .map((item) => item.n);
  const hotList = [...frequencyRows]
    .sort((a, b) => b.count - a.count || a.number - b.number)
    .map((row) => row.number)
    .filter((number) => poolSet.has(number))
    .slice(0, 18);
  const overdueList = [...frequencyRows]
    .sort((a, b) => b.gap - a.gap || a.number - b.number)
    .map((row) => row.number)
    .filter((number) => poolSet.has(number))
    .slice(0, 18);
  const focus = state.analysisFocus;
  const addRandomUntil = (targetSize, choices) => {
    const available = [...new Set(choices)].filter((number) => poolSet.has(number) && !numbers.has(number));
    while (numbers.size < targetSize && available.length) {
      const index = Math.floor(rng() * available.length);
      numbers.add(available.splice(index, 1)[0]);
    }
  };
  const zones = [
    pool.filter((n) => n <= 10),
    pool.filter((n) => n >= 11 && n <= 20),
    pool.filter((n) => n >= 21 && n <= 30),
    pool.filter((n) => n >= 31),
  ].filter(Boolean);

  zones.forEach((zone) => {
    const chance = focus === "pattern" || focus === "interval" ? 0.88 : 0.72;
    if (numbers.size < 5 && zone.length && rng() < chance) {
      numbers.add(randomChoice(zone, rng));
    }
  });
  if ((focus === "pattern" || focus === "interval" || rng() < 0.55) && pairChoices.length && numbers.size <= 3) {
    randomChoice(pairChoices, rng).forEach((number) => numbers.add(number));
  }
  if (dragTargets.length && numbers.size < 5 && rng() < 0.72) {
    numbers.add(randomChoice(dragTargets, rng));
  }
  if (intervalNumbers.length && numbers.size < 5 && rng() < 0.78) {
    numbers.add(randomChoice(intervalNumbers, rng));
  }
  if ((focus === "pattern" || focus === "interval") && intervalNumbers.length) {
    addRandomUntil(3, intervalNumbers);
  }
  if (repeatNumbers.length && numbers.size < 5 && rng() < 0.45) {
    numbers.add(randomChoice(repeatNumbers, rng));
  }
  if (state.game === "ca-fantasy5") {
    addRandomUntil(2, shortAround);
    if (shortEdges.length && numbers.size < 5 && rng() < 0.72) {
      numbers.add(randomChoice(shortEdges, rng));
    }
    if (shortAnchors.length && numbers.size < 5 && rng() < 0.5) {
      numbers.add(randomChoice(shortAnchors, rng));
    }
    if (shortAround.length && numbers.size < 4 && rng() < 0.85) {
      numbers.add(randomChoice(shortAround, rng));
    }
  }
  if (focus === "classic" && classicList.length) {
    addRandomUntil(4, classicList);
  }
  if (focus === "hot" && hotList.length) {
    addRandomUntil(3, hotList);
  }
  if (focus === "overdue" && overdueList.length) {
    addRandomUntil(3, overdueList);
  }
  if (focus === "backtest") {
    const top = [...pool]
      .map((n) => {
        const row = stats.get(n) || { count: 0, gap: 0 };
        return { n, score: row.count * 0.35 + row.gap * 0.25 + rng() * 8 };
      })
      .sort((a, b) => b.score - a.score)
      .slice(0, 20)
      .map((item) => item.n);
    addRandomUntil(4, top);
  }
  addRandomUntil(5, pool);
  return [...numbers].sort((a, b) => a - b).slice(0, 5);
}

function generateCandidates() {
  const cacheKey = `${state.game}-${state.limit}-${state.latest?.date || ""}-${state.latest?.period || ""}-${state.analysisFocus}-${JSON.stringify(state.modelWeights)}`;
  const cached = state.candidateCache.get(cacheKey);
  if (cached) return cached;
  try {
    const pool = candidatePool();
    const seed = `${cacheKey}-${state.history[0]?.numbers?.join(".") || ""}`;
    const rng = createRng(seed);
    const seen = new Set();
    const candidates = [];
    const attempts = state.analysisFocus === "backtest" ? CANDIDATE_ATTEMPTS + 24 : CANDIDATE_ATTEMPTS;
    for (let i = 0; i < attempts; i += 1) {
      const numbers = buildCandidate(pool, rng);
      if (numbers.length !== 5) continue;
      const key = numbers.join(",");
      if (seen.has(key)) continue;
      seen.add(key);
      const backtest = backtestPick(numbers);
      const score = scorePick(numbers, backtest);
      candidates.push({ numbers, backtest, score });
    }
    const result = candidates
      .sort((a, b) => b.score.total - a.score.total || b.backtest.bestHit - a.backtest.bestHit)
      .slice(0, 5);
    state.candidateCache.set(cacheKey, result);
    return result;
  } catch (error) {
    console.error("Candidate generation failed", error);
    return [];
  }
}

function withTemporaryFocus(focusKey, callback) {
  const previousFocus = state.analysisFocus;
  const previousWeights = { ...state.modelWeights };
  const preset = FOCUS_PRESETS[focusKey] || FOCUS_PRESETS.balanced;
  state.analysisFocus = focusKey;
  state.modelWeights = { ...preset.weights };
  try {
    return callback();
  } finally {
    state.analysisFocus = previousFocus;
    state.modelWeights = previousWeights;
  }
}

function modeSnapshotCandidates() {
  if (!state.analysis || !state.history.length) return [];
  return MODE_SNAPSHOT_KEYS.map((key) => {
    const preset = FOCUS_PRESETS[key];
    const candidate = withTemporaryFocus(key, () => generateCandidates()[0]);
    return candidate ? { key, preset, candidate } : null;
  }).filter(Boolean);
}

function rememberModelSnapshots() {
  if (!state.latest || !state.history.length || !isProPlan()) return;
  const currentKey = `${state.game}-${state.latest.period || state.latest.date || ""}`;
  const existing = loadModelSnapshots().filter((item) => item.key !== currentKey);
  const snapshots = modeSnapshotCandidates().map(({ key, preset, candidate }) => ({
    key: currentKey,
    game: state.game,
    basePeriod: state.latest.period || "",
    baseDate: state.latest.date || "",
    modeKey: key,
    mode: preset.label,
    pick: candidate.numbers,
    score: candidate.score.total,
    createdAt: new Date().toISOString(),
  }));
  saveModelSnapshots([...snapshots, ...existing]);
}

function validationRowsForLatest() {
  if (!state.latest) return [];
  const latestActual = state.latest.numbers || [];
  const manualRows = VALIDATION_RECORDS.filter((record) => {
    const sameGame = record.game === state.game;
    const sameDate = record.date && record.date === state.latest.date;
    const samePeriod = record.period && record.period === state.latest.period;
    const sameActual = record.actual?.join(",") === latestActual.join(",");
    return sameGame && (sameDate || samePeriod || sameActual);
  }).map((record) => ({
    date: record.date,
    period: record.period,
    mode: record.mode,
    source: record.source,
    pick: record.pick,
    actual: record.actual,
    hits: matchCount(record.pick, record.actual),
  }));

  const trackedRows = loadModelSnapshots()
    .filter((item) => item.game === state.game && item.basePeriod !== state.latest.period && item.baseDate !== state.latest.date)
    .slice(0, 7)
    .map((item) => ({
      date: state.latest.date,
      period: state.latest.period,
      mode: item.mode,
      source: `由 ${item.baseDate || item.basePeriod || "上一期"} 留存`,
      pick: item.pick,
      actual: latestActual,
      hits: matchCount(item.pick, latestActual),
    }))
    .filter((row) => row.hits >= 2)
    .sort((a, b) => b.hits - a.hits);

  const unique = new Set();
  return [...manualRows, ...trackedRows].filter((row) => {
    const key = `${row.mode}-${row.pick.join(",")}-${row.actual.join(",")}`;
    if (unique.has(key)) return false;
    unique.add(key);
    return true;
  });
}

function referenceCandidate() {
  const numbers = normalizedPick(state.analysis?.recommendation);
  if (numbers.length !== 5) return null;
  const backtest = backtestPick(numbers);
  return { numbers, backtest, score: { total: "核心", pattern: 0 } };
}

function currentReferenceNumbers() {
  return referenceCandidate()?.numbers || [];
}

function renderReferencePick() {
  const candidate = referenceCandidate();
  if (!candidate) {
    els.pickBalls.innerHTML = "";
    els.pickMeta.innerHTML = "";
    return;
  }
  els.pickBalls.innerHTML = balls(candidate.numbers);
  els.pickMeta.innerHTML = `
    <span>核心分析</span>
    <span>${coreWeightSummary()}</span>
    <span>最高 ${candidate.backtest.bestHit} 中</span>
    <span>${state.analysis?.adaptiveRecentPattern?.reason || "新一期資料進來後自動校準版路權重"}</span>
  `;
}

function renderFlagshipPick() {
  if (!els.flagshipBalls || !els.flagshipMeta) return;
  if (!isFlagshipPlan()) {
    els.flagshipBalls.innerHTML = "";
    els.flagshipMeta.innerHTML = "<span>量化旗艦版會員專屬</span>";
    return;
  }
  const numbers = normalizedPick(state.analysis?.flagshipRecommendation);
  if (numbers.length !== 5) {
    els.flagshipBalls.innerHTML = "";
    els.flagshipMeta.innerHTML = "<span>資料累積中，暫時無法產生 5 碼候選池。</span>";
  } else {
    const flagshipMethod = state.analysis?.flagshipMethod || `核心分析：${coreWeightSummary()}`;
    const adaptiveReason = state.analysis?.adaptiveRecentPattern?.reason || "新一期資料進來後自動校準版路權重";
    els.flagshipBalls.innerHTML = `
      <div class="flagship-star-shape" role="img" aria-label="五芒星摘星五碼">
        ${balls(numbers)}
      </div>
    `;
    els.flagshipMeta.innerHTML = `
      <span class="flagship-window-note">${flagshipMethod}</span>
      <span>${adaptiveReason}</span>
      <span>僅供統計參考，不代表保證中獎</span>
    `;
  }
}

function renderCoreCandidatePool() {
  if (!els.coreCandidateBalls || !els.coreCandidateMeta || !els.coreCandidateSave) return;
  if (!isFlagshipPlan()) {
    els.coreCandidateBalls.innerHTML = "";
    els.coreCandidateMeta.textContent = "旗艦會員專屬";
    els.coreCandidateSave.disabled = true;
    return;
  }
  const pool = Array.isArray(state.analysis?.coreCandidatePool) ? state.analysis.coreCandidatePool : [];
  if (pool.length < 15) {
    els.coreCandidateBalls.innerHTML = "";
    els.coreCandidateMeta.textContent = "資料累積中，暫時無法產生候選 15 碼。";
    els.coreCandidateSave.disabled = true;
    return;
  }
  const allowed = new Set(pool.map((item) => Number(item.number)));
  state.coreCandidateSelection = state.coreCandidateSelection.filter((number) => allowed.has(number)).slice(0, 5);
  els.coreCandidateBalls.innerHTML = pool
    .map((item) => {
      const number = Number(item.number);
      const selected = state.coreCandidateSelection.includes(number);
      const core = item.isCorePick ? " is-core-pick" : "";
      return `<button class="core-candidate-ball${selected ? " is-selected" : ""}${core}" type="button" data-core-number="${number}" aria-pressed="${selected}" title="${item.isCorePick ? "核心推薦號碼" : "邏輯候選號碼"}">${pad(number)}</button>`;
    })
    .join("");
  els.coreCandidateMeta.textContent = `已選 ${state.coreCandidateSelection.length}／5；金色外框為核心推薦五碼`;
  els.coreCandidateSave.disabled = state.coreCandidateSelection.length !== 5;
  els.coreCandidateBalls.querySelectorAll("[data-core-number]").forEach((button) => {
    button.addEventListener("click", () => {
      const number = Number(button.dataset.coreNumber);
      if (state.coreCandidateSelection.includes(number)) {
        state.coreCandidateSelection = state.coreCandidateSelection.filter((item) => item !== number);
      } else if (state.coreCandidateSelection.length < 5) {
        state.coreCandidateSelection = [...state.coreCandidateSelection, number].sort((a, b) => a - b);
      } else {
        setStatus("最多選 5 顆號碼。", true);
        return;
      }
      renderCoreCandidatePool();
    });
  });
}

function savePick(numbers) {
  const normalized = [...new Set((numbers || []).map(Number))]
    .filter((number) => Number.isInteger(number) && number >= 1 && number <= 39)
    .sort((a, b) => a - b);
  if (!normalized.length || normalized.length > 39) {
    setStatus("自選號碼請選 1 到 39 顆。", true);
    return false;
  }
  const picks = loadSavedPicks();
  const duplicate = picks.some((pick) => pick.game === state.game && pick.numbers.join(",") === normalized.join(","));
  if (duplicate) {
    setStatus("這組號碼已經儲存過。", true);
    return false;
  }
  picks.unshift({
    id: crypto.randomUUID ? crypto.randomUUID() : String(Date.now()),
    game: state.game,
    numbers: normalized,
    createdAt: new Date().toISOString(),
  });
  saveSavedPicks(picks.slice(0, 80));
  syncSavedPicksToServer().catch(() => {});
  renderSavedPicks();
  setStatus(`已儲存 ${gameLabel(state.game)}：${normalized.map(pad).join(" · ")}`);
  return true;
}

function renderCandidates() {
  if (!els.candidates) return;
  if (!isProPlan()) {
    els.candidates.innerHTML = `<div class="empty-state">高分組合排序屬於 Pro 訂閱版；目前會保留上方一組統計參考選號。</div>`;
    return;
  }
  if (!state.analysis || !state.history.length) {
    els.candidates.innerHTML = `<div class="empty-state">資料讀取後會產生候選組合。</div>`;
    return;
  }
  const candidates = CANDIDATE_LOGIC_PRESETS.map((preset) => {
    const candidate = withTemporaryFocus(preset.key, () => generateCandidates()[0]);
    return candidate ? { ...preset, candidate } : null;
  }).filter(Boolean);
  if (!candidates.length) {
    els.candidates.innerHTML = `<div class="empty-state">回測驗證暫時忙碌，請稍後再按一次重新產生。</div>`;
    return;
  }
  els.candidates.innerHTML = candidates
    .map(
      ({ key, label, description, candidate }) => `
        <div class="candidate-item" data-logic="${key}">
          <div class="candidate-copy">
            <div class="candidate-title-row">
              <strong class="candidate-name">${label}</strong>
              <span class="candidate-logic-tag">邏輯推理</span>
            </div>
            <p class="candidate-description">${description}</p>
            <div class="saved-balls">${miniBalls(candidate.numbers)}</div>
            <div class="candidate-meta">
              <span>${candidate.score.total} · ${candidate.score.label}</span>
              <span>版路 +${candidate.score.pattern || 0}</span>
              <span>最高 ${candidate.backtest.bestHit} 中</span>
              <span>3 中以上 ${candidate.backtest.profitableCount} 次</span>
            </div>
          </div>
          <button class="save-candidate" data-candidate="${candidate.numbers.join(",")}">儲存</button>
        </div>
      `,
    )
    .join("");

  els.candidates.querySelectorAll("[data-candidate]").forEach((button) => {
    button.addEventListener("click", () => {
      const numbers = button.dataset.candidate.split(",").map(Number);
      savePick(numbers);
    });
  });
}

function renderModeSnapshots() {
  if (!els.modeSnapshots) return;
  if (!isProPlan()) {
    els.modeSnapshots.innerHTML = `<div class="empty-state">各模式快照屬於 Pro 訂閱版；可先用「預覽 Pro」查看。</div>`;
    return;
  }
  if (!state.analysis || !state.history.length) {
    els.modeSnapshots.innerHTML = `<div class="empty-state">資料讀取後會顯示每個模式的候選組合。</div>`;
    return;
  }

  const activeFocus = state.analysisFocus;
  const snapshots = modeSnapshotCandidates();
  if (!snapshots.length) {
    els.modeSnapshots.innerHTML = `<div class="empty-state">模式回測暫時忙碌，請稍後再切換一次。</div>`;
    return;
  }
  els.modeSnapshots.innerHTML = snapshots
    .map(({ key, preset, candidate }) => {
      const isActive = key === activeFocus;
      const isInterval = key === "interval";
      const recentGood = candidate.backtest.recentGoodDraw;
      const latestHits = state.latest?.numbers ? matchCount(candidate.numbers, state.latest.numbers) : 0;
      const recentText = recentGood ? `近中 ${recentGood.hits}：${recentGood.date || recentGood.period || "-"}` : "近中待觀察";
      return `
        <button class="mode-snapshot-card ${isActive ? "active" : ""} ${isInterval ? "featured" : ""}" type="button" data-mode-snapshot="${key}">
          <span class="mode-snapshot-kicker">${isInterval ? "同類版路" : "模式"}</span>
          <strong>${preset.label}</strong>
          <span class="mode-snapshot-desc">${preset.description}</span>
          <span class="saved-balls">${miniBalls(candidate.numbers)}</span>
          <span class="candidate-meta">
            <span>本期 ${latestHits} 中</span>
            <span>分數 ${candidate.score.total}</span>
            <span>版路 +${candidate.score.pattern || 0}</span>
            <span>最高 ${candidate.backtest.bestHit} 中</span>
            <span>3 中以上 ${candidate.backtest.profitableCount} 次</span>
            <span>${recentText}</span>
          </span>
        </button>
      `;
    })
    .join("");

  els.modeSnapshots.querySelectorAll("[data-mode-snapshot]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!requirePro("模式版路切換")) return;
      const focusKey = button.dataset.modeSnapshot;
      const preset = FOCUS_PRESETS[focusKey];
      if (!preset) return;
      state.analysisFocus = focusKey;
      state.modelWeights = { ...preset.weights };
      saveAnalysisFocus();
      saveModelWeights();
      renderModelControls();
      scheduleModelRender(`已切換到 ${preset.label} 模式。`);
    });
  });
}

function renderModelOutput({ heavy = state.activeTab === "model" } = {}) {
  renderSavedPicks();
  renderReferencePick();
  renderCoreCandidatePool();
  if (els.candidates) {
    if (heavy) {
      renderCandidates();
    } else {
      els.candidates.innerHTML = `<div class="empty-state">切換到「邏輯推理」分頁後載入高分組合。</div>`;
    }
  }
  if (els.modeSnapshots) els.modeSnapshots.innerHTML = "";
}

function scheduleModelRender(message = "邏輯推理設定已更新。") {
  if (state.modelRenderTimer) {
    window.clearTimeout(state.modelRenderTimer);
  }
  setStatus("邏輯推理正在重新計算...");
  state.modelRenderTimer = window.setTimeout(() => {
    state.modelRenderTimer = null;
    renderModelOutput();
    setStatus(message);
  }, MODEL_RENDER_DEBOUNCE_MS);
}

function renderModelBacktest(backtest, profiles = []) {
  syncBacktestControls();
  if (!backtest || !backtest.testedCount) {
    els.backtestBadge.textContent = "資料不足";
    els.avgHit.textContent = "-";
    els.threePlusRate.textContent = "-";
    els.bestHit.textContent = "-";
    els.backtestRecent.innerHTML = `<div class="empty-state">累積更多期數後會顯示回測驗證。</div>`;
    els.backtestMethod.textContent = "";
    return;
  }
  const requestedCount = backtest.requestedCount || state.backtestLimit;
  els.backtestBadge.textContent = `${backtest.testedCount}/${requestedCount} 期`;
  els.avgHit.textContent = backtest.averageHit;
  els.threePlusRate.textContent = `${backtest.onePlusRate ?? 0}%`;
  els.bestHit.textContent = `${backtest.bestHit} 中`;
  const ranking = profiles
    .slice(0, 5)
    .map(
      (profile, index) => `
        <div class="model-rank ${index === 0 ? "best" : ""}">
          <div>
            <strong>${index + 1}. ${profile.label}</strong>
            <span>均中 ${profile.averageHit} · 摸邊 ${profile.onePlusRate ?? 0}% · 2中+ ${profile.twoPlusRate ?? 0}%</span>
          </div>
          <em>${profile.bestHit} 中</em>
        </div>
      `,
    )
    .join("");
  const distribution = backtest.distribution || {};
  const twoPlusText = `${backtest.twoPlusRate ?? 0}%`;
  const threePlusText = `${backtest.threePlusRate ?? 0}%`;
  const warning =
    (backtest.threePlusRate ?? 0) === 0
      ? `<div class="backtest-warning">最近 ${backtest.testedCount} 期沒有 3 中以上，這時候先看摸邊率和 2 中以上，比只盯 3 中更準。</div>`
      : "";
  const validationRows = validationRowsForLatest();
  const validationHtml = validationRows.length
    ? `
      <div class="hit-track-list">
        <div class="hit-track-title">
          <strong>命中追蹤</strong>
          <span>含舊版截圖補登與之後自動留存的推薦快照</span>
        </div>
        ${validationRows
          .map(
            (row) => `
              <div class="backtest-card hit-track-card ${row.hits >= 4 ? "strong" : ""}">
                <div>
                  <strong>${row.mode}</strong>
                  <span>${row.source}</span>
                  <span>${row.date || "-"} · 期別 ${row.period || "-"}</span>
                </div>
                <div>
                  <span class="tiny-label">當時畫面</span>
                  <div class="saved-balls">${miniBalls(row.pick, row.actual)}</div>
                </div>
                <div>
                  <span class="tiny-label">實際開獎</span>
                  <div class="saved-balls">${miniBalls(row.actual)}</div>
                </div>
                <div class="hit-chip">${row.hits} 中</div>
              </div>
            `,
          )
          .join("")}
      </div>
    `
    : "";
  els.backtestMethod.innerHTML = `
    ${backtest.method}
    <span class="backtest-method-line">回測設定：近 ${requestedCount} 期（可填 7～365）；實際可測資料不足時會以現有資料計算。</span>
    <span class="backtest-method-line">命中分布：0中 ${distribution[0] || 0}、1中 ${distribution[1] || 0}、2中 ${distribution[2] || 0}、3中以上 ${backtest.threePlusCount || 0}。2中以上 ${twoPlusText}，3中以上 ${threePlusText}。</span>
  `;
  els.backtestRecent.innerHTML = backtest.recentRows
    .map(
      (row) => `
        <div class="backtest-card">
          <div>
            <strong>${row.date}</strong>
            <span>期別 ${row.period}</span>
          </div>
          <div>
            <span class="tiny-label">當時推薦</span>
            <div class="saved-balls">${miniBalls(row.pick, row.actual)}</div>
          </div>
          <div>
            <span class="tiny-label">實際開獎</span>
            <div class="saved-balls">${miniBalls(row.actual)}</div>
          </div>
          <div class="hit-chip">${row.hits} 中</div>
        </div>
      `,
    )
    .join("");
  if (validationHtml || ranking || warning) {
    els.backtestRecent.insertAdjacentHTML("afterbegin", `${validationHtml}${warning}<div class="model-rank-list">${ranking}</div>`);
  }
}

function recentPatternWindow(draws, size) {
  const rows = draws.slice(0, size);
  if (!rows.length) return null;
  const totalNumbers = rows.length * 5;
  const zoneLabels = ["01-10", "11-20", "21-30", "31-39"];
  const zoneCounts = [0, 0, 0, 0];
  const tailCounts = new Map();
  const oddCounts = new Map();
  const consecutiveCounts = [];

  rows.forEach((draw) => {
    const numbers = normalizedPick(draw.numbers, 5);
    numbers.forEach((number) => {
      zoneCounts[Math.min(3, Math.floor((number - 1) / 10))] += 1;
      const tail = number % 10;
      tailCounts.set(tail, (tailCounts.get(tail) || 0) + 1);
    });
    const odd = numbers.filter((number) => number % 2 === 1).length;
    oddCounts.set(odd, (oddCounts.get(odd) || 0) + 1);
    consecutiveCounts.push(numbers.filter((number, index) => index > 0 && number - numbers[index - 1] === 1).length);
  });

  const transitions = rows.slice(0, -1).map((draw, index) => {
    const current = new Set(normalizedPick(draw.numbers, 5));
    const previous = new Set(normalizedPick(rows[index + 1].numbers, 5));
    return [...current].filter((number) => previous.has(number)).length;
  });
  const average = (values) => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
  const percentage = (value, total) => total ? Math.round((value / total) * 100) : 0;
  const mode = (map) => [...map.entries()].sort((left, right) => right[1] - left[1] || left[0] - right[0])[0] || [0, 0];
  const zoneIndex = zoneCounts.reduce((best, count, index) => count > zoneCounts[best] ? index : best, 0);
  const topTails = [...tailCounts.entries()]
    .sort((left, right) => right[1] - left[1] || left[0] - right[0])
    .slice(0, 3);
  const [oddMode, oddModeCount] = mode(oddCounts);
  const repeatAverage = average(transitions);
  const consecutiveRate = percentage(consecutiveCounts.filter((count) => count > 0).length, rows.length);
  const repeatRate = percentage(transitions.filter((count) => count > 0).length, transitions.length);
  const tailShare = topTails.length ? topTails[0][1] / totalNumbers : 0;
  const zoneShare = zoneCounts[zoneIndex] / totalNumbers;

  return {
    size: rows.length,
    requestedSize: size,
    latestRepeat: transitions[0] || 0,
    repeatAverage,
    repeatRate,
    consecutiveRate,
    oddMode,
    oddModeCount,
    oddModeRate: percentage(oddModeCount, rows.length),
    zone: zoneLabels[zoneIndex],
    zoneShare,
    topTails,
    tailShare,
    sumAverage: Math.round(average(rows.map((draw) => normalizedPick(draw.numbers, 5).reduce((sum, number) => sum + number, 0)))),
  };
}

function buildRecentPatternAutoAnalysis() {
  const draws = (state.history || []).filter((draw) => normalizedPick(draw.numbers, 5).length === 5);
  const windows = [10, 20, 36]
    .map((size) => recentPatternWindow(draws, size))
    .filter(Boolean);
  if (!windows.length) return null;

  const recent = windows[0];
  const longest = windows[windows.length - 1];
  const signals = [
    {
      key: "repeat",
      title: "連莊走勢",
      score: recent.repeatRate + recent.repeatAverage * 10,
      value: `近 ${recent.requestedSize} 期 ${recent.repeatAverage.toFixed(2)} 顆／期`,
      description: `最近一期與前一期重疊 ${recent.latestRepeat} 顆，觀察是否延續。`,
    },
    {
      key: "consecutive",
      title: "連號走勢",
      score: recent.consecutiveRate,
      value: `${recent.consecutiveRate}% 有連號`,
      description: "統計每期是否出現相鄰號碼，避免只看單一期。",
    },
    {
      key: "interval",
      title: "區間集中",
      score: recent.zoneShare * 100,
      value: `${recent.zone} 占 ${Math.round(recent.zoneShare * 100)}%`,
      description: "找出近期號碼較集中的區間，作為版路觀察。",
    },
    {
      key: "tail",
      title: "尾數聚焦",
      score: recent.tailShare * 100,
      value: `${recent.topTails.map(([tail]) => `${tail}尾`).join("、") || "資料不足"}`,
      description: "整理近期出現較多的尾數，僅作輔助訊號。",
    },
    {
      key: "oddEven",
      title: "奇偶結構",
      score: recent.oddModeRate,
      value: `${recent.oddMode} 奇 ${5 - recent.oddMode} 偶`,
      description: `近 ${recent.requestedSize} 期最常見的奇偶配置。`,
    },
  ].sort((left, right) => right.score - left.score || left.key.localeCompare(right.key));

  const lead = signals[0];
  const repeatDelta = recent.repeatAverage - longest.repeatAverage;
  const trend = repeatDelta >= 0.25
    ? "近期連莊比近 36 期平均更活躍。"
    : repeatDelta <= -0.25
      ? "近期連莊比近 36 期平均收斂，先保留觀察。"
      : "近 10 期與近 36 期的連莊強度接近。";
  return { windows, signals, lead, trend };
}

function renderRecentPatternAuto() {
  if (!els.recentPatternAutoSummary) return;
  const result = buildRecentPatternAutoAnalysis();
  if (!result) {
    els.recentPatternAutoBadge.textContent = "資料累積中";
    els.recentPatternAutoSummary.innerHTML = `<div class="empty-state">資料累積後會自動整理近期版路。</div>`;
    els.recentPatternAutoSignals.innerHTML = "";
    els.recentPatternAutoWindows.innerHTML = "";
    return;
  }

  const { windows, signals, lead, trend } = result;
  const adaptive = state.analysis?.adaptiveRecentPattern;
  els.recentPatternAutoBadge.textContent = `近 ${windows.map((item) => item.requestedSize).join("／")} 期`;
  els.recentPatternAutoSummary.innerHTML = `
    <div class="recent-pattern-auto-lead">
      <span>目前主導版路</span>
      <strong>${lead.title}</strong>
      <em>${lead.description}</em>
    </div>
    <div class="recent-pattern-auto-stat">
      <span>近期重疊</span>
      <strong>${windows[0].repeatAverage.toFixed(2)} 顆</strong>
      <em>近 ${windows[0].requestedSize} 期平均</em>
    </div>
    <div class="recent-pattern-auto-stat">
      <span>連號期比例</span>
      <strong>${windows[0].consecutiveRate}%</strong>
      <em>近 ${windows[0].requestedSize} 期</em>
    </div>
    <div class="recent-pattern-auto-stat">
      <span>集中區間</span>
      <strong>${windows[0].zone}</strong>
      <em>號碼占比 ${Math.round(windows[0].zoneShare * 100)}%</em>
    </div>
  `;
  els.recentPatternAutoSignals.innerHTML = signals
    .slice(0, 5)
    .map((signal, index) => `
      <div class="recent-pattern-auto-signal${index === 0 ? " is-lead" : ""}">
        <strong>${signal.title}</strong>
        <span>${signal.value}</span>
      </div>
    `)
    .join("");
  els.recentPatternAutoWindows.innerHTML = windows
    .map((item) => `
      <div class="recent-pattern-auto-window">
        <strong>近 ${item.size}${item.size === item.requestedSize ? "" : `／${item.requestedSize}`} 期</strong>
        <span>連莊 ${item.repeatAverage.toFixed(2)} 顆／期</span>
        <span>連號 ${item.consecutiveRate}%</span>
        <span>熱門尾數 ${item.topTails.map(([tail]) => `${tail}尾`).join("、") || "-"}</span>
        <span>奇偶 ${item.oddMode}奇${5 - item.oddMode}偶</span>
        <span>均和 ${item.sumAverage}</span>
      </div>
    `)
    .join("");
  const adaptiveText = adaptive?.selectedLabel ? `核心權重目前偏向「${adaptive.selectedLabel}」。` : "核心權重會隨新資料重新校準。";
  els.recentPatternAutoNote.textContent = `${trend}${adaptiveText} 新一期資料進來後自動重算；版路是統計觀察，不代表預測或保證中獎。`;
}

function renderPatterns(patterns, profiles = [], researchEvidence = null) {
  if (!patterns) {
    els.patternModel.textContent = "-";
    els.patternRepeat.textContent = "-";
    els.patternGrid.innerHTML = `<div class="empty-state">資料累積後會顯示版路分析。</div>`;
    els.patternLines.innerHTML = "";
    return;
  }
  els.patternModel.textContent = "核心版路摘要";
  els.patternRepeat.textContent = `重複均值 ${patterns.repeatAverage}`;
  const zone = patterns.zonePatterns?.[0];
  const odd = patterns.oddPatterns?.[0];
  const low = patterns.lowPatterns?.[0];
  const sumRange = patterns.sumRange || {};
  els.patternGrid.innerHTML = `
    <div>
      <span>常見分布</span>
      <strong>${zone ? zone.pattern : "-"}</strong>
      <em>${zone ? `${zone.count} 次` : ""}</em>
    </div>
    <div>
      <span>奇偶版路</span>
      <strong>${odd ? `${odd.odd} 奇 ${odd.even} 偶` : "-"}</strong>
      <em>${odd ? `${odd.count} 次` : ""}</em>
    </div>
    <div>
      <span>大小版路</span>
      <strong>${low ? `${low.low} 小 ${low.high} 大` : "-"}</strong>
      <em>${low ? `${low.count} 次` : ""}</em>
    </div>
    <div>
      <span>總和帶</span>
      <strong>${sumRange.min || "-"}-${sumRange.max || "-"}</strong>
      <em>中心 ${sumRange.center || "-"}</em>
    </div>
  `;
  const tails = (patterns.tails || []).slice(0, 4).map((item) => `${item.tail}尾`).join("、") || "資料不足";
  const intervals = (patterns.intervals || []).slice(0, 3).map((item) => `${item.label} ${item.rate}%`).join("、") || "資料不足";
  const neighbors = (patterns.neighborNumbers || []).slice(0, 6).map(pad).join("、") || "資料不足";
  const adaptive = patterns.adaptiveRecent || {};
  const adaptiveWeights = Array.isArray(adaptive.components) && adaptive.components.length
    ? adaptive.components.map((item) => `${item.label} ${item.weight}%`).join("・")
    : coreWeightSummary();
  const adaptiveTitle = adaptive.selectedLabel ? `動態版路：${adaptive.selectedLabel}` : "動態版路：綜合平衡";
  const adaptiveReason = adaptive.reason || "依近期逐期回測平滑校準，避免追逐單一期的波動。";
  els.patternLines.innerHTML = `
    <div class="pattern-soft">
      <span>近期尾數</span>
      <strong class="pattern-line-main">${tails}</strong>
      <em class="pattern-note">尾數作輔助觀察，不單獨決定推薦。</em>
    </div>
    <div class="pattern-soft">
      <span>集中區間</span>
      <strong class="pattern-line-main">${intervals}</strong>
      <em class="pattern-note">區間會作為核心版路的動態參考，避免組合過度集中。</em>
    </div>
    <div>
      <span>上期鄰近觀察</span>
      <strong class="pattern-line-main">${neighbors}</strong>
      <em class="pattern-note">僅作版路提示，不單獨把號碼推上推薦。</em>
    </div>
    <div class="pattern-wide pattern-soft">
      <span>近期自動版路</span>
      <strong class="pattern-line-main">${adaptiveTitle}</strong>
      <strong class="pattern-line-main">${adaptiveWeights}</strong>
      <em class="pattern-note">${adaptiveReason} 新一期資料進來後才重新校準，同一期不反覆改寫。</em>
    </div>
  `;
}

function renderTailAnalysis(tailAnalysis) {
  if (!els.tailAnalysisSummary || !els.tailHotList || !els.tailAvoidList) return;
  if (!isFlagshipPlan()) {
    els.tailAnalysisSummary.innerHTML = "";
    els.tailHotList.innerHTML = "";
    els.tailAvoidList.innerHTML = "";
    if (els.tailRecommendationBalls) els.tailRecommendationBalls.innerHTML = "";
    if (els.tailAnalysisMeta) els.tailAnalysisMeta.innerHTML = "";
    if (els.tailAnalysisNote) els.tailAnalysisNote.textContent = "旗艦會員專屬。";
    return;
  }
  if (!tailAnalysis) {
    els.tailAnalysisSummary.innerHTML = `<div class="empty-state">資料累積後會顯示尾數分析。</div>`;
    els.tailHotList.innerHTML = "";
    els.tailAvoidList.innerHTML = "";
    if (els.tailRecommendationBalls) els.tailRecommendationBalls.innerHTML = "";
    if (els.tailAnalysisMeta) els.tailAnalysisMeta.innerHTML = "";
    if (els.tailAnalysisNote) els.tailAnalysisNote.textContent = "";
    return;
  }

  const rows = Array.isArray(tailAnalysis.rows) ? tailAnalysis.rows : [];
  const rowByTail = new Map(rows.map((row) => [Number(row.tail), row]));
  const recommendedTails = Array.isArray(tailAnalysis.recommendedTails) ? tailAnalysis.recommendedTails : [];
  const avoidTails = Array.isArray(tailAnalysis.avoidTails) ? tailAnalysis.avoidTails : [];
  const windows = Array.isArray(tailAnalysis.windows) ? tailAnalysis.windows : [10, 20, 36];
  const chip = (tail, tone = "") => {
    const row = rowByTail.get(Number(tail));
    const score = row ? ` ${row.score}` : "";
    return `<span class="tail-pill ${tone}"><strong>${tail}尾</strong><small>${score} 分</small></span>`;
  };
  const strongest = rows[0];
  const freshest = [...rows].sort((left, right) => (right.momentum || 0) - (left.momentum || 0) || left.tail - right.tail)[0];
  const activeCount = rows.filter((row) => row.status !== "避開").length;
  els.tailAnalysisBadge.textContent = `${tailAnalysis.version || "獨立"} · 近 ${windows.join("／")} 期`;
  els.tailAnalysisSummary.innerHTML = `
    <div>
      <span>近期最強</span>
      <strong>${strongest ? `${strongest.tail}尾` : "-"}</strong>
      <em>${strongest ? `${strongest.score} 分` : "資料不足"}</em>
    </div>
    <div>
      <span>動能上升</span>
      <strong>${freshest ? `${freshest.tail}尾` : "-"}</strong>
      <em>${freshest ? `${freshest.momentum >= 0 ? "+" : ""}${freshest.momentum}%` : "資料不足"}</em>
    </div>
    <div>
      <span>可用尾數</span>
      <strong>${activeCount} / 10</strong>
      <em>依 4 期未出規則篩選</em>
    </div>
  `;
  els.tailHotList.innerHTML = recommendedTails.length
    ? recommendedTails.map((tail, index) => chip(tail, index < 2 ? "hot" : "")).join("")
    : `<span class="tail-analysis-empty">資料不足</span>`;
  els.tailAvoidList.innerHTML = avoidTails.length
    ? avoidTails.map((tail) => chip(tail, "avoid")).join("")
    : `<span class="tail-analysis-empty">目前沒有連續 4 期未出的尾數</span>`;
  if (els.tailRecommendationBalls) {
    const recommendation = normalizedPick(tailAnalysis.recommendation);
    els.tailRecommendationBalls.innerHTML = recommendation.length === 5 ? balls(recommendation) : `<span class="tail-analysis-empty">資料累積中</span>`;
  }
  if (els.tailAnalysisMeta) {
    els.tailAnalysisMeta.innerHTML = `
      <span>優先尾數：${recommendedTails.map((tail) => `${tail}尾`).join("、") || "資料不足"}</span>
      <span>避開：${avoidTails.map((tail) => `${tail}尾`).join("、") || "無"}</span>
      <span>固定短期分析</span>
    `;
  }
  if (els.tailAnalysisNote) {
    els.tailAnalysisNote.textContent = `${tailAnalysis.method || "獨立尾數統計分析。"} ${tailAnalysis.note || ""}`.trim();
  }
}

function renderSavedPicks() {
  const picks = loadSavedPicks().filter((pick) => pick.game === state.game);
  ensureDailyComparisonReset();
  const comparisonReady = dailyComparisonReady(state.latest);
  const latestNumbers = comparisonReady ? state.latest?.numbers || [] : [];
  if (!picks.length) {
    els.savedList.innerHTML = `<div class="empty-state">還沒有儲存號碼。</div>`;
    return;
  }

  els.savedList.innerHTML = picks
    .map((pick) => {
      const hits = matchCount(pick.numbers, latestNumbers);
      const savedAt = new Date(pick.createdAt).toLocaleDateString("zh-TW");
      const backtest = backtestPick(pick.numbers);
      const score = scorePick(pick.numbers, backtest);
      return `
        <div class="saved-item">
          <div>
            <div class="saved-balls">${miniBalls(pick.numbers, latestNumbers)}</div>
            <p class="saved-meta">${gameLabel(pick.game)} · ${savedAt} · ${pick.numbers.length} 顆${comparisonReady ? "" : " · 等待今日開獎"}</p>
            <div class="score-card">
              <div class="score-main">
                <strong>${score.total}</strong>
                <span>${score.label}</span>
              </div>
              <div class="score-details">${scoreDetails(score)}</div>
            </div>
            <div class="backtest-summary">
              <span>最高 ${backtest.bestHit} 中</span>
              <span>3 中以上 ${backtest.profitableCount} 次</span>
              <span>${backtest.recentGoodDraw ? `最近 ${backtest.recentGoodDraw.date}：${backtest.recentGoodDraw.hits} 中` : "近期未達 3 中"}</span>
            </div>
            <div class="backtest-bars">${backtestBars(backtest.distribution, backtest.testedCount)}</div>
          </div>
          <div class="saved-result">
            <strong>${comparisonReady ? hits : "-"}</strong>
            <span>${comparisonReady ? "中" : "待開獎"}</span>
            <button class="delete-button" data-delete-pick="${pick.id}" aria-label="刪除">×</button>
          </div>
        </div>
      `;
    })
    .join("");

  els.savedList.querySelectorAll("[data-delete-pick]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = loadSavedPicks().filter((pick) => pick.id !== button.dataset.deletePick);
      saveSavedPicks(next);
      syncSavedPicksToServer().catch(() => {});
      renderSavedPicks();
      setStatus("已刪除儲存號碼。");
    });
  });
}

function otherGame(game = state.game) {
  return game === "tw539" ? "ca-fantasy5" : "tw539";
}

function renderLatestOverview() {
  const current = state.latestByGame[state.game] || state.latest;
  const secondaryGame = otherGame(state.game);
  const secondary = state.latestByGame[secondaryGame];
  const primaryCard = els.gameName?.closest(".latest-draw-card");
  const secondaryCard = els.secondaryGameName?.closest(".latest-draw-card");

  if (primaryCard) primaryCard.dataset.game = state.game;
  if (secondaryCard) secondaryCard.dataset.game = secondaryGame;

  if (current) {
    els.gameName.textContent = current.name || gameLabel(state.game);
    els.period.textContent = `期別 ${current.period || "-"}`;
    els.date.textContent = `日期 ${current.date || "-"}`;
    els.latestBalls.innerHTML = balls(current.numbers || []);
  } else {
    els.gameName.textContent = gameLabel(state.game);
    els.period.textContent = "期別 -";
    els.date.textContent = "日期 -";
    els.latestBalls.innerHTML = '<span class="latest-placeholder">同步中...</span>';
  }

  if (!els.secondaryGameName) return;
  els.secondaryGameName.textContent = gameLabel(secondaryGame);
  if (secondary) {
    els.secondaryPeriod.textContent = `期別 ${secondary.period || "-"}`;
    els.secondaryDate.textContent = `日期 ${secondary.date || "-"}`;
    els.secondaryLatestBalls.innerHTML = balls(secondary.numbers || []);
    els.secondaryLatestStatus.textContent = "已同步最新資料";
  } else {
    els.secondaryPeriod.textContent = "期別 -";
    els.secondaryDate.textContent = "日期 -";
    els.secondaryLatestBalls.innerHTML = '<span class="latest-placeholder">同步中...</span>';
    els.secondaryLatestStatus.textContent = "正在同步另一彩種";
  }
}

function render(payload) {
  const { latest, history, analysis, updatedAt } = payload;
  state.latest = latest;
  state.latestByGame[state.game] = latest;
  markDailyComparison(latest);
  state.analysis = analysis;
  state.history = history;
  state.displayHistory = history;
  const scopeText = `目前使用近 ${analysis.drawCount} 期分析；短期可切換近 10、20、36 期。`;
  els.historyScope.textContent = scopeText;
  if (els.recentScope) els.recentScope.textContent = scopeText;
  els.dashboard.hidden = false;
  renderLatestOverview();
  els.note.textContent = analysis.note;
  renderModelBacktest(analysis.backtest, analysis.modelProfiles);
  renderPatterns(analysis.patterns, analysis.modelProfiles, analysis.researchEvidence);
  renderRecentPatternAuto();
  renderTailAnalysis(analysis.tailAnalysis);
  els.hot.innerHTML = rankRows(analysis.hot, "count");
  els.cold.innerHTML = rankRows(analysis.cold, "count");
  els.overdue.innerHTML = rankRows(analysis.overdue, "gap");
  renderHistory();
  els.drawCount.textContent = `${analysis.drawCount} 期`;
  renderFlagshipPick();
  renderFlagshipHistory();
  if ((state.activeTab === "model" || state.activeTab === "sniper") && isFlagshipPlan()) {
    loadFlagshipHistory({ silent: true });
  }
  renderModelOutput({ heavy: state.activeTab === "model" });
  setStatus(`已更新：${updatedAt.replace("T", " ")}`);
  refreshOtherLatest({ silent: true });
}

function renderLatestCard(latest) {
  if (!latest) return;
  state.latest = latest;
  state.latestByGame[state.game] = latest;
  els.dashboard.hidden = false;
  renderLatestOverview();
  refreshOtherLatest({ silent: true });
}

function renderPlans(subscription) {
  if (!subscription?.plans?.length) return;
  state.subscription = subscription;
  const plans = subscription.plans.filter((plan) => plan.id === "pro" || plan.id === "flagship");
  els.plans.innerHTML = plans
    .map((plan) => {
      const active = state.plan === plan.id;
      const paymentLink = plan.paymentLink || (plan.id === "pro" ? subscription.paymentLink : subscription.flagshipPaymentLink);
      const action = active ? "目前使用" : subscription.enabled && paymentLink ? `訂閱 ${plan.id === "flagship" ? "旗艦版" : "Pro"}` : `預覽 ${plan.id === "flagship" ? "旗艦版" : "Pro"}`;
      return `
        <div class="plan ${plan.id} ${active ? "active" : ""}">
          <div class="plan-title">
            <h3>${plan.name}</h3>
            <span>${active ? "使用中" : "升級"}</span>
          </div>
          <div class="price">${plan.price}</div>
          <ul class="features">
            ${plan.features.map((feature) => `<li>${feature}</li>`).join("")}
          </ul>
          <p class="plan-disclaimer">本訂閱附加功能只輔助提高中獎機率，並非百發百中；所有選號仍以統計參考為主。請理性投注，賽事每天有，祝您中獎。</p>
          <button class="plan-action" data-plan="${plan.id}" ${active ? "disabled" : ""}>${action}</button>
        </div>
      `;
    })
    .join("");

  els.plans.querySelectorAll("[data-plan]").forEach((button) => {
    button.addEventListener("click", () => {
      const selectedPlan = plans.find((plan) => plan.id === button.dataset.plan);
      const paymentLink = selectedPlan?.paymentLink || (button.dataset.plan === "pro" ? subscription.paymentLink : subscription.flagshipPaymentLink);
      if (subscription.enabled && paymentLink) {
        window.open(paymentLink, "_blank", "noopener,noreferrer");
        return;
      }
      state.plan = button.dataset.plan;
      savePlanPreview();
      renderPlans(subscription);
      applyPlanAccess();
      setStatus(state.plan === "flagship" ? "已切到量化旗艦版預覽：旗艦摘星五碼與自適應集成五碼已解鎖。" : "已切到 Pro 預覽：進階回測、版路、跨年查詢、通知與高分組合已解鎖。");
    });
  });
  applyPlanAccess();
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((char) => char.charCodeAt(0)));
}

function notificationSupported() {
  return "Notification" in window && "serviceWorker" in navigator;
}

function pushSupported() {
  return notificationSupported() && "PushManager" in window;
}

function updateNotificationUi() {
  if (!els.notifyToggle) return;
  if (!notificationSupported()) {
    els.notifyBadge.textContent = "不支援";
    els.notifyText.textContent = "這個瀏覽器目前不支援網站通知。iPhone 請先用 Safari 加入主畫面後再試。";
    els.notifyToggle.disabled = true;
    els.notifyTest.disabled = true;
    return;
  }

  const permission = Notification.permission;
  const hasPushKey = Boolean(state.notifications.publicKey);
  const serverReady = Boolean(state.notifications.serverReady);
  const isSubscribed = Boolean(state.pushSubscription);
  els.notifyBadge.textContent = isSubscribed ? "已訂閱" : permission === "denied" ? "已封鎖" : "可開啟";
  els.notifyToggle.textContent = isSubscribed ? "取消通知" : hasPushKey ? "開啟通知" : "開啟本機提醒";
  els.notifyToggle.disabled = permission === "denied";
  els.notifyTest.disabled = permission === "denied";
  els.notifyTest.hidden = permission === "denied" || !notificationSupported();

  if (permission === "denied") {
    els.notifyText.textContent = "瀏覽器目前封鎖通知。請到瀏覽器網站設定允許通知後，再回來開啟開獎提醒。";
  } else if (isSubscribed && serverReady) {
    const interval = state.notifications.autoNotifyIntervalSeconds || 30;
    els.notifyText.textContent = `已登錄開獎通知。系統約每 ${interval} 秒檢查新一期並發送提醒；目前約 ${state.notifications.subscriberCount || 1} 個裝置訂閱。`;
  } else if (permission === "granted" && !serverReady) {
    els.notifyText.textContent = "已開啟本機提醒；網站開著時偵測到新一期會跳通知。離線群發需設定 Render 推播金鑰與排程。";
  } else if (isSubscribed) {
    els.notifyText.textContent = "已訂閱通知；離線群發還需要 Render 推播金鑰與定時觸發流程。";
  } else if (!hasPushKey) {
    els.notifyText.textContent = "可先開啟本機提醒；正式離線群發需要在 Render 設定 VAPID 推播金鑰。";
  } else {
    els.notifyText.textContent = "開啟後，有新一期開獎時可收到通知。手機建議先加入主畫面。";
  }
}

async function getServiceWorkerRegistration() {
  if (!notificationSupported()) return null;
  if (state.serviceWorkerRegistration) return state.serviceWorkerRegistration;
  state.serviceWorkerRegistration = await navigator.serviceWorker.register("/sw.js");
  return state.serviceWorkerRegistration;
}

async function syncPushSubscription() {
  if (!pushSupported()) {
    updateNotificationUi();
    return;
  }
  const registration = await getServiceWorkerRegistration();
  state.pushSubscription = await registration.pushManager.getSubscription();
  updateNotificationUi();
  await syncSavedPicksToServer().catch(() => {});
}

async function postSubscription(action, subscription) {
  const payload = await fetchJsonWithTimeout("/api/push-subscription", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, subscription, game: state.game, savedPicks: notificationSavedPicks() }),
    timeoutMs: 15000,
  });
  if (!payload.ok) throw new Error(payload.error || "通知訂閱失敗");
  state.notifications.subscriberCount = payload.subscriberCount || state.notifications.subscriberCount || 0;
  return payload;
}

function notificationSavedPicks() {
  return loadSavedPicks()
    .slice(0, 20)
    .map((pick) => ({ game: pick.game, numbers: pick.numbers }));
}

async function syncSavedPicksToServer() {
  if (!state.pushSubscription) return;
  await postSubscription("sync-picks", state.pushSubscription);
}

async function enableNotifications() {
  if (!notificationSupported()) {
    setStatus("這個瀏覽器目前不支援通知。", true);
    return;
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    updateNotificationUi();
    setStatus("通知權限尚未開啟。", true);
    return;
  }
  const registration = await getServiceWorkerRegistration();
  if (!pushSupported() || !state.notifications.publicKey) {
    updateNotificationUi();
    await showLocalTestNotification("開獎通知已允許", "正式群發待設定推播金鑰；目前可在開站時接收本機提醒。");
    return;
  }
  state.pushSubscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(state.notifications.publicKey),
  });
  await postSubscription("subscribe", state.pushSubscription);
  updateNotificationUi();
  setStatus("已開啟開獎通知。");
}

async function disableNotifications() {
  if (!state.pushSubscription) {
    updateNotificationUi();
    return;
  }
  const oldSubscription = state.pushSubscription;
  await oldSubscription.unsubscribe();
  state.pushSubscription = null;
  await postSubscription("unsubscribe", oldSubscription);
  updateNotificationUi();
  setStatus("已取消開獎通知。");
}

async function toggleNotifications() {
  try {
    if (state.pushSubscription) {
      await disableNotifications();
    } else {
      await enableNotifications();
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function showLocalTestNotification(title = "摘星狙擊手開獎通知", body = "這是一則測試通知。", options = {}) {
  if (!notificationSupported()) {
    setStatus("這個瀏覽器目前不支援通知。", true);
    return;
  }
  if (Notification.permission !== "granted") {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      updateNotificationUi();
      setStatus("通知權限尚未開啟。", true);
      return;
    }
  }
  const registration = await getServiceWorkerRegistration();
  await registration.showNotification(title, {
    body,
    icon: "/logo-sniper-star-192.png?v=40",
    badge: "/logo-sniper-star-192.png?v=40",
    data: { url: `/?game=${state.game}` },
  });
  updateNotificationUi();
  if (!options.silent) setStatus("已送出測試通知。");
}

async function notifyIfLatestChanged(latest, previousKey) {
  const nextKey = drawKey(latest);
  if (!latest || !nextKey || !previousKey || previousKey === nextKey) return;
  if (Notification.permission !== "granted") return;
  const watchedPicks = loadSavedPicks().filter((pick) => pick.game === state.game).slice(0, 20);
  const outcomes = watchedPicks
    .map((pick) => {
      const hitNumbers = pick.numbers.filter((number) => (latest.numbers || []).includes(number));
      return hitNumbers.length ? starHitMessage(hitNumbers) : "";
    })
    .filter(Boolean);
  const title = outcomes.length ? `${latest.name || "摘星狙擊手"} 命中通知` : `${latest.name || "摘星狙擊手"} 已更新`;
  let body = outcomes.length
    ? outcomes.slice(0, 3).join(" ｜ ")
    : `第 ${latest.period || "-"} 期：${(latest.numbers || []).map(pad).join("、")}`;
  if (!outcomes.length && watchedPicks.length) {
    body += `；你儲存的 ${watchedPicks.length} 組號碼本期未命中。`;
  } else if (outcomes.length > 3) {
    body += `；另有 ${outcomes.length - 3} 組號碼命中。`;
  }
  await showLocalTestNotification(title, body, { silent: true });
}

function starHitMessage(hitNumbers) {
  const numbers = hitNumbers.map(pad).join("、");
  switch (hitNumbers.length) {
    case 1:
      return `恭喜（${numbers}）摘下一星`;
    case 2:
      return `恭喜（${numbers}）摘下二星`;
    case 3:
      return `恭喜（${numbers}）太神了！摘下三星`;
    case 4:
      return `恭喜（${numbers}）你超神了！摘下四星`;
    case 5:
      return `恭喜（${numbers}）你已成為最強狙擊手！五顆通通拿下`;
    default:
      return `本期命中 ${hitNumbers.length} 顆：${numbers}`;
  }
}

async function initNotifications(config) {
  state.notifications = {
    supported: Boolean(config?.supported),
    serverReady: Boolean(config?.serverReady),
    publicKey: config?.publicKey || "",
    subscriberCount: config?.subscriberCount || 0,
    autoNotifyIntervalSeconds: Number(config?.autoNotifyIntervalSeconds) || 30,
  };
  updateNotificationUi();
  if (notificationSupported()) {
    await syncPushSubscription().catch(() => updateNotificationUi());
  }
}

async function loadConfig() {
  try {
    const payload = await fetchJsonWithTimeout("/api/config");
    if (payload.ok) {
      state.plan = loadPlanPreview();
      renderPlans(payload.subscription);
    }
    if (payload.ok) await initNotifications(payload.notifications);
  } catch (error) {
    setStatus("訂閱設定讀取失敗，但開獎資料仍可使用。", true);
  }
}

async function load(options = {}) {
  const silent = Boolean(options.silent);
  const dailyReset = ensureDailyComparisonReset();
  if (dailyReset && state.latest) renderSavedPicks();
  if (!isProPlan() && state.limit > 90) {
    state.limit = 90;
    els.limit.value = "90";
  }
  const cacheKey = `${state.game}-${state.limit}-backtest-${state.backtestLimit}-flagship-${state.flagshipLimit}`;
  const cachedPayload = options.skipCache ? null : readCachedPayload(cacheKey);
  const requestId = ++state.requestId;
  if (cachedPayload) {
    render(cachedPayload);
    if (!silent) setStatus("已先顯示暫存資料，正在背景確認最新開獎...");
  } else {
    if (!silent) setStatus("數據分析中...");
    if (!state.latest) {
      fetchJsonWithTimeout(`/api/latest?game=${state.game}&t=${Date.now()}`, { timeoutMs: LATEST_FETCH_TIMEOUT_MS })
        .then((latestPayload) => {
          if (latestPayload.ok && requestId === state.requestId && !state.latest) {
            renderLatestCard(latestPayload.latest);
            setStatus("最新開獎已顯示，正在載入完整分析...");
          }
        })
        .catch(() => {});
    }
  }
  if (!silent) els.refresh.disabled = true;
  try {
    const previousSeen = readLastSeenDraw()[state.game] || "";
    const payload = await fetchJsonWithTimeout(
      `/api/lottery?game=${state.game}&limit=${state.limit}&backtestLimit=${state.backtestLimit}&flagshipLimit=${state.flagshipLimit}&t=${Date.now()}`,
    );
    if (!payload.ok) throw new Error(payload.error || "資料讀取失敗");
    if (requestId !== state.requestId) return;
    writeCachedPayload(cacheKey, payload);
    render(payload);
    await notifyIfLatestChanged(payload.latest, previousSeen).catch(() => {});
    writeLastSeenDraw(state.game, payload.latest);
  } catch (error) {
    if (cachedPayload) {
      if (!silent) setStatus("目前使用暫存資料；背景更新暫時失敗。", true);
      return;
    }
    if (!state.latest) els.dashboard.hidden = true;
    if (!silent) setStatus(error.name === "AbortError" ? "讀取逾時，請稍後再試。" : error.message, true);
  } finally {
    if (requestId === state.requestId) {
      els.refresh.disabled = false;
    }
  }
}

async function refreshLatest(options = {}) {
  if (state.latestRefreshInFlight) return;
  state.latestRefreshInFlight = true;
  const dailyReset = ensureDailyComparisonReset();
  if (dailyReset && state.latest) renderSavedPicks();
  const requestId = ++state.latestRequestId;
  try {
    const payload = await fetchJsonWithTimeout(
      `/api/latest?game=${state.game}&t=${Date.now()}`,
      { timeoutMs: LATEST_FETCH_TIMEOUT_MS },
    );
    if (!payload.ok || requestId !== state.latestRequestId) return;
    const nextLatest = payload.latest;
    const previousKey = readLastSeenDraw()[state.game] || "";
    const changed = drawKey(nextLatest) !== drawKey(state.latest);
    if (!changed) {
      refreshOtherLatest({ silent: true });
      return;
    }

    renderLatestCard(nextLatest);
    await notifyIfLatestChanged(nextLatest, previousKey).catch(() => {});
    writeLastSeenDraw(state.game, nextLatest);
    setStatus("最新開獎號碼已更新，正在同步邏輯推理...");
    await load({ silent: true, skipCache: true });
  } catch (error) {
    if (!options.silent) {
      setStatus(error.name === "AbortError" ? "最新開獎讀取逾時，請稍後再試。" : "最新開獎暫時無法讀取。", true);
    }
  } finally {
    state.latestRefreshInFlight = false;
  }
}

async function refreshOtherLatest(options = {}) {
  const requestedGame = otherGame(state.game);
  try {
    const payload = await fetchJsonWithTimeout(
      `/api/latest?game=${requestedGame}&t=${Date.now()}`,
      { timeoutMs: LATEST_FETCH_TIMEOUT_MS },
    );
    if (!payload.ok || !payload.latest) return;
    state.latestByGame[requestedGame] = payload.latest;
    renderLatestOverview();
  } catch (error) {
    if (!options.silent) {
      setStatus(error.name === "AbortError" ? "另一彩種最新資料讀取逾時。" : "另一彩種最新資料暫時無法讀取。", true);
    }
  }
}

function startAutoRefresh() {
  if (state.autoRefreshTimer) window.clearInterval(state.autoRefreshTimer);
  state.autoRefreshTimer = window.setInterval(() => {
    if (document.visibilityState === "visible") {
      refreshLatest({ silent: true });
    }
  }, POLL_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshLatest({ silent: true });
    }
  });
  window.addEventListener("online", () => refreshLatest({ silent: true }));
}

async function runCrossYearSearch() {
  if (!requirePro("跨年歷史查詢")) return;
  const fromYear = Number(els.historyFromYear.value);
  const toYear = Number(els.historyToYear.value);
  if (!Number.isInteger(fromYear) || !Number.isInteger(toYear) || fromYear < 2007 || toYear < 2007) {
    setStatus("請輸入有效年份，例如 2024 到今年。", true);
    return;
  }
  const number = els.historyNumber.value;
  if (number && (Number(number) < 1 || Number(number) > 39)) {
    setStatus("指定號碼請輸入 1 到 39。", true);
    return;
  }
  setStatus("正在查詢跨年度歷史紀錄...");
  els.crossYearSearch.disabled = true;
  try {
    const params = new URLSearchParams({
      game: state.game,
      fromYear: String(fromYear),
      toYear: String(toYear),
      keyword: els.historyKeyword.value.trim(),
      number,
      limit: "5000",
    });
    const payload = await fetchJsonWithTimeout(`/api/history-search?${params}`);
    if (!payload.ok) throw new Error(payload.error || "跨年查詢失敗");
    state.displayHistory = payload.history;
    state.historySearch.keyword = "";
    state.historySearch.number = "";
    els.historyKeyword.value = "";
    els.historyNumber.value = "";
    renderHistory();
    const years = payload.searchedYears?.length ? `${payload.searchedYears[0]}-${payload.searchedYears[payload.searchedYears.length - 1]}` : `${fromYear}-${toYear}`;
    const scopeText = `跨年查詢：${years}，共 ${payload.total} 筆${payload.limited ? "，目前顯示前 5000 筆" : ""}。`;
    els.historyScope.textContent = scopeText;
    if (els.recentScope) els.recentScope.textContent = scopeText;
    activateTab("recent");
    setStatus(`已完成跨年查詢：${payload.total} 筆。`);
  } catch (error) {
    setStatus(error.name === "AbortError" ? "查詢逾時，請縮小年份範圍或稍後再試。" : error.message, true);
  } finally {
    els.crossYearSearch.disabled = false;
  }
}

function initHistoryYears() {
  const currentYear = new Date().getFullYear();
  const fromYear = Math.max(2007, currentYear - 2);
  [els.historyFromYear, els.historyToYear].forEach((input) => {
    input.max = String(currentYear);
  });
  els.historyFromYear.value = String(fromYear);
  els.historyToYear.value = String(currentYear);
}

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".segment").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.game = button.dataset.game;
    state.latest = state.latestByGame[state.game] || null;
    renderLatestOverview();
    renderCountdown();
    load();
  });
});

organizeAnalysisPanels();

els.tabButtons.forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});

els.limit.addEventListener("change", () => {
  if (!isProPlan() && Number(els.limit.value) > 90) {
    els.limit.value = "90";
    state.limit = 90;
    setStatus("目前最多分析 90 期；Pro 可使用 120、180、365 期。", true);
    return;
  }
  state.limit = Number(els.limit.value);
  state.candidateCache.clear();
  state.backtestCache.clear();
  load();
});

els.backtestSelect.addEventListener("change", () => {
  if (els.backtestSelect.value === "custom") {
    els.backtestInput.focus();
    return;
  }
  state.backtestLimit = normalizeBacktestLimit(els.backtestSelect.value);
  saveBacktestLimit();
  state.candidateCache.clear();
  state.backtestCache.clear();
  syncBacktestControls();
  load();
});

els.backtestApply.addEventListener("click", () => {
  const value = Number(els.backtestInput.value);
  if (!Number.isInteger(value) || value < 7 || value > 365) {
    setStatus("回測期數請輸入 7 到 365 的整數。", true);
    els.backtestInput.focus();
    return;
  }
  state.backtestLimit = value;
  saveBacktestLimit();
  state.candidateCache.clear();
  state.backtestCache.clear();
  syncBacktestControls();
  load();
});

if (els.flagshipLimitApply) {
  els.flagshipLimitApply.addEventListener("click", () => {
    if (!requireFlagship("旗艦專屬期數分析")) return;
    state.flagshipLimit = normalizeFlagshipLimit(els.flagshipLimitSelect.value);
    saveFlagshipLimit();
    syncFlagshipControls();
    load();
  });
}

els.refresh.addEventListener("click", load);
els.crossYearSearch.addEventListener("click", runCrossYearSearch);

if (els.flagshipHistoryRefresh) {
  els.flagshipHistoryRefresh.addEventListener("click", () => {
    if (!requireFlagship("旗艦分析紀錄")) return;
    loadFlagshipHistory({ force: true });
  });
}

if (els.coreCandidateSave) {
  els.coreCandidateSave.addEventListener("click", () => {
    if (!requireFlagship("核心候選 15 碼")) return;
    if (state.coreCandidateSelection.length !== 5) {
      setStatus("請先從候選 15 碼選滿 5 顆。", true);
      return;
    }
    if (savePick(state.coreCandidateSelection)) {
      setStatus(`已儲存核心候選組合：${state.coreCandidateSelection.map(pad).join(" · ")}`);
    }
  });
}

els.savedForm.addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const numbers = parseSavedInputs();
    if (savePick(numbers)) fillSavedInputs([]);
  } catch (error) {
    setStatus(error.message, true);
  }
});

if (els.savedPicker) {
  els.savedPicker.addEventListener("click", (event) => {
    const button = event.target.closest("[data-saved-number]");
    if (!button) return;
    const number = Number(button.dataset.savedNumber);
    state.savedSelection = state.savedSelection.includes(number)
      ? state.savedSelection.filter((item) => item !== number)
      : [...state.savedSelection, number].sort((left, right) => left - right);
    renderSavedNumberPicker();
  });
}

if (els.clearSavedNumbers) {
  els.clearSavedNumbers.addEventListener("click", () => fillSavedInputs([]));
}

els.usePick.addEventListener("click", () => {
  const numbers = currentReferenceNumbers();
  if (numbers.length !== 5) {
    setStatus("目前還沒有可套用的參考選號。", true);
    return;
  }
  fillSavedInputs(numbers);
  setStatus("已套用統計參考選號，確認後可儲存。");
});

els.generate.addEventListener("click", () => {
  if (!requirePro("高分組合")) return;
  state.candidateCache.clear();
  scheduleModelRender("已重新產生高分候選組合。");
});

els.focusButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (!requirePro("邏輯模式切換")) return;
    const preset = FOCUS_PRESETS[button.dataset.focus];
    if (!preset) return;
    state.analysisFocus = button.dataset.focus;
    state.modelWeights = { ...preset.weights };
    saveAnalysisFocus();
    saveModelWeights();
    renderModelControls();
    scheduleModelRender(`分析重點已切換：${preset.label}。`);
  });
});

els.modelInputs.forEach((input) => {
  input.addEventListener("input", () => {
    if (!requirePro("邏輯權重調整")) return;
    state.modelWeights[input.dataset.weight] = Number(input.value);
    saveModelWeights();
    renderModelControls();
    scheduleModelRender("邏輯推理設定已更新。");
  });
});

els.resetModel.addEventListener("click", () => {
  if (!requirePro("邏輯推理設定重設")) return;
  state.analysisFocus = "balanced";
  state.modelWeights = { ...FOCUS_PRESETS.balanced.weights };
  saveAnalysisFocus();
  saveModelWeights();
  renderModelControls();
  scheduleModelRender("邏輯推理設定已重設。");
});

els.historyKeyword.addEventListener("input", () => {
  state.historySearch.keyword = els.historyKeyword.value;
  renderHistory();
});

els.historyNumber.addEventListener("input", () => {
  const value = els.historyNumber.value;
  if (value && (Number(value) < 1 || Number(value) > 39)) {
    setStatus("指定號碼請輸入 1 到 39。", true);
    return;
  }
  state.historySearch.number = value;
  renderHistory();
});

els.clearHistorySearch.addEventListener("click", () => {
  state.historySearch = { keyword: "", number: "" };
  els.historyKeyword.value = "";
  els.historyNumber.value = "";
  state.displayHistory = state.history;
  const scopeText = "目前顯示本次載入的分析期數。";
  els.historyScope.textContent = scopeText;
  if (els.recentScope) els.recentScope.textContent = scopeText;
  renderHistory();
  setStatus("已清除歷史查詢條件。");
});

if (els.notifyToggle) {
  els.notifyToggle.addEventListener("click", toggleNotifications);
}

if (els.notifyTest) {
  els.notifyTest.addEventListener("click", () => {
    const latest = state.latest;
    const title = latest ? `${latest.name} 最新開獎通知` : "摘星狙擊手開獎通知";
    const body = latest ? `第 ${latest.period || "-"} 期：${latest.numbers.map(pad).join("、")}` : "這是一則測試通知。";
    showLocalTestNotification(title, body);
  });
}

window.addEventListener("load", () => {
  getServiceWorkerRegistration().catch(() => {});
});

state.plan = loadPlanPreview();
state.backtestLimit = loadBacktestLimit();
state.flagshipLimit = loadFlagshipLimit();
state.analysisFocus = loadAnalysisFocus();
state.modelWeights = loadModelWeights();
initHistoryYears();
syncBacktestControls();
syncFlagshipControls();
renderModelControls();
renderSavedNumberPicker();
applyPlanAccess();
updateNotificationUi();
startCountdown();
loadConfig();
load();
startAutoRefresh();
