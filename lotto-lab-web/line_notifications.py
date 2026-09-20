"""Opt-in LINE notification subscriptions, isolated from web push and models."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


DEFAULT_GAMES = ("tw539", "mark-six", "power-lottery", "lotto-649", "daily-3", "daily-4")


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
