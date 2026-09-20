"""Opt-in LINE notification subscriptions, isolated from web push and models."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_GAMES = ("tw539", "mark-six", "power-lottery", "lotto-649", "daily-3", "daily-4")
TAIWAN_PRE_DRAW_WEEKDAYS = {
    "tw539": frozenset({0, 1, 2, 3, 4, 5}),
    "power-lottery": frozenset({0, 3}),
    "lotto-649": frozenset({1, 4}),
    "daily-3": frozenset({0, 1, 2, 3, 4, 5}),
    "daily-4": frozenset({0, 1, 2, 3, 4, 5}),
}


class LineNotificationStore:
    def __init__(self, path: Path, *, enabled: bool, tester_ids: set[str]) -> None:
        self.path = path
        self.enabled = enabled
        self.tester_ids = tester_ids

    def _connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS subscribers (user_id TEXT PRIMARY KEY, games_json TEXT NOT NULL, active INTEGER NOT NULL, consent_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
        )
        connection.execute("CREATE TABLE IF NOT EXISTS webhook_events (event_id TEXT PRIMARY KEY, received_at INTEGER NOT NULL)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS deliveries (event_key TEXT NOT NULL, user_id TEXT NOT NULL, state TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(event_key, user_id))"
        )
        return connection

    def _allowed(self, user_id: str) -> bool:
        return self.enabled and bool(user_id) and user_id in self.tester_ids

    def subscribe(self, user_id: str) -> str:
        if not self._allowed(user_id):
            return "LINE 通知測試尚未開放。"
        now = int(time.time())
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO subscribers(user_id, games_json, active, consent_at, updated_at) VALUES(?, ?, 1, ?, ?) ON CONFLICT(user_id) DO UPDATE SET games_json=excluded.games_json, active=1, consent_at=excluded.consent_at, updated_at=excluded.updated_at",
                (user_id, json.dumps(DEFAULT_GAMES), now, now),
            )
        return "已開啟測試通知。開獎前一小時與官方結果確認後才會通知；可隨時輸入「通知關閉」。"

    def unsubscribe(self, user_id: str) -> str:
        if not self._allowed(user_id):
            return "LINE 通知測試尚未開放。"
        now = int(time.time())
        with self._connection() as connection:
            connection.execute("UPDATE subscribers SET active=0, updated_at=? WHERE user_id=?", (now, user_id))
        return "已關閉通知。"

    def status(self, user_id: str) -> str:
        if not self._allowed(user_id):
            return "LINE 通知測試尚未開放。"
        with self._connection() as connection:
            row = connection.execute("SELECT active FROM subscribers WHERE user_id=?", (user_id,)).fetchone()
        return "目前通知：已開啟" if row and row[0] else "目前通知：未開啟"

    def record_webhook_event(self, event_id: str) -> bool:
        """Return false for a duplicate LINE redelivery without exposing event IDs."""
        if not self.enabled or not event_id:
            return True
        with self._connection() as connection:
            try:
                connection.execute("INSERT INTO webhook_events(event_id, received_at) VALUES(?, ?)", (event_id, int(time.time())))
            except sqlite3.IntegrityError:
                return False
        return True

    def follow(self, user_id: str) -> None:
        # Following the account is not consent to push notifications. The user
        # must explicitly send the notification-enable command.
        return None

    def unfollow(self, user_id: str) -> None:
        if self._allowed(user_id):
            self.unsubscribe(user_id)

    def recipients(self, game: str) -> list[str]:
        if not self.enabled:
            return []
        with self._connection() as connection:
            rows = connection.execute("SELECT user_id, games_json FROM subscribers WHERE active=1").fetchall()
        recipients = []
        for user_id, games_json in rows:
            try:
                games = json.loads(games_json)
            except ValueError:
                continue
            if user_id in self.tester_ids and game in games:
                recipients.append(str(user_id))
        return recipients

    def claim_delivery(self, event_key: str, user_id: str) -> bool:
        """Claim one delivery; a stuck pending claim is retriable after five minutes."""
        if not self.enabled:
            return False
        now = int(time.time())
        with self._connection() as connection:
            row = connection.execute("SELECT state, updated_at FROM deliveries WHERE event_key=? AND user_id=?", (event_key, user_id)).fetchone()
            if row and row[0] == "sent":
                return False
            if row and row[0] == "pending" and now - int(row[1]) < 300:
                return False
            connection.execute(
                "INSERT INTO deliveries(event_key, user_id, state, updated_at) VALUES(?, ?, 'pending', ?) ON CONFLICT(event_key, user_id) DO UPDATE SET state='pending', updated_at=excluded.updated_at",
                (event_key, user_id, now),
            )
        return True

    def finish_delivery(self, event_key: str, user_id: str, *, sent: bool) -> None:
        if not self.enabled:
            return
        with self._connection() as connection:
            connection.execute(
                "UPDATE deliveries SET state=?, updated_at=? WHERE event_key=? AND user_id=?",
                ("sent" if sent else "failed", int(time.time()), event_key, user_id),
            )


def taiwan_pre_draw_games(now: datetime) -> list[str]:
    """Return games in the official Taiwan 19:30 local pre-draw window."""
    if now.tzinfo is None:
        raise ValueError("通知時間必須含時區")
    local = now.astimezone(ZoneInfo("Asia/Taipei"))
    if local.hour != 19 or not 30 <= local.minute < 35:
        return []
    return [game for game, weekdays in TAIWAN_PRE_DRAW_WEEKDAYS.items() if local.weekday() in weekdays]
