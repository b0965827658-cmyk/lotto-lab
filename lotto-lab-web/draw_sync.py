"""Fetch-only draw synchronization. This module never sends notifications."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
PACIFIC = ZoneInfo("America/Los_Angeles")
ALL_GAMES = ("tw539", "power-lottery", "lotto-649", "daily-3", "daily-4", "mark-six", "ca-fantasy5")
TAIWAN_DAILY = ("tw539", "daily-3", "daily-4")


def due_games(now: datetime, *, startup: bool = False) -> tuple[str, ...]:
    if startup:
        return ALL_GAMES
    due: list[str] = []
    taipei = now.astimezone(TAIPEI)
    minutes = taipei.hour * 60 + taipei.minute
    if taipei.weekday() <= 5 and 20 * 60 + 30 <= minutes < 22 * 60 + 30:
        due.extend(TAIWAN_DAILY)
        if taipei.weekday() in {0, 3}:
            due.append("power-lottery")
        if taipei.weekday() in {1, 4}:
            due.append("lotto-649")
    hong_kong = now.astimezone(HONG_KONG)
    minutes = hong_kong.hour * 60 + hong_kong.minute
    if hong_kong.weekday() in {1, 3, 5, 6} and 21 * 60 <= minutes < 23 * 60 + 30:
        due.append("mark-six")
    pacific = now.astimezone(PACIFIC)
    minutes = pacific.hour * 60 + pacific.minute
    if 18 * 60 + 30 <= minutes < 20 * 60 + 30:
        due.append("ca-fantasy5")
    return tuple(dict.fromkeys(due))


def poll_seconds(now: datetime, *, active_seconds: int = 30, idle_seconds: int = 900) -> int:
    return max(15, active_seconds) if due_games(now) else max(300, idle_seconds)


class DrawSyncStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS draws (game TEXT NOT NULL, draw_key TEXT NOT NULL, draw_date TEXT NOT NULL, numbers TEXT NOT NULL, payload TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, PRIMARY KEY(game,draw_key))")
            db.execute("CREATE TABLE IF NOT EXISTS checks (game TEXT PRIMARY KEY, checked_at TEXT NOT NULL, ok INTEGER NOT NULL, detail TEXT NOT NULL)")

    def _connection(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, game: str, draw: dict, checked_at: str) -> bool:
        draw_key = str(draw.get("period") or draw.get("providerDrawId") or draw.get("date") or "").strip()
        draw_date = str(draw.get("date") or "").strip()
        numbers = draw.get("numbers")
        if not draw_key or not draw_date or not isinstance(numbers, list):
            raise ValueError("開獎同步資料缺少期別、日期或號碼")
        encoded_numbers = json.dumps(numbers, separators=(",", ":"))
        encoded_payload = json.dumps(draw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT numbers FROM draws WHERE game=? AND draw_key=?", (game, draw_key)).fetchone()
            if existing and existing[0] != encoded_numbers:
                raise ValueError("同一期號碼與既有同步紀錄衝突")
            if existing:
                db.execute("UPDATE draws SET last_seen_at=? WHERE game=? AND draw_key=?", (checked_at, game, draw_key))
            else:
                db.execute("INSERT INTO draws VALUES (?,?,?,?,?,?,?)", (game, draw_key, draw_date, encoded_numbers, encoded_payload, checked_at, checked_at))
            return not bool(existing)

    def check(self, game: str, checked_at: str, ok: bool, detail: str) -> None:
        with self._connection() as db:
            db.execute("INSERT INTO checks VALUES (?,?,?,?) ON CONFLICT(game) DO UPDATE SET checked_at=excluded.checked_at,ok=excluded.ok,detail=excluded.detail", (game, checked_at, int(ok), detail[:300]))

    def status(self) -> dict:
        with self._connection() as db:
            rows = db.execute("SELECT game,checked_at,ok,detail FROM checks ORDER BY game").fetchall()
        return {game: {"checkedAt": checked_at, "ok": bool(ok), "detail": detail} for game, checked_at, ok, detail in rows}


def run_cycle(store: DrawSyncStore, fetchers: dict, now: datetime | None = None, *, startup: bool = False) -> dict:
    current = now or datetime.now(timezone.utc)
    checked_at = current.isoformat()
    result = {"checked": [], "updated": [], "failed": {}}
    for game in due_games(current, startup=startup):
        fetcher = fetchers.get(game)
        if fetcher is None:
            result["failed"][game] = "尚未設定來源"
            store.check(game, checked_at, False, "尚未設定來源")
            continue
        try:
            draw = fetcher()
            created = store.record(game, draw, checked_at)
            store.check(game, checked_at, True, "new" if created else "unchanged")
            result["checked"].append(game)
            if created:
                result["updated"].append(game)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            store.check(game, checked_at, False, detail)
            result["failed"][game] = detail
    return result


def daemon_loop(store: DrawSyncStore, fetchers: dict, *, active_seconds=30, idle_seconds=900, clock=None, sleeper=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleeper = sleeper or time.sleep
    startup = True
    while True:
        now = clock()
        active = bool(due_games(now))
        result = run_cycle(store, fetchers, now, startup=startup or not active)
        startup = False
        if result["updated"] or result["failed"]:
            print("draw sync", json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        sleeper(poll_seconds(now, active_seconds=active_seconds, idle_seconds=idle_seconds))
