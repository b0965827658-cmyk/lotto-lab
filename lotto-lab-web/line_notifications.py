"""Opt-in LINE notification subscriptions, isolated from web push and models."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_GAMES = ("tw539", "mark-six", "power-lottery", "lotto-649", "daily-3", "daily-4")
GAME_ALIASES = {
    "539": "tw539",
    "今彩539": "tw539",
    "tw539": "tw539",
    "六合彩": "mark-six",
    "威力彩": "power-lottery",
    "大樂透": "lotto-649",
    "三星彩": "daily-3",
    "四星彩": "daily-4",
}
GAME_LABELS = {
    "tw539": "今彩539",
    "mark-six": "六合彩",
    "power-lottery": "威力彩",
    "lotto-649": "大樂透",
    "daily-3": "三星彩",
    "daily-4": "四星彩",
}
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
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        if self._initialized:
            return connection
        try:
            with self._init_lock:
                if not self._initialized:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS subscribers (user_id TEXT PRIMARY KEY, games_json TEXT NOT NULL, active INTEGER NOT NULL, consent_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)"
                    )
                    connection.execute("CREATE TABLE IF NOT EXISTS webhook_events (event_id TEXT PRIMARY KEY, received_at INTEGER NOT NULL)")
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS deliveries (event_key TEXT NOT NULL, user_id TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL, retry_key TEXT NOT NULL DEFAULT '', PRIMARY KEY(event_key, user_id))"
                    )
                    connection.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(deliveries)").fetchall()}
                    if "retry_key" not in columns:
                        connection.execute("ALTER TABLE deliveries ADD COLUMN retry_key TEXT NOT NULL DEFAULT ''")
                    if "created_at" not in columns:
                        connection.execute("ALTER TABLE deliveries ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0")
                    connection.execute("UPDATE deliveries SET created_at=updated_at WHERE created_at=0")
                    connection.commit()
                    self._initialized = True
        except Exception:
            connection.close()
            raise
        return connection

    def _allowed(self, user_id: str) -> bool:
        return self.enabled and bool(user_id) and user_id in self.tester_ids

    @staticmethod
    def _labels(games: list[str] | tuple[str, ...]) -> str:
        return "、".join(GAME_LABELS[game] for game in DEFAULT_GAMES if game in games)

    @staticmethod
    def _normalise_games(tokens: list[str]) -> tuple[str, ...]:
        values = [token.strip().lower() for token in tokens if token.strip()]
        if not values or any(token in {"全部", "全開", "all"} for token in values):
            return DEFAULT_GAMES
        selected = []
        for token in values:
            game = GAME_ALIASES.get(token)
            if not game:
                raise ValueError("找不到彩種")
            if game not in selected:
                selected.append(game)
        return tuple(game for game in DEFAULT_GAMES if game in selected)

    def _setting(self, key: str, default: str = "") -> str:
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def _set_setting(self, key: str, value: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, int(time.time())),
            )

    def subscribe(self, user_id: str) -> str:
        if not self._allowed(user_id):
            return "LINE 通知測試尚未開放。"
        now = int(time.time())
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO subscribers(user_id, games_json, active, consent_at, updated_at) VALUES(?, ?, 1, ?, ?) ON CONFLICT(user_id) DO UPDATE SET games_json=excluded.games_json, active=1, consent_at=excluded.consent_at, updated_at=excluded.updated_at",
                (user_id, json.dumps(DEFAULT_GAMES), now, now),
            )
        return (
            f"已開啟測試通知：{self._labels(DEFAULT_GAMES)}。目前只在官方結果確認後通知；"
            "開獎前提醒會等官方當期時程驗證完成後再開放。"
            "可隨時輸入「通知關閉」或「通知設定 彩種」。"
        )

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
            row = connection.execute("SELECT active, games_json FROM subscribers WHERE user_id=?", (user_id,)).fetchone()
        if not row or not row[0]:
            return "目前通知：未開啟"
        try:
            games = json.loads(row[1])
        except (TypeError, ValueError):
            games = []
        pause_note = "（管理員暫停中）" if self.is_paused() else ""
        return f"目前通知：已開啟{pause_note}\n彩種：{self._labels(games)}"

    def configure(self, user_id: str, game_tokens: list[str]) -> str:
        if not self._allowed(user_id):
            return "LINE 通知測試尚未開放。"
        try:
            games = self._normalise_games(game_tokens)
        except ValueError:
            return "找不到彩種。可設定：539、六合彩、威力彩、大樂透、三星彩、四星彩，或「全部」。"
        now = int(time.time())
        with self._connection() as connection:
            row = connection.execute("SELECT active FROM subscribers WHERE user_id=?", (user_id,)).fetchone()
            if not row or not row[0]:
                return "請先輸入「通知開啟」，再調整彩種。"
            connection.execute("UPDATE subscribers SET games_json=?, updated_at=? WHERE user_id=?", (json.dumps(games), now, user_id))
        return f"已調整通知彩種：{self._labels(games)}"

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

    def is_paused(self) -> bool:
        return self._setting("global_pause", "0") == "1"

    def set_paused(self, paused: bool) -> bool:
        if not self.enabled:
            return False
        self._set_setting("global_pause", "1" if paused else "0")
        return True

    def paused_games(self) -> tuple[str, ...]:
        try:
            values = json.loads(self._setting("paused_games", "[]"))
        except (TypeError, ValueError):
            return ()
        return tuple(game for game in DEFAULT_GAMES if game in values)

    def is_game_paused(self, game: str) -> bool:
        return game in self.paused_games()

    def set_game_paused(self, game: str, paused: bool) -> bool:
        if not self.enabled or game not in DEFAULT_GAMES:
            return False
        values = set(self.paused_games())
        if paused:
            values.add(game)
        else:
            values.discard(game)
        ordered = [candidate for candidate in DEFAULT_GAMES if candidate in values]
        self._set_setting("paused_games", json.dumps(ordered))
        return True

    def summary(self) -> dict[str, object]:
        if not self.enabled:
            return {"active": 0, "total": 0, "byGame": {}, "paused": False, "pausedGames": []}
        with self._connection() as connection:
            rows = connection.execute("SELECT active, games_json FROM subscribers").fetchall()
        active = 0
        by_game = {game: 0 for game in DEFAULT_GAMES}
        for is_active, games_json in rows:
            if not is_active:
                continue
            active += 1
            try:
                games = json.loads(games_json)
            except (TypeError, ValueError):
                games = []
            for game in games:
                if game in by_game:
                    by_game[game] += 1
        return {
            "active": active,
            "total": len(rows),
            "byGame": by_game,
            "paused": self.is_paused(),
            "pausedGames": list(self.paused_games()),
        }

    def recipients(self, game: str) -> list[str]:
        if not self.enabled or self.is_paused() or self.is_game_paused(game):
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

    def claim_delivery(self, event_key: str, user_id: str) -> str | None:
        """Atomically claim one delivery and retain its LINE retry key across retries."""
        if not self.enabled:
            return None
        now = int(time.time())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, created_at, updated_at, retry_key FROM deliveries WHERE event_key=? AND user_id=?",
                (event_key, user_id),
            ).fetchone()
            if row and row[0] in {"sent", "permanent", "expired"}:
                return None
            created_at = int(row[1]) if row and row[1] else (int(row[2]) if row else now)
            if row and now - created_at >= 24 * 60 * 60:
                connection.execute(
                    "UPDATE deliveries SET state='expired', updated_at=? WHERE event_key=? AND user_id=?",
                    (now, event_key, user_id),
                )
                return None
            if row and row[0] in {"pending", "failed"} and now - int(row[2]) < 300:
                return None
            retry_key = str(row[3]) if row and row[3] else str(uuid.uuid4())
            connection.execute(
                "INSERT INTO deliveries(event_key, user_id, state, created_at, updated_at, retry_key) VALUES(?, ?, 'pending', ?, ?, ?) ON CONFLICT(event_key, user_id) DO UPDATE SET state='pending', updated_at=excluded.updated_at, retry_key=excluded.retry_key",
                (event_key, user_id, created_at, now, retry_key),
            )
        return retry_key

    def finish_delivery(self, event_key: str, user_id: str, *, sent: bool = False, permanent: bool = False) -> None:
        if not self.enabled:
            return
        state = "sent" if sent else "permanent" if permanent else "failed"
        with self._connection() as connection:
            connection.execute(
                "UPDATE deliveries SET state=?, updated_at=? WHERE event_key=? AND user_id=?",
                (state, int(time.time()), event_key, user_id),
            )


def taiwan_pre_draw_games(now: datetime) -> list[str]:
    """Return games in the official Taiwan 19:30 local pre-draw window."""
    if now.tzinfo is None:
        raise ValueError("通知時間必須含時區")
    local = now.astimezone(ZoneInfo("Asia/Taipei"))
    if local.hour != 19 or not 30 <= local.minute < 35:
        return []
    return [game for game, weekdays in TAIWAN_PRE_DRAW_WEEKDAYS.items() if local.weekday() in weekdays]
