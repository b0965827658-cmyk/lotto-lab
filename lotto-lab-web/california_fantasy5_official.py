"""California Lottery Fantasy 5 official-source probe.

Staging-first adapter.  It never treats a third-party result as verified.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

API_URL = "https://www.calottery.com/api/v1.5/drawgames"
OFFICIAL_PAGE_URL = "https://www.calottery.com/draw-games/fantasy-5"
USER_AGENT = "Mozilla/5.0 LottoLab/1.0 (+official-result-probe)"
PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class Fantasy5Draw:
    draw_number: str
    draw_date: str
    numbers: tuple[int, ...]
    source: str
    fetched_at: float
    latency_ms: int

    @property
    def event_key(self) -> str:
        return f"ca-fantasy5:{self.draw_number}"


def validate_numbers(values: Any) -> tuple[int, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("winningNumbers must be a list")
    numbers: list[int] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("number", value.get("value"))
        try:
            numbers.append(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("winningNumbers contains a non-integer") from exc
    if len(numbers) != 5 or len(set(numbers)) != 5 or not all(1 <= n <= 39 for n in numbers):
        raise ValueError("Fantasy 5 requires five unique integers from 1 through 39")
    return tuple(numbers)


def _get_json(url: str, timeout: float = 8.0) -> tuple[Any, int]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Cache-Control": "no-cache"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload, round((time.monotonic() - started) * 1000)


def _games(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("drawGames", "games", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def discover_fantasy5(payload: Any) -> dict[str, Any]:
    matches = []
    for game in _games(payload):
        name = str(game.get("name") or game.get("gameName") or "").lower().replace(" ", "")
        if "fantasy5" in name:
            matches.append(game)
    if len(matches) != 1:
        raise ValueError(f"expected one Fantasy 5 game, found {len(matches)}")
    return matches[0]


def _latest_draw(game: dict[str, Any]) -> dict[str, Any]:
    draws = game.get("draws")
    if isinstance(draws, list) and draws:
        rows = [row for row in draws if isinstance(row, dict)]
        if rows:
            return max(rows, key=lambda row: int(row.get("drawNumber") or 0))
    # Some API revisions expose the current draw directly on the game object.
    if game.get("drawNumber") is not None:
        return game
    raise ValueError("official Fantasy 5 response has no draw")


def fetch_latest(timeout: float = 8.0) -> Fantasy5Draw:
    payload, latency_ms = _get_json(API_URL, timeout=timeout)
    game = discover_fantasy5(payload)
    draw = _latest_draw(game)
    draw_number = str(draw.get("drawNumber") or "").strip()
    draw_date = str(draw.get("drawDate") or "").strip()
    if not draw_number or not draw_date:
        raise ValueError("official draw is missing drawNumber/drawDate")
    numbers = validate_numbers(draw.get("winningNumbers"))
    return Fantasy5Draw(
        draw_number=draw_number,
        draw_date=draw_date,
        numbers=numbers,
        source="calottery-api-v1.5",
        fetched_at=time.time(),
        latency_ms=latency_ms,
    )


def in_fast_poll_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(tz=PACIFIC)
    if now.tzinfo is None:
        raise ValueError("poll clock must be timezone-aware")
    local = now.astimezone(PACIFIC)
    # Draw break starts at 18:30 PT.  Keep the sprint bounded.
    return local.hour == 18 and 28 <= local.minute <= 50


def recommended_poll_seconds(now: datetime | None = None, failures: int = 0) -> int:
    base = 10 if in_fast_poll_window(now) else 300
    if failures <= 0:
        return base
    return min(300, max(base, 2 ** min(failures, 8)))


def probe() -> dict[str, Any]:
    try:
        draw = fetch_latest()
        return {
            "ok": True,
            "source": draw.source,
            "drawNumber": draw.draw_number,
            "drawDate": draw.draw_date,
            "winningNumbers": list(draw.numbers),
            "eventKey": draw.event_key,
            "latencyMs": draw.latency_ms,
        }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "source": "calottery-api-v1.5", "httpStatus": exc.code, "fallback": OFFICIAL_PAGE_URL}
    except Exception as exc:
        return {"ok": False, "source": "calottery-api-v1.5", "error": type(exc).__name__, "fallback": OFFICIAL_PAGE_URL}
