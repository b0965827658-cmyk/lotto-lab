const state = {
  game: "tw539",
  limit: 90,
  analysisFocus: "balanced",
  latest: null,
  analysis: null,
  history: [],
  displayHistory: [],
  requestId: 0,
  apiCache: new Map(),
  candidateCache: new Map(),
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

const $ = (selector) => document.querySelector(selector);

const els = {
  dashboard: $("#dashboard"),
  status: $("#status"),
  refresh: $("#refreshBtn"),
  limit: $("#limitSelect"),
  gameName: $("#gameName"),
  sourceLink: $("#sourceLink"),
  period: $("#period"),
  date: $("#date"),
  latestBalls: $("#latestBalls"),
  pickBalls: $("#pickBalls"),
  pickMeta: $("#pickMeta"),
  note: $("#analysisNote"),
  hot: $("#hotList"),
  cold: $("#coldList"),
  overdue: $("#overdueList"),
  history: $("#historyRows"),
  drawCount: $("#drawCount"),
  plans: $("#planGrid"),
  savedForm: $("#savedForm"),
  savedInputs: Array.from(document.querySelectorAll(".number-input")),
  savedList: $("#savedList"),
  usePick: $("#usePickBtn"),
  generate: $("#generateBtn"),
  candidates: $("#candidateList"),
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
  backtestBadge: $("#backtestBadge"),
  avgHit: $("#avgHit"),
  threePlusRate: $("#threePlusRate"),
  bestHit: $("#bestHit"),
  backtestRecent: $("#backtestRecent"),
  backtestMethod: $("#backtestMethod"),
  patternModel: $("#patternModel"),
  patternRepeat: $("#patternRepeat"),
  patternGrid: $("#patternGrid"),
  patternLines: $("#patternLines"),
};

function pad(n) {
  return String(n).padStart(2, "0");
}

function balls(numbers) {
  return numbers.map((n) => `<span class="ball">${pad(n)}</span>`).join("");
}

function miniBalls(numbers, winners = []) {
  const winnerSet = new Set(winners);
  return numbers
    .map((n) => `<span class="mini-ball ${winnerSet.has(n) ? "hit" : ""}">${pad(n)}</span>`)
    .join("");
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

function historyRows(draws) {
  if (!draws.length) {
    return `
      <tr>
        <td colspan="3" class="empty-cell">查無符合條件的開獎紀錄</td>
      </tr>
    `;
  }
  return draws
    .map(
      (draw) => `
        <tr>
          <td>${draw.date || "-"}</td>
          <td>${draw.period || "-"}</td>
          <td class="number-text">${draw.numbers.map(pad).join(" · ")}</td>
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

function setStatus(message, isError = false) {
  els.status.textContent = message;
  els.status.classList.toggle("error", isError);
}

function loadSavedPicks() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveSavedPicks(picks) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(picks));
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

function renderModelControls() {
  els.focusButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.focus === state.analysisFocus);
  });
  els.modelInputs.forEach((input) => {
    const key = input.dataset.weight;
    input.value = state.modelWeights[key];
    const valueEl = document.querySelector(`#${key}Weight`);
    if (valueEl) valueEl.textContent = state.modelWeights[key];
  });
  const weights = normalizedWeights();
  const focus = FOCUS_PRESETS[state.analysisFocus] || FOCUS_PRESETS.balanced;
  els.modelSummary.textContent = `${focus.label}：${focus.description} 權重為熱度 ${Math.round(weights.heat * 100)}%、遺漏 ${Math.round(weights.overdue * 100)}%、分散 ${Math.round(weights.spread * 100)}%、回測 ${Math.round(weights.backtest * 100)}%。`;
}

function gameLabel(game) {
  return game === "ca-fantasy5" ? "加州天天樂" : "今彩 539";
}

function parseSavedInputs() {
  const numbers = els.savedInputs.map((input) => Number(input.value));
  if (numbers.some((n) => !Number.isInteger(n) || n < 1 || n > 39)) {
    throw new Error("請輸入 1 到 39 的五個號碼。");
  }
  const unique = new Set(numbers);
  if (unique.size !== 5) {
    throw new Error("五個號碼不能重複。");
  }
  return [...unique].sort((a, b) => a - b);
}

function fillSavedInputs(numbers) {
  els.savedInputs.forEach((input, index) => {
    input.value = numbers[index] ?? "";
  });
}

function matchCount(numbers, winners) {
  const winnerSet = new Set(winners || []);
  return numbers.filter((n) => winnerSet.has(n)).length;
}

function backtestPick(numbers) {
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

  return {
    distribution,
    bestHit,
    bestDraw,
    recentGoodDraw,
    testedCount: state.history.length,
    profitableCount: distribution[3] + distribution[4] + distribution[5],
  };
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
  const patternBonus = pairBonus + dragBonus + repeatBonus + intervalBonus;
  const weights = normalizedWeights();
  const total = clamp(
    Math.round(heat * weights.heat + overdue * weights.overdue + spread * weights.spread + backtestScore * weights.backtest + patternBonus),
    0,
    100,
  );
  const label = total >= 75 ? "高追蹤" : total >= 55 ? "可觀察" : "保守";
  return { total, heat, overdue, spread, backtest: backtestScore, pattern: Math.round(patternBonus), interval: Math.round(intervalBonus), label };
}

function scoreDetails(score) {
  return [
    ["熱度", score.heat],
    ["遺漏", score.overdue],
    ["分散", score.spread],
    ["回測", score.backtest],
    ["區間", score.interval || 0],
    ["版路", score.pattern || 0],
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
  return ["hot", "pattern", "interval"].includes(state.analysisFocus);
}

function patternHints() {
  const patterns = state.analysis?.patterns || {};
  const pairs = (patterns.pairCombos || []).map((item) => item.numbers || []).filter((pair) => pair.length === 2);
  const dragTargets = [...new Set((patterns.dragCards || []).map((item) => item.target).filter(Boolean))];
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
  return { pairs, dragTargets, intervalNumbers, intervals, repeatNumbers };
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
  const patternNumbers = [...hints.pairs.flat(), ...hints.dragTargets, ...hints.intervalNumbers, ...hints.repeatNumbers];
  const pool = [...new Set([...hot, ...overdue, ...balanced, ...patternNumbers, ...tailFilteredUniverse])].filter(numberAllowed);
  if (pool.length >= 12) return pool;
  return filterByTail ? tailFilteredUniverse : Array.from({ length: 39 }, (_, i) => i + 1);
}

function randomChoice(items) {
  return items[Math.floor(Math.random() * items.length)];
}

function buildCandidate(pool) {
  const numbers = new Set();
  const frequencyRows = state.analysis?.frequency || [];
  const stats = new Map(frequencyRows.map((row) => [row.number, row]));
  const poolSet = new Set(pool);
  const hints = patternHints();
  const pairChoices = hints.pairs.filter((pair) => pair.every((number) => poolSet.has(number)));
  const dragTargets = hints.dragTargets.filter((number) => poolSet.has(number));
  const intervalNumbers = hints.intervalNumbers.filter((number) => poolSet.has(number));
  const repeatNumbers = hints.repeatNumbers.filter((number) => poolSet.has(number));
  const classicList = [...frequencyRows]
    .map((row) => ({ n: row.number, score: row.count * 0.45 + row.gap * 0.27 + Math.random() * 5 }))
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
  const zones = [
    pool.filter((n) => n <= 10),
    pool.filter((n) => n >= 11 && n <= 20),
    pool.filter((n) => n >= 21 && n <= 30),
    pool.filter((n) => n >= 31),
  ].filter(Boolean);

  zones.forEach((zone) => {
    const chance = focus === "pattern" || focus === "interval" ? 0.88 : 0.72;
    if (numbers.size < 5 && zone.length && Math.random() < chance) {
      numbers.add(randomChoice(zone));
    }
  });
  if ((focus === "pattern" || focus === "interval" || Math.random() < 0.55) && pairChoices.length && numbers.size <= 3) {
    randomChoice(pairChoices).forEach((number) => numbers.add(number));
  }
  if (dragTargets.length && numbers.size < 5 && Math.random() < 0.72) {
    numbers.add(randomChoice(dragTargets));
  }
  if (intervalNumbers.length && numbers.size < 5 && Math.random() < 0.78) {
    numbers.add(randomChoice(intervalNumbers));
  }
  if ((focus === "pattern" || focus === "interval") && intervalNumbers.length) {
    while (numbers.size < 3) numbers.add(randomChoice(intervalNumbers));
  }
  if (repeatNumbers.length && numbers.size < 5 && Math.random() < 0.45) {
    numbers.add(randomChoice(repeatNumbers));
  }
  if (focus === "classic" && classicList.length) {
    while (numbers.size < 4) numbers.add(randomChoice(classicList));
  }
  if (focus === "hot" && hotList.length) {
    while (numbers.size < 3) numbers.add(randomChoice(hotList));
  }
  if (focus === "overdue" && overdueList.length) {
    while (numbers.size < 3) numbers.add(randomChoice(overdueList));
  }
  if (focus === "backtest") {
    const top = [...pool]
      .map((n) => {
        const row = stats.get(n) || { count: 0, gap: 0 };
        return { n, score: row.count * 0.35 + row.gap * 0.25 + Math.random() * 8 };
      })
      .sort((a, b) => b.score - a.score)
      .slice(0, 20)
      .map((item) => item.n);
    while (numbers.size < 4 && top.length) numbers.add(randomChoice(top));
  }
  while (numbers.size < 5) {
    numbers.add(randomChoice(pool));
  }
  return [...numbers].sort((a, b) => a - b);
}

function generateCandidates() {
  const cacheKey = `${state.game}-${state.limit}-${state.latest?.date || ""}-${state.latest?.period || ""}-${state.analysisFocus}-${JSON.stringify(state.modelWeights)}`;
  const cached = state.candidateCache.get(cacheKey);
  if (cached) return cached;
  const pool = candidatePool();
  const seen = new Set();
  const candidates = [];
  const attempts = state.analysisFocus === "backtest" ? 260 : 200;
  for (let i = 0; i < attempts; i += 1) {
    const numbers = buildCandidate(pool);
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
}

function referenceCandidate() {
  const candidates = generateCandidates();
  if (candidates.length) return candidates[0];
  const numbers = state.analysis?.recommendation || [];
  if (numbers.length !== 5) return null;
  const backtest = backtestPick(numbers);
  return { numbers, backtest, score: scorePick(numbers, backtest) };
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
  const focus = FOCUS_PRESETS[state.analysisFocus] || FOCUS_PRESETS.balanced;
  const tailProfile = hotTailProfile();
  els.pickBalls.innerHTML = balls(candidate.numbers);
  els.pickMeta.innerHTML = `
    <span>${focus.label}</span>
    <span>${focus.description}</span>
    <span>熱尾 ${tailProfile.label}</span>
    <span>分數 ${candidate.score.total}</span>
    <span>版路 +${candidate.score.pattern || 0}</span>
    <span>最高 ${candidate.backtest.bestHit} 中</span>
    <span>3 中以上 ${candidate.backtest.profitableCount} 次</span>
  `;
}

function savePick(numbers) {
  const normalized = [...numbers].sort((a, b) => a - b);
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
  renderSavedPicks();
  setStatus(`已儲存 ${gameLabel(state.game)}：${normalized.map(pad).join(" · ")}`);
  return true;
}

function renderCandidates() {
  if (!state.analysis || !state.history.length) {
    els.candidates.innerHTML = `<div class="empty-state">資料讀取後會產生候選組合。</div>`;
    return;
  }
  const candidates = generateCandidates();
  els.candidates.innerHTML = candidates
    .map(
      (candidate, index) => `
        <div class="candidate-item">
          <div class="candidate-rank">#${index + 1}</div>
          <div>
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

function renderModelBacktest(backtest, profiles = []) {
  if (!backtest || !backtest.testedCount) {
    els.backtestBadge.textContent = "資料不足";
    els.avgHit.textContent = "-";
    els.threePlusRate.textContent = "-";
    els.bestHit.textContent = "-";
    els.backtestRecent.innerHTML = `<div class="empty-state">累積更多期數後會顯示模型回測。</div>`;
    els.backtestMethod.textContent = "";
    return;
  }
  els.backtestBadge.textContent = `${backtest.testedCount} 期`;
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
  els.backtestMethod.innerHTML = `
    ${backtest.method}
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
  if (ranking || warning) {
    els.backtestRecent.insertAdjacentHTML("afterbegin", `${warning}<div class="model-rank-list">${ranking}</div>`);
  }
}

function renderPatterns(patterns, profiles = []) {
  if (!patterns) {
    els.patternModel.textContent = "-";
    els.patternRepeat.textContent = "-";
    els.patternGrid.innerHTML = `<div class="empty-state">資料累積後會顯示版路分析。</div>`;
    els.patternLines.innerHTML = "";
    return;
  }
  els.patternModel.textContent = patterns.selectedLabel || "版路模型";
  els.patternRepeat.textContent = `重複均值 ${patterns.repeatAverage}`;
  const zone = patterns.zonePatterns?.[0];
  const odd = patterns.oddPatterns?.[0];
  const low = patterns.lowPatterns?.[0];
  const tails = patterns.tails || [];
  const intervals = patterns.intervals || [];
  const sumRange = patterns.sumRange || {};
  const chip = (text, tone = "") => `<span class="pattern-chip ${tone}">${text}</span>`;
  const empty = `<span class="pattern-note">資料不足</span>`;
  els.patternGrid.innerHTML = `
    <div>
      <span>常見區間</span>
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
  const tailText = tails.length
    ? tails.slice(0, 5).map((item, index) => chip(`${item.tail}尾 ${item.count}次`, index < 2 ? "gold" : "")).join("")
    : empty;
  const intervalText = intervals.length
    ? intervals.slice(0, 4).map((item, index) => chip(`${item.label} ${item.rate}%`, index < 2 ? "gold" : "")).join("")
    : empty;
  const neighborText = patterns.neighborNumbers?.length
    ? patterns.neighborNumbers.slice(0, 12).map((number) => chip(pad(number))).join("")
    : empty;
  const pairText = patterns.pairCombos?.length
    ? patterns.pairCombos
        .slice(0, 5)
        .map((item, index) => chip(`${pad(item.numbers[0])}-${pad(item.numbers[1])} ${item.count}次`, index < 2 ? "gold" : ""))
        .join("")
    : empty;
  const dragText = patterns.dragCards?.length
    ? patterns.dragCards
        .slice(0, 5)
        .map((item, index) => chip(`${pad(item.source)}拖${pad(item.target)} ${item.rate}%`, index < 2 ? "gold" : ""))
        .join("")
    : empty;
  const repeatText = patterns.repeatCandidates?.length
    ? patterns.repeatCandidates
        .slice(0, 5)
        .map((item, index) => chip(`${pad(item.number)} ${item.rate}%`, index < 2 ? "gold" : ""))
        .join("")
    : empty;
  const profileText = profiles
    .slice(0, 3)
    .map((item, index) => chip(`${item.label} 均${item.averageHit} / 高${item.bestHit}`, index === 0 ? "gold" : ""))
    .join("");
  els.patternLines.innerHTML = `
    <div class="pattern-soft">
      <span>近期熱門尾數</span>
      <strong class="pattern-line-main">${tailText}</strong>
      <em class="pattern-note">優先保留近期有熱度的尾數，過冷尾數降低權重。</em>
    </div>
    <div class="pattern-soft">
      <span>集中區間</span>
      <strong class="pattern-line-main">${intervalText}</strong>
      <em class="pattern-note">看近期開獎是否集中在你設定的區間帶。</em>
    </div>
    <div>
      <span>哥倆好</span>
      <strong class="pattern-line-main">${pairText}</strong>
    </div>
    <div>
      <span>拖牌</span>
      <strong class="pattern-line-main">${dragText}</strong>
    </div>
    <div>
      <span>可能連莊</span>
      <strong class="pattern-line-main">${repeatText}</strong>
    </div>
    <div>
      <span>上期鄰近</span>
      <strong class="pattern-line-main">${neighborText}</strong>
    </div>
    <div class="pattern-wide">
      <span>模型比較</span>
      <strong class="pattern-line-main">${profileText || empty}</strong>
    </div>
  `;
}

function renderSavedPicks() {
  const picks = loadSavedPicks().filter((pick) => pick.game === state.game);
  const latestNumbers = state.latest?.numbers || [];
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
            <p class="saved-meta">${gameLabel(pick.game)} · ${savedAt} · 回測 ${backtest.testedCount} 期</p>
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
            <strong>${hits}</strong>
            <span>中</span>
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
      renderSavedPicks();
      setStatus("已刪除儲存號碼。");
    });
  });
}

function render(payload) {
  const { latest, history, analysis, updatedAt } = payload;
  state.latest = latest;
  state.analysis = analysis;
  state.history = history;
  state.displayHistory = history;
  els.historyScope.textContent = "目前顯示本次載入的分析期數。";
  els.dashboard.hidden = false;
  els.gameName.textContent = latest.name;
  els.sourceLink.href = latest.sourceUrl;
  els.sourceLink.textContent = latest.source;
  els.period.textContent = `期別 ${latest.period || "-"}`;
  els.date.textContent = `日期 ${latest.date || "-"}`;
  els.latestBalls.innerHTML = balls(latest.numbers);
  els.note.textContent = analysis.note;
  renderModelBacktest(analysis.backtest, analysis.modelProfiles);
  renderPatterns(analysis.patterns, analysis.modelProfiles);
  els.hot.innerHTML = rankRows(analysis.hot, "count");
  els.cold.innerHTML = rankRows(analysis.cold, "count");
  els.overdue.innerHTML = rankRows(analysis.overdue, "gap");
  renderHistory();
  els.drawCount.textContent = `${analysis.drawCount} 期`;
  renderSavedPicks();
  renderReferencePick();
  renderCandidates();
  setStatus(`已更新：${updatedAt.replace("T", " ")}`);
}

function renderPlans(subscription) {
  if (!subscription?.plans?.length) return;
  els.plans.innerHTML = subscription.plans
    .map((plan) => {
      const isPro = plan.id === "pro";
      const action = isPro ? "訂閱 Pro" : "目前方案";
      return `
        <div class="plan ${isPro ? "pro" : ""}">
          <h3>${plan.name}</h3>
          <div class="price">${plan.price}</div>
          <ul class="features">
            ${plan.features.map((feature) => `<li>${feature}</li>`).join("")}
          </ul>
          <button class="plan-action ${isPro ? "" : "secondary"}" data-plan="${plan.id}">${action}</button>
        </div>
      `;
    })
    .join("");

  els.plans.querySelectorAll("[data-plan]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.plan !== "pro") {
        setStatus("你目前正在使用免費版。");
        return;
      }
      if (subscription.enabled && subscription.paymentLink) {
        window.open(subscription.paymentLink, "_blank", "noopener,noreferrer");
        return;
      }
      setStatus("尚未設定 Stripe 付款連結。設定 LOTTO_STRIPE_PAYMENT_LINK 後，這個按鈕就會帶使用者去訂閱。", true);
    });
  });
}

async function loadConfig() {
  try {
    const response = await fetch("/api/config");
    const payload = await response.json();
    if (payload.ok) renderPlans(payload.subscription);
  } catch (error) {
    setStatus("訂閱設定讀取失敗，但開獎資料仍可使用。", true);
  }
}

async function load() {
  const cacheKey = `${state.game}-${state.limit}`;
  const cachedPayload = state.apiCache.get(cacheKey);
  const requestId = ++state.requestId;
  if (cachedPayload) {
    render(cachedPayload);
    setStatus("已用暫存資料顯示，正在背景確認最新資料...");
  } else {
    setStatus("正在讀取資料...");
  }
  els.refresh.disabled = true;
  try {
    const response = await fetch(`/api/lottery?game=${state.game}&limit=${state.limit}`);
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "資料讀取失敗");
    if (requestId !== state.requestId) return;
    state.apiCache.set(cacheKey, payload);
    render(payload);
  } catch (error) {
    if (cachedPayload) {
      setStatus("目前使用暫存資料；背景更新暫時失敗。", true);
      return;
    }
    els.dashboard.hidden = true;
    setStatus(error.message, true);
  } finally {
    if (requestId === state.requestId) {
      els.refresh.disabled = false;
    }
  }
}

async function runCrossYearSearch() {
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
    const response = await fetch(`/api/history-search?${params}`);
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || "跨年查詢失敗");
    state.displayHistory = payload.history;
    state.historySearch.keyword = "";
    state.historySearch.number = "";
    els.historyKeyword.value = "";
    els.historyNumber.value = "";
    renderHistory();
    const years = payload.searchedYears?.length ? `${payload.searchedYears[0]}-${payload.searchedYears[payload.searchedYears.length - 1]}` : `${fromYear}-${toYear}`;
    const sourceNote = payload.sourceNote ? ` ${payload.sourceNote}` : "";
    els.historyScope.textContent = `跨年查詢：${years}，共 ${payload.total} 筆${payload.limited ? "，目前顯示前 5000 筆" : ""}。${sourceNote}`;
    setStatus(`已完成跨年查詢：${payload.total} 筆。`);
  } catch (error) {
    setStatus(error.message, true);
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
    load();
  });
});

els.limit.addEventListener("change", () => {
  state.limit = Number(els.limit.value);
  state.candidateCache.clear();
  load();
});

els.refresh.addEventListener("click", load);
els.crossYearSearch.addEventListener("click", runCrossYearSearch);

els.savedForm.addEventListener("submit", (event) => {
  event.preventDefault();
  try {
    const numbers = parseSavedInputs();
    if (savePick(numbers)) fillSavedInputs([]);
  } catch (error) {
    setStatus(error.message, true);
  }
});

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
  state.candidateCache.clear();
  renderReferencePick();
  renderCandidates();
  setStatus("已重新產生高分候選組合。");
});

els.focusButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const preset = FOCUS_PRESETS[button.dataset.focus];
    if (!preset) return;
    state.analysisFocus = button.dataset.focus;
    state.modelWeights = { ...preset.weights };
    saveAnalysisFocus();
    saveModelWeights();
    renderModelControls();
    renderSavedPicks();
    renderReferencePick();
    renderCandidates();
    setStatus(`分析重點已切換：${preset.label}。`);
  });
});

els.modelInputs.forEach((input) => {
  input.addEventListener("input", () => {
    state.modelWeights[input.dataset.weight] = Number(input.value);
    saveModelWeights();
    renderModelControls();
    renderSavedPicks();
    renderReferencePick();
    renderCandidates();
    setStatus("模型設定已更新。");
  });
});

els.resetModel.addEventListener("click", () => {
  state.analysisFocus = "balanced";
  state.modelWeights = { ...FOCUS_PRESETS.balanced.weights };
  saveAnalysisFocus();
  saveModelWeights();
  renderModelControls();
  renderSavedPicks();
  renderReferencePick();
  renderCandidates();
  setStatus("模型設定已重設。");
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
  els.historyScope.textContent = "目前顯示本次載入的分析期數。";
  renderHistory();
  setStatus("已清除歷史查詢條件。");
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

state.analysisFocus = loadAnalysisFocus();
state.modelWeights = loadModelWeights();
initHistoryYears();
renderModelControls();
loadConfig();
load();
