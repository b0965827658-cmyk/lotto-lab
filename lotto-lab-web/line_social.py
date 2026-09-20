"""Opt-in, staging-safe LINE player sharing features.

This data store is intentionally separate from draw history, models, analysis, and
prediction journals.  It stores only player submissions and short discussions.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from lottery_registry import BY_CODE, validate_main_numbers


GAME_ALIASES = {
    "539": "tw539",
    "今彩539": "tw539",
    "天天樂": "ca-fantasy5",
    "加州天天樂": "ca-fantasy5",
    "六合彩": "mark-six",
    "威力彩": "power-lottery",
    "大樂透": "lotto-649",
    "三星彩": "daily-3",
    "四星彩": "daily-4",
}


def anonymous_player(user_id: str) -> str:
    """Return a stable anonymous label without persisting the LINE ID in output."""
    return f"玩家-{hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:6].upper()}"


class LineSocialStore:
    def __init__(self, path: Path, *, enabled: bool) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = threading.Lock()

    def _data(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {"shares": [], "posts": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("shares"), list) and isinstance(value.get("posts"), list):
                return value
        except (OSError, ValueError):
            pass
        return {"shares": [], "posts": []}

    def _save(self, value: dict[str, list[dict[str, Any]]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".line-social-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def share(self, user_id: str, game_text: str, raw_numbers: list[int], event_id: str = "") -> str:
        if not self.enabled:
            return "玩家分享功能準備中。"
        game = GAME_ALIASES.get(game_text)
        if not game or game not in BY_CODE:
            return "找不到彩種。請使用：539、加州天天樂、六合彩、威力彩、大樂透、三星彩或四星彩。"
        if not validate_main_numbers(game, raw_numbers):
            rule = BY_CODE[game]
            return f"號碼格式不正確；{rule.name}請輸入 {rule.draw_size} 個合法號碼。"
        now = int(time.time())
        record = {
            "player": anonymous_player(user_id),
            "game": game,
            "numbers": raw_numbers,
            "created_at": now,
        }
        with self._lock:
            data = self._data()
            if event_id and any(row.get("event_id") == event_id for row in data["shares"]):
                return "這則分享已收到，不會重複計算。"
            record["event_id"] = event_id
            data["shares"].append(record)
            data["shares"] = data["shares"][-3000:]
            self._save(data)
        numbers = "、".join(f"{number:02d}" for number in raw_numbers)
        return f"已分享 {BY_CODE[game].name}：{numbers}\n會列入熱門號碼統計；實際命中排名會在官方開獎驗證後計算。"

    def hot_numbers(self, game_text: str) -> str:
        if not self.enabled:
            return "玩家分享功能準備中。"
        game = GAME_ALIASES.get(game_text)
        if not game:
            return "請在「熱門」後加彩種，例如：熱門 539"
        with self._lock:
            shares = [row for row in self._data()["shares"] if row.get("game") == game]
        counts: dict[int, int] = {}
        for row in shares:
            for number in row.get("numbers", []):
                counts[number] = counts.get(number, 0) + 1
        if not counts:
            return f"{BY_CODE[game].name}目前還沒有玩家分享。"
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        return f"{BY_CODE[game].name}玩家熱門號碼\n" + "、".join(f"{number:02d}（{count}）" for number, count in top)

    def active_ranking(self, game_text: str) -> str:
        if not self.enabled:
            return "玩家分享功能準備中。"
        game = GAME_ALIASES.get(game_text)
        if not game:
            return "請在「排行」後加彩種，例如：排行 539"
        with self._lock:
            shares = [row for row in self._data()["shares"] if row.get("game") == game]
        counts: dict[str, int] = {}
        for row in shares:
            player = str(row.get("player", "玩家"))
            counts[player] = counts.get(player, 0) + 1
        if not counts:
            return f"{BY_CODE[game].name}目前還沒有排行榜資料。"
        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
        lines = [f"{BY_CODE[game].name}玩家分享排行（依分享次數）"]
        lines.extend(f"{index}. {player}：{count} 組" for index, (player, count) in enumerate(top, 1))
        return "\n".join(lines)

    def post(self, user_id: str, message: str, event_id: str = "") -> str:
        if not self.enabled:
            return "討論區準備中。"
        message = " ".join(message.split())[:180]
        if not message:
            return "請在「討論」後輸入內容。"
        with self._lock:
            data = self._data()
            if event_id and any(row.get("event_id") == event_id for row in data["posts"]):
                return "這則留言已收到，不會重複發布。"
            data["posts"].append({"player": anonymous_player(user_id), "message": message, "created_at": int(time.time()), "event_id": event_id})
            data["posts"] = data["posts"][-200:]
            self._save(data)
        return "已發到玩家討論區。輸入「討論區」可查看最新留言。"

    def recent_posts(self) -> str:
        if not self.enabled:
            return "討論區準備中。"
        with self._lock:
            posts = self._data()["posts"][-8:]
        if not posts:
            return "討論區目前還沒有留言。"
        return "玩家討論區\n" + "\n".join(f"• {row['player']}：{row['message']}" for row in reversed(posts))
