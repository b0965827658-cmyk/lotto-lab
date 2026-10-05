"""Attributed third-party Fantasy 5 lookup; never official verification or LINE."""
from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

GAME_ID = "CA-FANTASY5"
BASE_URL = "https://lotteryanalytics.app/api/v1/games/" + GAME_ID
SOURCE_URL = "https://lotteryanalytics.app/api-docs"
PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_BYTES = 200_000


class FeedError(ValueError):
    pass


def configured():
    return bool(os.environ.get("STEPZERO_API_KEY", "").strip())


def _request(path):
    key = os.environ.get("STEPZERO_API_KEY", "").strip()
    if not key:
        raise FeedError("第三方資料來源尚未設定。")
    req = urllib.request.Request(BASE_URL + path, headers={
        "X-API-Key": key, "Accept": "application/json", "User-Agent": "LottoLab/1.0"})
    try:
        # Do not forward credentials to any redirect destination.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect).open(req, timeout=8) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise FeedError("第三方來源未回傳有效 JSON。")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FeedError("第三方資料超出大小限制。")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise FeedError("第三方資料格式不符。")
            return payload
    except FeedError:
        raise
    except Exception:
        # Neither upstream messages nor authentication headers enter public errors.
        raise FeedError("第三方資料暫時無法取得，請稍後重試。") from None


def parse_draw(row):
    if not isinstance(row, dict):
        raise FeedError("開獎資料格式不符。")
    raw_date = row.get("draw_date")
    # The observed provider schema uses UTC midnight as a calendar-date label.
    # Converting that midnight instant to Pacific would incorrectly subtract a day.
    if not isinstance(raw_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00\.000Z", raw_date):
        raise FeedError("第三方開獎日期格式已變更。")
    try:
        day = date.fromisoformat(raw_date[:10])
    except ValueError:
        raise FeedError("開獎日期無效。") from None
    result = row.get("result")
    if not isinstance(result, str) or not re.fullmatch(r"\d{1,2}(?: \d{1,2}){4}", result):
        raise FeedError("第三方號碼格式不符。")
    numbers = sorted(int(n) for n in result.split())
    if len(set(numbers)) != 5 or not all(1 <= n <= 39 for n in numbers):
        raise FeedError("天天樂必須是 1–39 的五個不重複整數。")
    provider_id = row.get("draw_id")
    if not isinstance(provider_id, str) or not re.fullmatch(r"[0-9]{1,30}", provider_id) or row.get("draw_time") != "E":
        raise FeedError("第三方開獎識別資料不符。")
    if row.get("add_ons") is not None:
        raise FeedError("第三方彩種附加號碼格式不符。")
    return {"date": day.isoformat(), "numbers": numbers, "providerDrawId": provider_id}


def freshness(day, now):
    if now.tzinfo is None:
        raise FeedError("查詢時間必須包含時區。")
    local = now.astimezone(PACIFIC)
    drawn = date.fromisoformat(day)
    today = local.date()
    minutes = local.hour * 60 + local.minute
    expected = today if minutes >= 18 * 60 + 30 else today - timedelta(days=1)
    if drawn > expected or drawn < today - timedelta(days=1):
        raise FeedError("第三方開獎日期過期或超前，暫不顯示號碼。")
    pending = drawn != expected
    # Allow a clearly labelled previous draw during the publication window only.
    if pending and minutes >= 19 * 60:
        raise FeedError("第三方尚未更新本期資料，暫不顯示舊號碼。")
    return pending, expected.isoformat()


def validate(latest, history, now):
    if not isinstance(latest, dict) or latest.get("game_id") != GAME_ID or not isinstance(history, dict):
        raise FeedError("第三方彩種不符。")
    game = history.get("game", {})
    if not isinstance(game, dict) or any(game.get(k) != v for k, v in {
        "game_id": GAME_ID, "jurisdiction_code": "CA", "jurisdiction_name": "California",
        "game_code": "FANTASY5", "name": "Fantasy 5", "game_type": "pick_balls",
    }.items()):
        raise FeedError("來源並非加州 Fantasy 5。")
    rows = history.get("data")
    if not isinstance(rows, list) or len(rows) < 2 or len(rows) > 10 or history.get("count") != len(rows):
        raise FeedError("第三方歷史資料不足或格式不符。")
    draws = [parse_draw(row) for row in rows]
    if len({r["date"] for r in draws}) != len(draws) or len({r["providerDrawId"] for r in draws}) != len(draws):
        raise FeedError("第三方歷史包含重複日期或識別碼。")
    draws.sort(key=lambda r: r["date"], reverse=True)
    for newer, older in zip(draws, draws[1:]):
        if date.fromisoformat(newer["date"]) - date.fromisoformat(older["date"]) != timedelta(days=1):
            raise FeedError("第三方最近歷史期次不連續。")
    current = parse_draw(latest.get("latest_draw"))
    if current != draws[0]:
        raise FeedError("第三方最新與歷史結果不一致，暫不顯示號碼。")
    freshness(current["date"], now)
    # Historical anchor only; it is never promoted to the current result.
    for row in draws:
        if row["date"] == "2026-09-20" and row["numbers"] != [1, 8, 10, 19, 21]:
            raise FeedError("第三方歷史錨點不一致。")
    return current, draws


def record_draws(path, draws, fetched_at):
    """Append only to a separate third-party ledger; reject later conflicts."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS draws (draw_date TEXT PRIMARY KEY, provider_id TEXT UNIQUE NOT NULL, numbers TEXT NOT NULL, first_seen_at TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        newest = db.execute("SELECT MAX(draw_date) FROM draws").fetchone()[0]
        if newest and max(row["date"] for row in draws) < newest:
            raise FeedError("第三方回傳資料早於已取得的期次，暫停顯示。")
        pending = []
        for row in draws:
            numbers = json.dumps(row["numbers"], separators=(",", ":"))
            existing = db.execute("SELECT provider_id,numbers FROM draws WHERE draw_date=?", (row["date"],)).fetchone()
            if existing and existing != (row["providerDrawId"], numbers):
                raise FeedError("第三方同日資料發生衝突，暫停顯示並等待核對。")
            if not existing:
                reused = db.execute("SELECT draw_date FROM draws WHERE provider_id=?", (row["providerDrawId"],)).fetchone()
                if reused:
                    raise FeedError("第三方識別碼與既有日期衝突。")
                pending.append((row["date"], row["providerDrawId"], numbers, fetched_at))
        db.executemany("INSERT INTO draws VALUES (?,?,?,?)", pending)
        return len(pending)


class Feed:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = None
        self.until = 0

    def lookup(self, path, *, requester=None, now=None, force=False):
        requester = requester or _request
        now = now or datetime.now(timezone.utc)
        with self.lock:
            if force or self.cache is None or time.monotonic() >= self.until:
                started = time.perf_counter()
                try:
                    current, draws = validate(requester("/latest"), requester("/draws?limit=10"), now)
                    fetched_at = now.isoformat()
                    record_draws(path, draws, fetched_at)
                    self.cache = {"ok": True, "game": "ca-fantasy5", "notificationsEnabled": False,
                        "latest": dict(current, game="ca-fantasy5", name="加州天天樂 Fantasy 5", period=None,
                            bonus=[], source="Stepzero（第三方）", sourceUrl=SOURCE_URL,
                            fetchedAt=fetched_at, latencyMs=round((time.perf_counter()-started)*1000),
                            providerUpdatedAt=None),
                        "dataStatus": {"validated": False, "schemaValidated": True, "historyConsistent": True,
                            "trust": "third_party", "state": "third_party_latest", "timezone": "America/Los_Angeles",
                            "publicationDelayKnown": False}}
                except Exception as exc:
                    self.cache = {"ok": False, "game": "ca-fantasy5", "notificationsEnabled": False,
                        "error": str(exc) if isinstance(exc, FeedError) else "第三方資料檢查失敗，請稍後重試。",
                        "dataStatus": {"validated": False, "trust": "third_party", "state": "unavailable"}}
                self.until = time.monotonic() + 60
            result = copy.deepcopy(self.cache)
            if result["ok"]:
                try:
                    pending, expected = freshness(result["latest"]["date"], now)
                except FeedError as exc:
                    return {"ok": False, "game": "ca-fantasy5", "notificationsEnabled": False,
                        "error": str(exc), "dataStatus": {"validated": False, "trust": "third_party", "state": "stale"}}
                result["dataStatus"].update(expectedDrawDate=expected, awaitingCurrentDraw=pending)
            return result


feed = Feed()
