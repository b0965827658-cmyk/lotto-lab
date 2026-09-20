"""HKJC Mark Six official latest-result adapter for Staging LINE delivery."""

from __future__ import annotations

import gzip
import json
import urllib.request
from typing import Any, Callable


HKJC_MARK_SIX_URL = "https://info.cld.hkjc.com/graphql/base/"
HKJC_MARK_SIX_QUERY = """
fragment lotteryDrawsFragment on LotteryDraw {
  id year no openDate closeDate drawDate status snowballCode snowballName_en snowballName_ch
  lotteryPool {
    sell status totalInvestment jackpot unitBet estimatedPrize derivedFirstPrizeDiv
    lotteryPrizes { type winningUnit dividend }
  }
  drawResult { drawnNo xDrawnNo }
}
query marksixDraw {
  timeOffset { m6 ts }
  lotteryDraws { ...lotteryDrawsFragment }
}
"""


def _request_payload() -> dict[str, Any]:
    payload = json.dumps(
        {"operationName": "marksixDraw", "query": HKJC_MARK_SIX_QUERY, "variables": {}},
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


def latest(fetcher: Callable[[], dict[str, Any]] = _request_payload) -> dict[str, Any]:
    """Return only an official, completed Mark Six draw; fail closed otherwise."""
    payload = fetcher()
    rows = payload.get("data", {}).get("lotteryDraws", [])
    if not isinstance(rows, list):
        raise RuntimeError("香港六合彩官方來源未提供開獎資料")
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "Result":
            continue
        result = row.get("drawResult") or {}
        main = result.get("drawnNo")
        bonus = result.get("xDrawnNo")
        if not isinstance(main, list) or len(main) != 6:
            continue
        try:
            numbers = [int(number) for number in main]
            special = int(bonus)
        except (TypeError, ValueError):
            continue
        if len(set(numbers)) != 6 or not all(1 <= number <= 49 for number in numbers):
            continue
        if not 1 <= special <= 49 or special in numbers:
            continue
        period = str(row.get("id") or "").strip()
        draw_date = str(row.get("drawDate") or "").strip()
        if not period or not draw_date:
            continue
        return {
            "game": "mark-six",
            "name": "六合彩",
            "period": period,
            "date": draw_date[:10],
            "numbers": numbers,
            "bonus": [special],
            "bonusLabel": "特別號",
            "source": "香港賽馬會六合彩官方 GraphQL",
            "sourceUrl": HKJC_MARK_SIX_URL,
        }
    raise RuntimeError("香港六合彩最新資料尚未通過官方驗證")
