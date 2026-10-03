"""California Lottery Fantasy 5 official-source probe.

Staging-first adapter.  It never treats a third-party result as verified.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin
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
    source_url: str = API_URL

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
        if type(value) is not int and not (isinstance(value, str) and re.fullmatch(r"[0-9]+", value)):
            raise ValueError("winningNumbers contains a non-integer")
        try:
            numbers.append(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("winningNumbers contains a non-integer") from exc
    if len(numbers) != 5 or len(set(numbers)) != 5 or not all(1 <= n <= 39 for n in numbers):
        raise ValueError("Fantasy 5 requires five unique integers from 1 through 39")
    return tuple(numbers)


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
        if name == "fantasy5":
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


# An independently inspected official-page historical anchor; never third-party data.
# https://www.calottery.com/en/draw-games/fantasy-5 (observed 2026-09-22)
ANCHOR_NUMBER = 12006
ANCHOR_DATE = "2026-09-20"
ANCHOR_NUMBERS = (1, 8, 10, 19, 21)
MAX_BYTES = 2_000_000


def official_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in
            {"www.calottery.com", "calottery.com", "static.www.calottery.com", "scorigin.calottery.com"}
            or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("unapproved official URL")
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)  # Reject before contacting an external host.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("non-finite JSON number")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def request_source(url, role, attempts, timeout):
    attempt = {"role": role, "endpoint": official_url(url), "fetchedAt": time.time()}
    attempts.append(attempt)
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
            "Accept": "application/json" if role != "page" else "text/html", "Cache-Control": "no-cache"})
        with urllib.request.build_opener(OfficialRedirect()).open(req, timeout=timeout) as response:
            attempt.update(httpStatus=response.status, finalUrl=official_url(response.geturl()),
                           contentType=response.headers.get_content_type())
            body = response.read(MAX_BYTES + 1)
        attempt.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        if len(body) > MAX_BYTES:
            raise ValueError("response too large")
        if attempt["httpStatus"] != 200:
            raise ValueError("non-200 response")
        text = body.decode("utf-8-sig")
        if re.search(r"<title[^>]*>[^<]*503|503 Error.*California State Lottery", text, re.I):
            raise ValueError("maintenance HTML; not draw data")
        if role == "page":
            if attempt["contentType"] not in {"text/html", "application/xhtml+xml"}:
                raise ValueError("expected HTML Content-Type")
            return text
        if attempt["contentType"] != "application/json" and not attempt["contentType"].endswith("+json"):
            raise ValueError("expected JSON Content-Type")
        return strict_json(text)
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            attempt["httpStatus"] = exc.code
        attempt["error"] = str(exc)
        raise
    finally:
        attempt["latencyMs"] = round((time.monotonic() - started) * 1000)


class PageData(HTMLParser):
    """Only structured JSON / explicit data endpoints; never visible draw text.

    These are conservative supported formats, not a claim that the maintenance
    page exposes them. Unrecognized page formats deliberately fail closed.
    """
    def __init__(self, base):
        super().__init__()
        self.base, self.payloads, self.endpoints = base, [], []
        self.capture, self.parts = False, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.capture = attrs.get("type", "").lower() in {"application/json", "application/ld+json"}
            self.parts = []
        for key, value in attrs.items():
            if key.startswith("data-") and value:
                if value.lstrip().startswith(("{", "[")):
                    try:
                        self.payloads.append(strict_json(value))
                    except ValueError:
                        pass
                if key in {"data-api-url", "data-endpoint", "data-draw-url"}:
                    url = urljoin(self.base, value)
                    try:
                        official_url(url)
                        if url not in self.endpoints:
                            self.endpoints.append(url)
                    except ValueError:
                        pass

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.capture:
            self.capture = False
            self.payloads.append(strict_json("".join(self.parts)))


def structured_games(payload):
    if isinstance(payload, dict):
        name = payload.get("name", payload.get("gameName", ""))
        if isinstance(name, str) and name.lower().replace(" ", "") == "fantasy5":
            yield payload
        else:
            for value in payload.values():
                yield from structured_games(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from structured_games(value)


def validated_draw(payload, attempt, source):
    games = list(structured_games(payload))
    if len(games) != 1:
        raise ValueError("schema: expected exactly one identified Fantasy 5 game")
    game = games[0]
    rows = game.get("draws", [game])
    if not isinstance(rows, list) or not rows:
        raise ValueError("schema: missing draws")
    validated = []
    seen = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("schema: draw must be object")
        number = row.get("drawNumber")
        if type(number) is not int and not (isinstance(number, str) and re.fullmatch(r"[1-9][0-9]*", number)):
            raise ValueError("schema: invalid drawNumber")
        number = int(number)
        raw_date = row.get("drawDate")
        if not isinstance(raw_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?", raw_date):
            raise ValueError("schema: missing drawDate")
        parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        day = parsed.astimezone(PACIFIC).date() if parsed.tzinfo else parsed.date()
        nums = validate_numbers(row.get("winningNumbers"))
        anchor = datetime.fromisoformat(ANCHOR_DATE).date()
        if number - ANCHOR_NUMBER != (day - anchor).days:
            raise ValueError("history: draw number/date continuity mismatch")
        if number == ANCHOR_NUMBER and tuple(sorted(nums)) != ANCHOR_NUMBERS:
            raise ValueError("history: known official draw conflict")
        if day > datetime.now(PACIFIC).date():
            raise ValueError("future draw")
        if number in seen and seen[number] != (day, tuple(sorted(nums))):
            raise ValueError("conflicting duplicate draw in response")
        seen[number] = (day, tuple(sorted(nums)))
        validated.append((number, day, nums))
    number, day, nums = max(validated, key=lambda row: row[0])
    if (datetime.now(PACIFIC).date() - day).days > 1:
        raise ValueError("stale latest draw")
    attempt["schemaValid"] = True
    attempt["historyCheck"] = "official anchor and daily number/date continuity"
    return Fantasy5Draw(str(number), day.isoformat(), nums, source,
                        attempt["fetchedAt"], attempt["latencyMs"], attempt["finalUrl"])


class SourceUnavailable(ValueError):
    def __init__(self, attempts):
        super().__init__("no valid official Fantasy 5 source")
        self.attempts = attempts


def fetch_latest(timeout: float = 8.0, *, attempts=None) -> Fantasy5Draw:
    attempts = attempts if attempts is not None else []
    try:
        payload = request_source(API_URL, "api", attempts, timeout)
        return validated_draw(payload, attempts[-1], "calottery-api-v1.5")
    except Exception as exc:
        attempts[-1]["error"] = str(exc)
    try:
        html = request_source(OFFICIAL_PAGE_URL, "page", attempts, timeout)
        page_attempt = attempts[-1]
        page = PageData(page_attempt["finalUrl"])
        page.feed(html)
        if page.payloads:
            try:
                return validated_draw(page.payloads, page_attempt, "calottery-page-json")
            except ValueError as exc:
                page_attempt["embeddedJsonError"] = str(exc)
        for endpoint in page.endpoints[:4]:
            try:
                payload = request_source(endpoint, "xhr", attempts, timeout)
                return validated_draw(payload, attempts[-1], "calottery-page-xhr")
            except Exception as exc:
                attempts[-1]["error"] = str(exc)
        page_attempt["error"] = "no valid structured Fantasy 5 data; visible text is not accepted"
    except Exception as exc:
        attempts[-1]["error"] = str(exc)
    raise SourceUnavailable(attempts)


def record_draw(path, draw):
    """Immutable staging probe ledger. No access to LINE or production results."""
    official_url(draw.source_url)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS official_draws (number TEXT PRIMARY KEY, day TEXT UNIQUE, numbers TEXT, source TEXT, fetched REAL, latency INTEGER)")
        db.execute("BEGIN IMMEDIATE")
        values = json.dumps(sorted(draw.numbers))
        existing = db.execute("SELECT day, numbers FROM official_draws WHERE number=?", (draw.draw_number,)).fetchone()
        if existing:
            if existing != (draw.draw_date, values):
                raise ValueError("immutable official history conflict")
            return False
        db.execute("INSERT INTO official_draws VALUES (?,?,?,?,?,?)", (draw.draw_number, draw.draw_date, values,
                   draw.source_url, draw.fetched_at, draw.latency_ms))
        return True


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


def probe(*, ledger_path=None) -> dict[str, Any]:
    attempts = []
    try:
        draw = fetch_latest(attempts=attempts)
        result = {"ok": True, "source": draw.source, "sourceUrl": draw.source_url,
            "drawNumber": draw.draw_number, "drawDate": draw.draw_date,
            "winningNumbers": list(draw.numbers), "eventKey": draw.event_key,
            "fetchedAt": draw.fetched_at, "latencyMs": draw.latency_ms, "attempts": attempts}
        if ledger_path is not None:
            result["inserted"] = record_draw(ledger_path, draw)
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc), "attempts": attempts}
