"""HKJC Mark Six official latest-result adapter for Staging LINE delivery."""

from __future__ import annotations

import gzip
import json
import urllib.request
from typing import Any, Callable


HKJC_MARK_SIX_URL = "https://info.cld.hkjc.com/graphql/base/"
HKJC_MARK_SIX_FRAGMENT = """
fragment lotteryDrawsFragment on LotteryDraw {
  id year no openDate closeDate drawDate status snowballCode snowballName_en snowballName_ch
  lotteryPool {
    sell status totalInvestment jackpot unitBet estimatedPrize derivedFirstPrizeDiv
    lotteryPrizes { type winningUnit dividend }
  }
  drawResult { drawnNo xDrawnNo }
}
"""

HKJC_MARK_SIX_QUERY = HKJC_MARK_SIX_FRAGMENT + """
query marksixDraw {
  timeOffset { m6 ts }
  lotteryDraws { ...lotteryDrawsFragment }
}
"""

HKJC_MARK_SIX_HISTORY_QUERY = HKJC_MARK_SIX_FRAGMENT + """
query marksixResult($lastNDraw: Int, $startDate: String, $endDate: String, $drawType: LotteryDrawType) {
  lotteryDraws(lastNDraw: $lastNDraw, startDate: $startDate, endDate: $endDate, drawType: $drawType) {
    ...lotteryDrawsFragment
  }
}
"""


def _request_payload(
    query: str = HKJC_MARK_SIX_QUERY,
    operation_name: str = "marksixDraw",
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = json.dumps(
        {"operationName": operation_name, "query": query, "variables": variables or {}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        HKJC_MARK_SIX_URL,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 LottoLab/0.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("香港六合彩官方來源回傳格式不正確")
    return value


def _normalise_completed_draw(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict) or row.get("status") != "Result":
        raise RuntimeError("香港六合彩官方歷史資料尚未完成開獎")
    result = row.get("drawResult") or {}
    main = result.get("drawnNo")
    bonus = result.get("xDrawnNo")
    if not isinstance(main, list) or len(main) != 6:
        raise RuntimeError("香港六合彩官方歷史資料主號不完整")
    try:
        numbers = [int(number) for number in main]
        special = int(bonus)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("香港六合彩官方歷史資料號碼格式不正確") from exc
    if len(set(numbers)) != 6 or not all(1 <= number <= 49 for number in numbers):
        raise RuntimeError("香港六合彩官方歷史資料主號未通過驗證")
    if not 1 <= special <= 49 or special in numbers:
        raise RuntimeError("香港六合彩官方歷史資料特別號未通過驗證")
    period = str(row.get("id") or "").strip()
    draw_date = str(row.get("drawDate") or "").strip()[:10]
    if not period or len(draw_date) != 10:
        raise RuntimeError("香港六合彩官方歷史資料期別或日期不完整")
    return {
        "game": "mark-six",
        "name": "六合彩",
        "period": period,
        "date": draw_date,
        "numbers": numbers,
        "bonus": [special],
        "bonusLabel": "特別號",
        "source": "香港賽馬會六合彩官方 GraphQL",
        "sourceUrl": HKJC_MARK_SIX_URL,
    }


def latest(fetcher: Callable[[], dict[str, Any]] = _request_payload) -> dict[str, Any]:
    """Return only an official, completed Mark Six draw; fail closed otherwise."""
    payload = fetcher()
    rows = payload.get("data", {}).get("lotteryDraws", [])
    if not isinstance(rows, list):
        raise RuntimeError("香港六合彩官方來源未提供開獎資料")
    for row in rows:
        try:
            return _normalise_completed_draw(row)
        except RuntimeError:
            continue
    raise RuntimeError("香港六合彩最新資料尚未通過官方驗證")


def history(limit: int = 5, fetcher: Callable[[], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return a small, fully validated official Mark Six history page."""
    safe_limit = max(1, min(20, int(limit)))
    if fetcher is None:
        fetcher = lambda: _request_payload(
            query=HKJC_MARK_SIX_HISTORY_QUERY,
            operation_name="marksixResult",
            variables={"lastNDraw": safe_limit, "startDate": None, "endDate": None, "drawType": "All"},
        )
    payload = fetcher()
    rows = payload.get("data", {}).get("lotteryDraws", [])
    if not isinstance(rows, list) or len(rows) < safe_limit:
        raise RuntimeError("香港六合彩官方歷史資料不足")
    values = [_normalise_completed_draw(row) for row in rows[:safe_limit]]
    periods = {row["period"] for row in values}
    if len(periods) != len(values):
        raise RuntimeError("香港六合彩官方歷史資料含重複期別")
    return sorted(values, key=lambda row: (row["date"], row["period"]), reverse=True)
