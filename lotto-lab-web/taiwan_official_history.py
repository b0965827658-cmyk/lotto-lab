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
    # endpoint, response key, main count, bonus maximum, bonus may overlap main,
    # require the official sorted-number field to cross-check the main numbers.
    "tw539": ("Daily539Result", "daily539Res", 5, None, False, True),
    "power-lottery": ("SuperLotto638Result", "superLotto638Res", 6, 8, True, True),
    "lotto-649": ("Lotto649Result", "lotto649Res", 6, 49, False, True),
    "daily-3": ("3DHistoryResult", "lotto3DHistoryRes", 3, None, False, False),
    "daily-4": ("4DHistoryResult", "lotto4DHistoryRes", 4, None, False, False),
}


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 LottoLab/0.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("台灣彩券官方歷史來源回傳格式不正確")
    return value


def _normalise(game: str, row: dict[str, Any], source_url: str) -> dict[str, Any]:
    _endpoint, _key, draw_size, bonus_max, allow_bonus_overlap, requires_sorted_cross_check = SPECS[game]
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
    if requires_sorted_cross_check:
        if not isinstance(size, list) or len(size) != len(values):
            raise RuntimeError("台灣彩券官方歷史資料缺少排序交叉驗證")
        try:
            sorted_values = [int(number) for number in size]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("台灣彩券官方排序資料格式不正確") from exc
        if sorted(main) != sorted_values[:draw_size] or (bonus_max and bonus != sorted_values[draw_size:]):
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


def _previous_month(month: str) -> str:
    year, month_number = (int(value) for value in month.split("-"))
    if month_number == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month_number - 1:02d}"


def _read_month(
    game: str,
    month: str,
    limit: int,
    fetcher: Callable[[str], dict[str, Any]],
) -> list[dict[str, Any]]:
    endpoint, key, _draw_size, _bonus_max, _overlap, _requires_sorted_cross_check = SPECS[game]
    query = urllib.parse.urlencode({"month": month, "endMonth": month, "pageNum": 1, "pageSize": limit})
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


def recent(game: str, *, month: str | None = None, limit: int = 10, fetcher: Callable[[str], dict[str, Any]] = _fetch_json) -> list[dict[str, Any]]:
    """Return a small, validated official history page; fail closed on any bad row."""
    if game not in SPECS:
        raise ValueError("不支援的官方歷史彩種")
    requested_month = month or datetime.now().strftime("%Y-%m")
    if len(requested_month) != 7 or requested_month[4] != "-":
        raise ValueError("月份格式必須為 YYYY-MM")
    page_size = max(1, min(100, int(limit)))
    values = _read_month(game, requested_month, page_size, fetcher)
    # Early in a month there may be fewer than five draws.  When no explicit
    # month was requested, append the preceding official month to keep the
    # LINE "recent five" view complete without using any third-party source.
    if month is None and len(values) < page_size:
        previous = _read_month(game, _previous_month(requested_month), page_size - len(values), fetcher)
        seen_periods = {row["period"] for row in values}
        if any(row["period"] in seen_periods for row in previous):
            raise RuntimeError("台灣彩券官方跨月歷史資料含重複期別")
        values.extend(previous)
    return values[:page_size]
