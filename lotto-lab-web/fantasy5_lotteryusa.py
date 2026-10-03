"""Strict, attributed LotteryUSA Fantasy 5 lookup for Staging LINE replies only."""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE_URL = "https://www.lotteryusa.com/california/fantasy-5/year"
PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_BYTES = 600_000
MIN_HISTORY = 10


class FeedError(ValueError):
    pass


class _ExportParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.capture = False
        self.exports: list[str] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if tag == "textarea" and "c-copy-to-clipboard__input" in classes:
            self.capture = True
            self.parts = []

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "textarea" and self.capture:
            self.exports.append("".join(self.parts))
            self.capture = False


def _request():
    request = urllib.request.Request(SOURCE_URL, headers={
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    })

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
            if response.status != 200 or response.headers.get_content_type() != "text/html":
                raise FeedError("第三方來源未回傳有效網頁。")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FeedError("第三方網頁超出大小限制。")
            return raw.decode(response.headers.get_content_charset() or "utf-8", errors="strict")
    except FeedError:
        raise
    except Exception:
        raise FeedError("第三方資料暫時無法取得，請稍後重試。") from None


def parse(html):
    parser = _ExportParser()
    try:
        parser.feed(html)
    except Exception:
        raise FeedError("第三方網頁格式無法解析。") from None
    exports = [value for value in parser.exports if value.startswith("CA Fantasy 5 numbers from LotteryUSA\n")]
    if len(exports) != 1:
        raise FeedError("第三方頁面識別資料已變更。")
    pattern = re.compile(
        r"(?m)^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
        r"([A-Z][a-z]{2} \d{2}, \d{4})\n(\d{1,2}(?:, \d{1,2}){4})$"
    )
    draws = []
    for weekday, raw_date, raw_numbers in pattern.findall(exports[0]):
        try:
            day = datetime.strptime(f"{weekday}, {raw_date}", "%A, %b %d, %Y").date()
        except ValueError:
            raise FeedError("第三方開獎日期格式已變更。") from None
        numbers = [int(number) for number in raw_numbers.split(", ")]
        if numbers != sorted(numbers) or len(set(numbers)) != 5 or not all(1 <= number <= 39 for number in numbers):
            raise FeedError("天天樂必須是 1–39 的五個遞增、不重複整數。")
        draws.append({"date": day.isoformat(), "numbers": numbers, "providerDrawId": "lotteryusa-" + day.isoformat()})
    if len(draws) < MIN_HISTORY or len({row["date"] for row in draws}) != len(draws):
        raise FeedError("第三方歷史資料不足或包含重複日期。")
    if draws != sorted(draws, key=lambda row: row["date"], reverse=True):
        raise FeedError("第三方歷史排序異常。")
    for newer, older in zip(draws[:MIN_HISTORY], draws[1:MIN_HISTORY]):
        if date.fromisoformat(newer["date"]) - date.fromisoformat(older["date"]) != timedelta(days=1):
            raise FeedError("第三方最近歷史期次不連續。")
    for row in draws:
        if row["date"] == "2026-09-20" and row["numbers"] != [1, 8, 10, 19, 21]:
            raise FeedError("第三方歷史錨點不一致。")
    return draws


def freshness(day, now):
    if now.tzinfo is None:
        raise FeedError("查詢時間必須包含時區。")
    local = now.astimezone(PACIFIC)
    today = local.date()
    minutes = local.hour * 60 + local.minute
    expected = today if minutes >= 18 * 60 + 30 else today - timedelta(days=1)
    drawn = date.fromisoformat(day)
    if drawn > expected or drawn < today - timedelta(days=1):
        raise FeedError("第三方開獎日期過期或超前，暫不顯示號碼。")
    pending = drawn != expected
    if pending and minutes >= 19 * 60:
        raise FeedError("第三方尚未更新本期資料，暫不顯示舊號碼。")
    return pending, expected.isoformat()


def record_draws(path, draws, fetched_at):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as db:
        db.execute("CREATE TABLE IF NOT EXISTS draws (draw_date TEXT PRIMARY KEY, provider_id TEXT UNIQUE NOT NULL, numbers TEXT NOT NULL, first_seen_at TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        newest = db.execute("SELECT MAX(draw_date) FROM draws").fetchone()[0]
        if newest and draws[0]["date"] < newest:
            raise FeedError("第三方回傳資料早於已取得的期次，暫停顯示。")
        pending = []
        for row in draws:
            numbers = json.dumps(row["numbers"], separators=(",", ":"))
            existing = db.execute("SELECT provider_id,numbers FROM draws WHERE draw_date=?", (row["date"],)).fetchone()
            if existing and existing != (row["providerDrawId"], numbers):
                raise FeedError("第三方同日資料發生衝突，暫停顯示並等待核對。")
            if not existing:
                pending.append((row["date"], row["providerDrawId"], numbers, fetched_at))
        db.executemany("INSERT INTO draws VALUES (?,?,?,?)", pending)


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
                    draws = parse(requester())
                    freshness(draws[0]["date"], now)
                    fetched_at = now.isoformat()
                    record_draws(path, draws, fetched_at)
                    self.cache = {"ok": True, "game": "ca-fantasy5", "notificationsEnabled": False,
                        "latest": dict(draws[0], game="ca-fantasy5", name="加州天天樂 Fantasy 5",
                            source="LotteryUSA（第三方）", sourceUrl=SOURCE_URL, fetchedAt=fetched_at,
                            latencyMs=round((time.perf_counter() - started) * 1000)),
                        "dataStatus": {"validated": False, "schemaValidated": True, "historyConsistent": True,
                            "trust": "third_party", "state": "third_party_latest", "timezone": "America/Los_Angeles"}}
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
