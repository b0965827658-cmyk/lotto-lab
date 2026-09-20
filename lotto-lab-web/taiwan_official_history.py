"""Official Taiwan Lottery history adapter, isolated from the existing models."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable

from lottery_registry import validate_main_numbers


TAIWAN_LOTTERY_BASE = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/"
SPECS = {
    "power-lottery": ("SuperLotto638Result", "superLotto638Res", 6, 8, True),
    "lotto-649": ("Lotto649Result", "lotto649Res", 6, 49, False),
    "daily-3": ("3DHistoryResult", "lotto3DHistoryRes", 3, None, False),
    "daily-4": ("4DHistoryResult", "lotto4DHistoryRes", 4, None, False),
}


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 LottoLab/0.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("台灣彩券官方歷史來源回傳格式不正確")
    return value


def _normalise(game: str, row: dict[str, Any], source_url: str) -> dict[str, Any]:
    _endpoint, _key, draw_size, bonus_max, allow_bonus_overlap = SPECS[game]
    period = str(row.get("period") or "").strip()
    date = str(row.get("lotteryDate") or "").strip()[:10]
    raw = row.get("drawNumberAppear")
    size = row.get("drawNumberSize")
    if not period or len(date) != 10 or not isinstance(raw, list) or len(raw) != draw_size + (1 if bonus_max else 0):
        raise RuntimeError("台灣彩券官方歷史資料欄位不完整")
    try:
        values = [int(number) for number in raw]
    except (TypeError, ValueError) as exc:
        raise RuntimeError("台灣彩券官方歷史資料號碼格式不正確") from exc
    main, bonus = values[:draw_size], values[draw_size:]
    if not validate_main_numbers(game, main):
        raise RuntimeError("台灣彩券官方歷史資料未通過主號驗證")
    if bonus_max:
        if len(bonus) != 1 or not 1 <= bonus[0] <= bonus_max:
            raise RuntimeError("台灣彩券官方歷史資料未通過特別號驗證")
        if not allow_bonus_overlap and bonus[0] in main:
            raise RuntimeError("台灣彩券官方歷史資料特別號重複")
        if not isinstance(size, list) or len(size) != len(values):
            raise RuntimeError("台灣彩券官方歷史資料缺少排序交叉驗證")
        try:
            sorted_values = [int(number) for number in size]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("台灣彩券官方排序資料格式不正確") from exc
        if sorted(main) != sorted_values[:draw_size] or bonus != sorted_values[draw_size:]:
            raise RuntimeError("台灣彩券官方歷史資料排序交叉驗證失敗")
    return {
        "game": game,
        "period": period,
        "date": date,
        "numbers": main,
        "sortedNumbers": sorted(main) if len(set(main)) == len(main) else main,
        "bonus": bonus,
        "source": "台灣彩券官方歷史 API",
        "sourceUrl": source_url,
        "sourceHash": hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
    }


def recent(game: str, *, month: str | None = None, limit: int = 10, fetcher: Callable[[str], dict[str, Any]] = _fetch_json) -> list[dict[str, Any]]:
    """Return a small, validated official history page; fail closed on any bad row."""
    if game not in SPECS:
        raise ValueError("不支援的官方歷史彩種")
    endpoint, key, _draw_size, _bonus_max, _overlap = SPECS[game]
    month = month or datetime.now().strftime("%Y-%m")
    if len(month) != 7 or month[4] != "-":
        raise ValueError("月份格式必須為 YYYY-MM")
    page_size = max(1, min(100, int(limit)))
    query = urllib.parse.urlencode({"month": month, "endMonth": month, "pageNum": 1, "pageSize": page_size})
    source_url = f"{TAIWAN_LOTTERY_BASE}{endpoint}?{query}"
    payload = fetcher(source_url)
    if payload.get("rtCode") != 0:
        raise RuntimeError("台灣彩券官方歷史來源目前不可用")
    rows = payload.get("content", {}).get(key)
    if not isinstance(rows, list):
        raise RuntimeError("台灣彩券官方歷史來源未提供資料")
    values = [_normalise(game, row, source_url) for row in rows if isinstance(row, dict)]
    periods = {row["period"] for row in values}
    if len(periods) != len(values):
        raise RuntimeError("台灣彩券官方歷史來源含重複期別")
    return values[:limit]
