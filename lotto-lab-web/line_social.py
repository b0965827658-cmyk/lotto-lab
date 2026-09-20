"""Private, opt-in Staging store for LINE player sharing and discussion."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from lottery_registry import BY_CODE, validate_main_numbers


GAME_ALIASES = {
    "539": "tw539",
    "今彩539": "tw539",
    "六合彩": "mark-six",
    "威力彩": "power-lottery",
    "大樂透": "lotto-649",
    "三星彩": "daily-3",
    "四星彩": "daily-4",
}
SHAREABLE_GAMES = frozenset(GAME_ALIASES.values())


class LineSocialStore:
    """Keeps social votes separate from all draw, model and journal data."""

    def __init__(self, path: Path, *, enabled: bool, tester_ids: set[str], anonymous_key: str) -> None:
        self.path = path
        self.enabled = enabled
        self.tester_ids = tester_ids
        self.anonymous_key = anonymous_key

    def _allowed(self, user_id: str) -> bool:
        return self.enabled and bool(self.anonymous_key) and user_id in self.tester_ids

    def _connection(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS social_events (event_id TEXT PRIMARY KEY, received_at INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS social_entries (player TEXT NOT NULL, game TEXT NOT NULL, round_key TEXT NOT NULL, numbers_json TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY(player, game, round_key))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS social_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL, message TEXT NOT NULL, created_at INTEGER NOT NULL, hidden INTEGER NOT NULL DEFAULT 0)"
        )
        return connection

    def _player(self, user_id: str) -> str:
        digest = hmac.new(self.anonymous_key.encode("utf-8"), user_id.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"玩家-{digest[:8].upper()}"

    @staticmethod
    def _round_key() -> str:
        return datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()

    @staticmethod
    def _first_event(connection: sqlite3.Connection, event_id: str) -> bool:
        if not event_id:
            return True
        try:
            connection.execute("INSERT INTO social_events(event_id, received_at) VALUES(?, ?)", (event_id, int(time.time())))
        except sqlite3.IntegrityError:
            return False
        return True

    def share(self, user_id: str, game_text: str, raw_numbers: list[int], event_id: str = "") -> str:
        if not self._allowed(user_id):
            return "玩家分享功能準備中。"
        game = GAME_ALIASES.get(game_text)
        if not game or game not in SHAREABLE_GAMES:
            return "找不到可分享彩種。請使用：539、六合彩、威力彩、大樂透、三星彩或四星彩。"
        if not validate_main_numbers(game, raw_numbers):
            rule = BY_CODE[game]
            return f"號碼格式不正確；{rule.name}請輸入 {rule.draw_size} 個合法號碼。"
        player, round_key, now = self._player(user_id), self._round_key(), int(time.time())
        with self._connection() as connection:
            if not self._first_event(connection, event_id):
                return "這則分享已收到，不會重複計算。"
            connection.execute(
                "INSERT INTO social_entries(player, game, round_key, numbers_json, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?) ON CONFLICT(player, game, round_key) DO UPDATE SET numbers_json=excluded.numbers_json, updated_at=excluded.updated_at",
                (player, game, round_key, json.dumps(raw_numbers), now, now),
            )
        numbers = "、".join(f"{number:02d}" for number in raw_numbers)
        return f"已分享 {BY_CODE[game].name}：{numbers}\n同彩種當天再次分享會更新原本的一組。玩家熱門號碼不代表 AI 推薦或開獎機率。"

    def remove_share(self, user_id: str) -> str:
        if not self._allowed(user_id):
            return "玩家分享功能準備中。"
        with self._connection() as connection:
            connection.execute("DELETE FROM social_entries WHERE player=? AND round_key=?", (self._player(user_id), self._round_key()))
        return "已刪除你今天的分享號碼。"

    def hot_numbers(self, user_id: str, game_text: str) -> str:
        if not self._allowed(user_id):
            return "玩家分享功能準備中。"
        game = GAME_ALIASES.get(game_text)
        if not game:
            return "請在「熱門」後加彩種，例如：熱門 539"
        with self._connection() as connection:
            rows = connection.execute("SELECT numbers_json FROM social_entries WHERE game=? AND round_key=?", (game, self._round_key())).fetchall()
        counts: dict[int, int] = {}
        for (numbers_json,) in rows:
            try:
                values = json.loads(numbers_json)
            except ValueError:
                continue
            for number in values:
                counts[int(number)] = counts.get(int(number), 0) + 1
        if not counts:
            return f"{BY_CODE[game].name}今天還沒有玩家分享。"
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        numbers = "、".join(f"{number:02d}（{count}）" for number, count in top)
        return f"{BY_CODE[game].name}今日玩家熱門號碼\n{numbers}\n僅代表玩家票選熱度，不代表 AI 推薦或開獎機率。"

    def active_ranking(self, user_id: str, game_text: str) -> str:
        if not self._allowed(user_id):
            return "玩家分享功能準備中。"
        game = GAME_ALIASES.get(game_text)
        if not game:
            return "請在「排行」後加彩種，例如：排行 539"
        cutoff = int(time.time()) - 30 * 24 * 60 * 60
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT player, COUNT(*) FROM social_entries WHERE game=? AND updated_at>=? GROUP BY player ORDER BY COUNT(*) DESC, player ASC LIMIT 10",
                (game, cutoff),
            ).fetchall()
        if not rows:
            return f"{BY_CODE[game].name}目前還沒有排行榜資料。"
        lines = [f"{BY_CODE[game].name}玩家分享排行（近 30 天分享天數）"]
        lines.extend(f"{index}. {player}：{count} 天" for index, (player, count) in enumerate(rows, 1))
        return "\n".join(lines)

    def post(self, user_id: str, message: str, event_id: str = "") -> str:
        if not self._allowed(user_id):
            return "討論區準備中。"
        message = " ".join(message.split())[:180]
        if not message:
            return "請在「討論」後輸入內容。"
        lowered = message.lower()
        if "http://" in lowered or "https://" in lowered or "line.me" in lowered:
            return "討論區暫不允許連結。"
        now, player = int(time.time()), self._player(user_id)
        with self._connection() as connection:
            if not self._first_event(connection, event_id):
                return "這則留言已收到，不會重複發布。"
            recent = connection.execute("SELECT created_at FROM social_posts WHERE player=? ORDER BY created_at DESC LIMIT 1", (player,)).fetchone()
            if recent and now - int(recent[0]) < 60:
                return "請稍後一分鐘再留言。"
            connection.execute("INSERT INTO social_posts(player, message, created_at) VALUES(?, ?, ?)", (player, message, now))
        return "已發到玩家討論區。輸入「討論區」可查看最新留言。"

    def recent_posts(self, user_id: str) -> str:
        if not self._allowed(user_id):
            return "討論區準備中。"
        with self._connection() as connection:
            rows = connection.execute("SELECT player, message FROM social_posts WHERE hidden=0 ORDER BY id DESC LIMIT 8").fetchall()
        if not rows:
            return "討論區目前還沒有留言。"
        return "玩家討論區\n" + "\n".join(f"• {player}：{message}" for player, message in rows)
