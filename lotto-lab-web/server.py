from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import random
import re
import socket
import ssl
import sqlite3
import threading
import time
import posixpath
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

try:
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover - optional production dependency
    WebPushException = None
    webpush = None

try:
    import psycopg
except Exception:  # pragma: no cover - optional when using local SQLite
    psycopg = None

_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def ipv4_getaddrinfo(*args, **kwargs):
    results = _ORIGINAL_GETADDRINFO(*args, **kwargs)
    ipv4_results = [info for info in results if info[0] == socket.AF_INET]
    return ipv4_results or results


socket.getaddrinfo = ipv4_getaddrinfo

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
BUNDLED_TAIWAN_HISTORY = PUBLIC / "taiwan_539_history.json"

TAIWAN_LAST_URL = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/LastNumber"
TAIWAN_DATASET_URL = "https://gaze.nta.gov.tw/dntmb/OpenData/csvDw?ntaCode=D423F"
PILIO_TAIWAN_URL = "https://www.pilio.idv.tw/lto539/list.asp?indexpage={page}&orderby=new"
CALIFORNIA_FANTASY5_URL = "https://sc888.net/index.php?s=%2FLotteryFan%2Findex"

USER_AGENT = "Mozilla/5.0 LottoLab/0.1"
CACHE_TTL_SECONDS = int(os.environ.get("LOTTO_CACHE_TTL_SECONDS", "300"))
LATEST_CACHE_TTL_SECONDS = int(os.environ.get("LOTTO_LATEST_CACHE_TTL_SECONDS", "30"))
BACKTEST_FALLBACK_LIMIT = 90
BACKTEST_MIN_HISTORY = 36
BACKTEST_DEFAULT_LIMIT = 24
BACKTEST_MIN_LIMIT = 7
BACKTEST_MAX_LIMIT = 365
MAX_JSON_BODY_BYTES = 64 * 1024
MAX_PUSH_SUBSCRIPTIONS = int(os.environ.get("LOTTO_MAX_PUSH_SUBSCRIPTIONS", "5000"))
MAX_SAVED_PICKS_PER_SUBSCRIPTION = 20
API_RATE_LIMITS = {
    "/api/lottery": (90, 60),
    "/api/history-search": (45, 60),
    "/api/config": (120, 60),
    "/api/push-subscription": (20, 60),
    "/api/notify-latest": (5, 600),
}
ALLOWED_GAMES = {"tw539", "ca-fantasy5"}
STRIPE_PAYMENT_LINK = os.environ.get("LOTTO_STRIPE_PAYMENT_LINK", "").strip()
STRIPE_FLAGSHIP_PAYMENT_LINK = os.environ.get("LOTTO_STRIPE_FLAGSHIP_PAYMENT_LINK", "").strip()
PUSH_PUBLIC_KEY = os.environ.get("LOTTO_VAPID_PUBLIC_KEY", "").strip()
PUSH_PRIVATE_KEY = os.environ.get("LOTTO_VAPID_PRIVATE_KEY", "").strip().replace("\\n", "\n")
PUSH_CONTACT_EMAIL = os.environ.get("LOTTO_PUSH_CONTACT_EMAIL", "admin@example.com").strip()
NOTIFY_SECRET = os.environ.get("LOTTO_NOTIFY_SECRET", "").strip()
SUBSCRIPTIONS_FILE = Path(os.environ.get("LOTTO_SUBSCRIPTIONS_FILE", ROOT / "data" / "push_subscriptions.json"))
NOTIFY_STATE_FILE = Path(os.environ.get("LOTTO_NOTIFY_STATE_FILE", ROOT / "data" / "notify_state.json"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
SQLITE_DATABASE_FILE = Path(os.environ.get("LOTTO_SQLITE_PATH", ROOT / "data" / "lotto.sqlite3"))
AUTO_NOTIFY_ENABLED = os.environ.get("LOTTO_AUTO_NOTIFY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
AUTO_NOTIFY_INTERVAL_SECONDS = int(os.environ.get("LOTTO_AUTO_NOTIFY_INTERVAL_SECONDS", "180"))
AUTO_NOTIFY_GAMES = [
    game.strip()
    for game in os.environ.get("LOTTO_AUTO_NOTIFY_GAMES", "tw539,ca-fantasy5").split(",")
    if game.strip() in ALLOWED_GAMES
]


@dataclass
class CacheItem:
    value: Any
    created_at: float


cache: dict[str, CacheItem] = {}
rate_limit_hits: dict[tuple[str, str], list[float]] = {}
notify_lock = threading.Lock()
database_ready = False
database_lock = threading.RLock()


def database_backend() -> str:
    return "postgres" if DATABASE_URL else "sqlite"


def database_connection():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL 已設定，但尚未安裝 psycopg")
        return psycopg.connect(DATABASE_URL, autocommit=True)
    SQLITE_DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SQLITE_DATABASE_FILE, timeout=20)
    connection.row_factory = sqlite3.Row
    return connection


def database_sql(sql: str) -> str:
    return sql.replace("?", "%s") if DATABASE_URL else sql


def database_execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    with database_lock:
        connection = database_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(database_sql(sql), params)
            if not DATABASE_URL:
                connection.commit()
        finally:
            connection.close()


def database_execute_many(sql: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    with database_lock:
        connection = database_connection()
        try:
            cursor = connection.cursor()
            cursor.executemany(database_sql(sql), rows)
            if not DATABASE_URL:
                connection.commit()
        finally:
            connection.close()


def database_query(sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with database_lock:
        connection = database_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(database_sql(sql), params)
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            connection.close()


def init_database() -> None:
    global database_ready
    database_execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id TEXT PRIMARY KEY,
            subscription_json TEXT NOT NULL,
            game TEXT NOT NULL,
            saved_picks_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """
    )
    database_execute(
        """
        CREATE TABLE IF NOT EXISTS notify_state (
            game TEXT PRIMARY KEY,
            draw_key TEXT NOT NULL
        )
        """
    )
    database_execute(
        """
        CREATE TABLE IF NOT EXISTS draw_history (
            game TEXT NOT NULL,
            period TEXT NOT NULL,
            date TEXT NOT NULL,
            name TEXT NOT NULL,
            numbers_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (game, period, date)
        )
        """
    )
    database_ready = True


def database_draw_key(draw: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(draw.get("game", "")).strip(),
        str(draw.get("period", "")).strip(),
        str(draw.get("date", "")).strip(),
    )


def persist_draw_history(draws: list[dict[str, Any]]) -> None:
    if not database_ready:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for draw in draws:
        game, period, date = database_draw_key(draw)
        numbers = draw.get("numbers", [])
        if game not in ALLOWED_GAMES or not period or not date or not isinstance(numbers, list) or len(numbers) != 5:
            continue
        rows.append(
            (
                game,
                period,
                date,
                str(draw.get("name", "")),
                json.dumps(numbers, ensure_ascii=False),
                now,
            )
        )
    database_execute_many(
        """
        INSERT INTO draw_history (game, period, date, name, numbers_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (game, period, date) DO UPDATE SET
            name = excluded.name,
            numbers_json = excluded.numbers_json,
            updated_at = excluded.updated_at
        """,
        rows,
    )


def load_database_history(game: str, limit: int = 5000) -> list[dict[str, Any]]:
    if not database_ready or game not in ALLOWED_GAMES:
        return []
    rows = database_query(
        """
        SELECT game, period, date, name, numbers_json
        FROM draw_history
        WHERE game = ?
        ORDER BY date DESC, period DESC
        LIMIT ?
        """,
        (game, max(1, min(limit, 10000))),
    )
    history = []
    for row in rows:
        try:
            numbers = json.loads(row[4])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(numbers, list) or len(numbers) != 5:
            continue
        history.append(
            {
                "game": row[0],
                "period": row[1],
                "date": row[2],
                "name": row[3],
                "numbers": normalize_numbers(numbers),
            }
        )
    return history


def merge_draw_history(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group in groups:
        for draw in group:
            key = database_draw_key(draw)
            if all(key):
                merged[key] = draw
    values = list(merged.values())
    values.sort(key=lambda item: (item.get("date", ""), str(item.get("period", ""))), reverse=True)
    return values


def clamp_int(value: str, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def clean_game(value: str) -> str:
    game = (value or "tw539").strip()
    if game not in ALLOWED_GAMES:
        raise ValueError("不支援的遊戲種類")
    return game


def validate_push_subscription(subscription: dict[str, Any]) -> None:
    endpoint = str(subscription.get("endpoint", ""))
    keys = subscription.get("keys", {})
    if not endpoint.startswith("https://"):
        raise ValueError("缺少有效的通知 endpoint")
    if not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("缺少有效的通知金鑰")


def sanitize_saved_picks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value[:MAX_SAVED_PICKS_PER_SUBSCRIPTION]:
        if not isinstance(item, dict):
            continue
        game = str(item.get("game", "")).strip()
        raw_numbers = item.get("numbers", [])
        if game not in ALLOWED_GAMES or not isinstance(raw_numbers, list) or len(raw_numbers) != 5:
            continue
        try:
            numbers = sorted({int(number) for number in raw_numbers})
        except (TypeError, ValueError):
            continue
        if len(numbers) != 5 or any(number < 1 or number > 39 for number in numbers):
            continue
        key = f"{game}:{','.join(str(number) for number in numbers)}"
        if key in seen:
            continue
        seen.add(key)
        clean.append({"game": game, "numbers": numbers})
    return clean


def load_push_subscriptions() -> list[dict[str, Any]]:
    try:
        if database_ready:
            rows = database_query(
                "SELECT id, subscription_json, game, saved_picks_json, updated_at FROM push_subscriptions"
            )
            if rows:
                subscriptions = []
                for row in rows:
                    try:
                        subscription = json.loads(row[1])
                        saved_picks = json.loads(row[3])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    subscriptions.append(
                        {
                            "id": row[0],
                            "subscription": subscription,
                            "game": row[2],
                            "savedPicks": sanitize_saved_picks(saved_picks),
                            "updatedAt": row[4],
                        }
                    )
                return subscriptions
        if not SUBSCRIPTIONS_FILE.exists():
            return []
        payload = json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
        subscriptions = payload if isinstance(payload, list) else []
        if database_ready and subscriptions:
            save_push_subscriptions(subscriptions)
        return subscriptions
    except Exception:
        return []


def save_push_subscriptions(subscriptions: list[dict[str, Any]]) -> None:
    if database_ready:
        database_execute("DELETE FROM push_subscriptions")
        for item in subscriptions:
            subscription = item.get("subscription", {})
            if not isinstance(subscription, dict) or not subscription.get("endpoint"):
                continue
            database_execute(
                """
                INSERT INTO push_subscriptions
                    (id, subscription_json, game, saved_picks_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(item.get("id", subscription_id(subscription))),
                    json.dumps(subscription, ensure_ascii=False),
                    str(item.get("game", "all")),
                    json.dumps(sanitize_saved_picks(item.get("savedPicks", [])), ensure_ascii=False),
                    str(item.get("updatedAt", datetime.now(timezone.utc).isoformat(timespec="seconds"))),
                ),
            )
        return
    SUBSCRIPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUBSCRIPTIONS_FILE.write_text(json.dumps(subscriptions, ensure_ascii=False, indent=2), encoding="utf-8")


def subscription_id(subscription: dict[str, Any]) -> str:
    endpoint = str(subscription.get("endpoint", ""))
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def upsert_push_subscription(subscription: dict[str, Any], game: str = "all", saved_picks: Any = None) -> int:
    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        raise ValueError("缺少有效的通知訂閱資料")
    validate_push_subscription(subscription)
    subscriptions = load_push_subscriptions()
    if len(subscriptions) >= MAX_PUSH_SUBSCRIPTIONS and subscription_id(subscription) not in {item.get("id") for item in subscriptions}:
        raise ValueError("通知訂閱數已達上限")
    item_id = subscription_id(subscription)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {
        "id": item_id,
        "subscription": subscription,
        "game": game if game in ALLOWED_GAMES else "all",
        "savedPicks": sanitize_saved_picks(saved_picks),
        "updatedAt": now,
    }
    kept = [item for item in subscriptions if item.get("id") != item_id]
    kept.append(record)
    save_push_subscriptions(kept)
    return len(kept)


def remove_push_subscription(subscription: dict[str, Any]) -> int:
    item_id = subscription_id(subscription)
    subscriptions = [item for item in load_push_subscriptions() if item.get("id") != item_id]
    save_push_subscriptions(subscriptions)
    return len(subscriptions)


def push_server_ready() -> bool:
    return bool(PUSH_PUBLIC_KEY and PUSH_PRIVATE_KEY and webpush)


def send_push_message(subscription: dict[str, Any], payload: dict[str, Any]) -> None:
    if not push_server_ready():
        raise RuntimeError("尚未設定完整推播金鑰，無法由伺服器群發通知")
    subject = f"mailto:{PUSH_CONTACT_EMAIL}" if "@" in PUSH_CONTACT_EMAIL else PUSH_CONTACT_EMAIL
    webpush(
        subscription_info=subscription,
        data=json.dumps(payload, ensure_ascii=False),
        vapid_private_key=PUSH_PRIVATE_KEY,
        vapid_claims={"sub": subject},
    )


def load_notify_state() -> dict[str, Any]:
    try:
        if database_ready:
            rows = database_query("SELECT game, draw_key FROM notify_state")
            if rows:
                return {str(row[0]): str(row[1]) for row in rows}
        if not NOTIFY_STATE_FILE.exists():
            return {}
        payload = json.loads(NOTIFY_STATE_FILE.read_text(encoding="utf-8"))
        state = payload if isinstance(payload, dict) else {}
        if database_ready and state:
            save_notify_state(state)
        return state
    except Exception:
        return {}


def save_notify_state(state: dict[str, Any]) -> None:
    if database_ready:
        database_execute("DELETE FROM notify_state")
        for game, draw_key in state.items():
            if game in ALLOWED_GAMES and draw_key:
                database_execute(
                    "INSERT INTO notify_state (game, draw_key) VALUES (?, ?)",
                    (game, str(draw_key)),
                )
        return
    NOTIFY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def already_notified(game: str, draw: dict[str, Any]) -> bool:
    state = load_notify_state()
    key = f"{draw.get('period', '')}|{draw.get('date', '')}|{'.'.join(str(number) for number in draw.get('numbers', []))}"
    return bool(key and state.get(game) == key)


def mark_notified(game: str, draw: dict[str, Any]) -> None:
    state = load_notify_state()
    state[game] = f"{draw.get('period', '')}|{draw.get('date', '')}|{'.'.join(str(number) for number in draw.get('numbers', []))}"
    save_notify_state(state)


def star_hit_message(hit_numbers: list[int]) -> str:
    numbers = "、".join(f"{number:02d}" for number in hit_numbers)
    count = len(hit_numbers)
    if count == 1:
        return f"恭喜（{numbers}）摘下一星"
    if count == 2:
        return f"恭喜（{numbers}）摘下二星"
    if count == 3:
        return f"恭喜（{numbers}）太神了！摘下三星"
    if count == 4:
        return f"恭喜（{numbers}）你超神了！摘下四星"
    if count == 5:
        return f"恭喜（{numbers}）你已成為最強狙擊手！五顆通通拿下"
    return f"本期命中 {count} 顆：{numbers}"


def latest_notification_message(game: str, lottery: dict[str, Any], saved_picks: Any = None) -> dict[str, Any]:
    winning_numbers = lottery.get("numbers", [])
    numbers = "、".join(f"{number:02d}" for number in winning_numbers)
    watched_picks = [pick for pick in sanitize_saved_picks(saved_picks) if pick.get("game") == game]
    outcomes = []
    for pick in watched_picks:
        hit_numbers = [number for number in pick["numbers"] if number in winning_numbers]
        if hit_numbers:
            outcomes.append(star_hit_message(hit_numbers))
    if outcomes:
        title = f"{lottery.get('name', '摘星狙擊手')} 命中通知"
        body = " ｜ ".join(outcomes[:3])
        if len(outcomes) > 3:
            body += f"；另有 {len(outcomes) - 3} 組號碼命中。"
    elif watched_picks:
        title = f"{lottery.get('name', '摘星狙擊手')} 對獎結果"
        body = f"第 {lottery.get('period', '-')} 期已開獎：{numbers}；你儲存的 {len(watched_picks)} 組號碼本期未命中。"
    else:
        title = f"{lottery.get('name', '摘星狙擊手')} 已開獎"
        body = f"第 {lottery.get('period', '-')} 期：{numbers}"
    return {
        "title": title,
        "body": body,
        "url": f"/?game={game}",
        "tag": f"lotto-lab-{game}-{lottery.get('period', lottery.get('date', 'latest'))}",
    }


def broadcast_push_message(message: dict[str, Any] | None, message_factory=None) -> tuple[int, int, int]:
    subscriptions = load_push_subscriptions()
    sent = 0
    failed = 0
    alive = []
    for item in subscriptions:
        subscription = item.get("subscription", {})
        try:
            payload = message_factory(item) if message_factory else message
            send_push_message(subscription, payload or {})
            sent += 1
            alive.append(item)
        except Exception as exc:
            failed += 1
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code not in (404, 410):
                alive.append(item)
    if len(alive) != len(subscriptions):
        save_push_subscriptions(alive)
    return sent, failed, len(alive)


def notify_latest_game(game: str) -> dict[str, Any]:
    if not push_server_ready():
        return {"ok": False, "game": game, "error": "尚未設定完整推播金鑰"}
    if not load_push_subscriptions():
        return {"ok": True, "game": game, "sent": 0, "failed": 0, "subscriberCount": 0, "skipped": True, "message": "目前沒有訂閱用戶"}
    lottery = build_payload(game, 90)["latest"]
    if already_notified(game, lottery):
        return {"ok": True, "game": game, "sent": 0, "failed": 0, "subscriberCount": len(load_push_subscriptions()), "skipped": True, "message": "這一期已通知過"}
    message = latest_notification_message(game, lottery)
    sent, failed, alive = broadcast_push_message(
        message,
        lambda item: latest_notification_message(game, lottery, item.get("savedPicks", [])),
    )
    if sent > 0:
        mark_notified(game, lottery)
    return {"ok": True, "game": game, "sent": sent, "failed": failed, "subscriberCount": alive, "message": message}


def auto_notify_loop() -> None:
    time.sleep(20)
    while True:
        try:
            if push_server_ready() and AUTO_NOTIFY_GAMES:
                with notify_lock:
                    for game in AUTO_NOTIFY_GAMES:
                        result = notify_latest_game(game)
                        if result.get("sent") or result.get("failed"):
                            print(
                                "auto notify",
                                game,
                                "sent",
                                result.get("sent", 0),
                                "failed",
                                result.get("failed", 0),
                                "subscribers",
                                result.get("subscriberCount", 0),
                            )
        except Exception as exc:
            print(f"auto notify error: {exc}")
        time.sleep(max(60, AUTO_NOTIFY_INTERVAL_SECONDS))


def cached(key: str, loader, ttl_seconds: int | None = None):
    hit = cache.get(key)
    ttl = CACHE_TTL_SECONDS if ttl_seconds is None else max(1, ttl_seconds)
    if hit and time.time() - hit.created_at < ttl:
        return hit.value
    value = loader()
    cache[key] = CacheItem(value=value, created_at=time.time())
    return value


def cache_key_for_draws(prefix: str, game: str, limit: int, draws: list[dict[str, Any]]) -> str:
    latest = draws[0] if draws else {}
    return f"{prefix}-{game}-{limit}-{latest.get('date', '')}-{latest.get('period', '')}"


def fetch_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with open_url(req, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_bytes(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with open_url(req, timeout=timeout) as response:
        return response.read()


def open_url(req: urllib.request.Request, timeout: int):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError):
            context = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=context)
        raise


def normalize_numbers(nums: list[int]) -> list[int]:
    return sorted(int(n) for n in nums)


def same_draw(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left.get("date") == right.get("date") and normalize_numbers(left.get("numbers", [])) == normalize_numbers(right.get("numbers", []))


def parse_date(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return value


def parse_pilio_date(value: str) -> str:
    clean = re.sub(r"<[^>]+>", " ", value)
    clean = html.unescape(clean)
    match = re.search(r"(\d{1,2})/(\d{1,2})\s+(\d{2,4})", clean)
    if not match:
        return ""
    month, day, year = match.groups()
    year_number = int(year)
    if year_number < 100:
        year_number += 2000
    return f"{year_number:04d}-{int(month):02d}-{int(day):02d}"


def pilio_taiwan_history(limit: int = 90, ttl_seconds: int | None = None) -> list[dict[str, Any]]:
    def load():
        draws = []
        page_count = max(1, min(8, (limit + 22) // 23))
        for page in range(1, page_count + 1):
            url = PILIO_TAIWAN_URL.format(page=page)
            text = fetch_text(url, timeout=15)
            rows = re.findall(
                r'<td class="date-cell">\s*(.*?)\s*</td>\s*<td class="number-cell">\s*(.*?)\s*</td>',
                text,
                re.S,
            )
            for date_html, number_html in rows:
                numbers = [int(n) for n in re.findall(r"\d{1,2}", html.unescape(number_html))]
                if len(numbers) < 5:
                    continue
                date = parse_pilio_date(date_html)
                if not date:
                    continue
                draws.append(
                    {
                        "game": "tw539",
                        "name": "今彩 539",
                        "period": date.replace("-", ""),
                        "date": date,
                        "numbers": normalize_numbers(numbers[:5]),
                        "source": "樂透彩幸運發財網備援資料",
                        "sourceUrl": url,
                    }
                )
        draws.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
        return draws[:limit]

    return cached(f"pilio-taiwan-history-{limit}", load, ttl_seconds)


def taiwan_latest() -> dict[str, Any]:
    def load():
        candidates: list[dict[str, Any]] = []
        try:
            payload = json.loads(fetch_text(TAIWAN_LAST_URL, timeout=10))
            entries = payload.get("content", {}).get("lastNumberList", [])
            daily_cash = next((item for item in entries if item.get("gameCode") == 5120), None)
            if not daily_cash:
                raise RuntimeError("台灣彩券 API 目前沒有回傳今彩 539 最新資料")
            candidates.append(
                {
                    "game": "tw539",
                    "name": "今彩 539",
                    "period": daily_cash.get("period", ""),
                    "date": parse_date(daily_cash.get("drawDate", "")),
                    "numbers": normalize_numbers(daily_cash.get("lotNumber", [])),
                    "source": "台灣彩券 LastNumber API",
                    "sourceUrl": TAIWAN_LAST_URL,
                }
            )
        except Exception:
            pass

        # The official endpoint can lag after the evening draw. Check the
        # lightweight history source and use whichever candidate has the newer date.
        try:
            candidates.extend(pilio_taiwan_history(1, ttl_seconds=LATEST_CACHE_TTL_SECONDS))
        except Exception:
            pass

        candidates = [item for item in candidates if item.get("date") and len(item.get("numbers", [])) >= 5]
        if candidates:
            return max(candidates, key=lambda item: (item.get("date", ""), str(item.get("period", ""))))
        raise RuntimeError("目前沒有回傳今彩 539 最新資料")

    return cached("taiwan-latest", load, LATEST_CACHE_TTL_SECONDS)


def taiwan_dataset_rows() -> list[dict[str, str]]:
    def load():
        dataset = fetch_text(TAIWAN_DATASET_URL)
        return list(csv.DictReader(io.StringIO(dataset)))

    return cached("taiwan-dataset-rows", load)


def parse_taiwan_zip(zip_url: str) -> list[dict[str, Any]]:
    data = fetch_bytes(zip_url)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [name for name in zf.namelist() if not name.endswith("/")]
        name = next((name for name in names if "今彩539" in name or "539" in name), "")
        if not name:
            raise ValueError(f"找不到今彩539年度資料檔：{zip_url}")
        raw = zf.read(name)
        for encoding in ("utf-8-sig", "cp950", "big5"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(io.StringIO(text))
    parsed = []
    for row in reader:
        numbers = []
        for key, value in row.items():
            if key and re.search(r"(獎號|獎號[1-5]|球號|號碼)", key) and value:
                found = re.findall(r"\d+", value)
                numbers.extend(int(n) for n in found)
        if len(numbers) < 5:
            numbers = [int(n) for n in re.findall(r"\b\d{1,2}\b", ",".join(row.values()))[-5:]]
        if len(numbers) >= 5:
            values = list(row.values())
            date_value = next((v for v in values if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", v)), "")
            period = next((v for v in values if re.fullmatch(r"\d{6,}", v.strip())), "")
            parsed.append(
                {
                    "game": "tw539",
                    "name": "今彩 539",
                    "period": period,
                    "date": parse_date(date_value),
                    "numbers": normalize_numbers(numbers[:5]),
                    "source": "政府資料開放平臺年度 zip",
                    "sourceUrl": zip_url,
                }
            )
    parsed.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    return parsed


def taiwan_year_history(year: int) -> list[dict[str, Any]]:
    def load():
        rows = taiwan_dataset_rows()
        row = next((item for item in rows if int(item.get("資料所屬年度", "0") or "0") + 1911 == year), None)
        if not row:
            row = next((item for item in rows if item.get("下載連結", "").endswith(f"/{year}.zip")), None)
        if not row:
            return []
        return parse_taiwan_zip(row["下載連結"])

    return cached(f"taiwan-year-history-{year}", load)


def bundled_taiwan_history() -> list[dict[str, Any]]:
    def load():
        if not BUNDLED_TAIWAN_HISTORY.exists():
            return []
        with BUNDLED_TAIWAN_HISTORY.open("r", encoding="utf-8") as file:
            rows = json.load(file)
        rows.sort(key=lambda item: (item.get("date", ""), item.get("period", "")), reverse=True)
        return rows

    return cached("bundled-taiwan-539-history", load)


def taiwan_history(limit: int = 180) -> list[dict[str, Any]]:
    fast_history = pilio_taiwan_history(limit)
    if len(fast_history) >= limit:
        return fast_history[:limit]
    bundled = bundled_taiwan_history()
    stored = load_database_history("tw539", max(limit, 5000))
    if bundled or stored:
        combined = merge_draw_history(bundled, stored)
        try:
            latest = taiwan_latest()
        except Exception:
            latest = None
        if latest:
            combined = merge_draw_history([latest], combined)
        return combined[:limit]
    try:
        rows = taiwan_dataset_rows()
        latest_row = max(rows, key=lambda row: int(row.get("資料所屬年度", "0") or "0"))
        latest_year = int(latest_row.get("資料所屬年度", "0") or "0") + 1911
        return taiwan_year_history(latest_year)[:limit]
    except Exception:
        return stored or pilio_taiwan_history(limit)


def search_taiwan_history(from_year: int, to_year: int, keyword: str = "", number: int | None = None, limit: int = 2000) -> dict[str, Any]:
    bundled = bundled_taiwan_history()
    stored = load_database_history("tw539", 10000)
    combined_history = merge_draw_history(bundled, stored)
    if combined_history:
        available_years = sorted({int(draw["date"][:4]) for draw in combined_history if draw.get("date")})
    else:
        rows = taiwan_dataset_rows()
        available_years = sorted(int(row.get("資料所屬年度", "0") or "0") + 1911 for row in rows)
    if not available_years:
        return {"history": [], "availableYears": [], "searchedYears": []}
    start = max(min(from_year, to_year), available_years[0])
    end = min(max(from_year, to_year), available_years[-1])
    searched_years = list(range(start, end + 1))
    if combined_history:
        draws = [draw for draw in combined_history if draw.get("date") and start <= int(draw["date"][:4]) <= end]
        try:
            latest = taiwan_latest()
            latest_year = int(latest["date"][:4]) if latest.get("date") else None
            if latest_year in searched_years and all(draw.get("period") != latest.get("period") for draw in draws):
                draws.append(latest)
        except Exception:
            pass
    else:
        draws = []
        for year in searched_years:
            draws.extend(taiwan_year_history(year))
        latest = taiwan_latest()
        latest_year = int(latest["date"][:4]) if latest.get("date") else None
        if latest_year in searched_years and all(draw.get("period") != latest.get("period") for draw in draws):
            draws.append(latest)
    query = keyword.strip().lower()
    if query or number:
        draws = filter_history_rows(draws, query, number)
    draws.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    persist_draw_history(draws)
    return {
        "history": public_draws(draws[:limit]),
        "total": len(draws),
        "availableYears": available_years,
        "searchedYears": searched_years,
        "limited": len(draws) > limit,
    }


def filter_history_rows(draws: list[dict[str, Any]], query: str = "", number: int | None = None) -> list[dict[str, Any]]:
    query = query.strip().lower()
    return [
        draw
        for draw in draws
        if (not query or query in f"{draw.get('date', '')} {draw.get('period', '')} {' '.join(str(n).zfill(2) for n in draw.get('numbers', []))}".lower())
        and (not number or number in draw.get("numbers", []))
    ]


def california_history(limit: int = 180) -> list[dict[str, Any]]:
    def load():
        html = fetch_text(CALIFORNIA_FANTASY5_URL)
        text = re.sub(r"<[^>]+>", "\n", html)
        text = re.sub(r"&nbsp;?", " ", text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        parsed = []
        for i, line in enumerate(lines):
            period_match = re.match(r"第\s*(\d+)\s*期", line)
            if not period_match:
                continue
            window = lines[i : i + 24]
            date = next((m.group(1) for part in window for m in [re.search(r"(20\d{2}-\d{2}-\d{2})", part)] if m), "")
            nums = []
            for part in window:
                if re.fullmatch(r"\d{1,2}", part):
                    value = int(part)
                    if 1 <= value <= 39:
                        nums.append(value)
                if len(nums) == 5:
                    break
            if date and len(nums) == 5:
                parsed.append(
                    {
                        "game": "ca-fantasy5",
                        "name": "加州天天樂 Fantasy 5",
                        "period": period_match.group(1),
                        "date": date,
                        "numbers": normalize_numbers(nums),
                        "source": "速彩加州天天樂頁面",
                        "sourceUrl": CALIFORNIA_FANTASY5_URL,
                    }
                )
        dedup = {item["period"]: item for item in parsed}
        values = list(dedup.values())
        values.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
        persist_draw_history(values)
        return merge_draw_history(values, load_database_history("ca-fantasy5", 5000))

    try:
        return cached("california-history", load, LATEST_CACHE_TTL_SECONDS)[:limit]
    except Exception:
        return load_database_history("ca-fantasy5", limit)


def search_california_history(from_year: int, to_year: int, keyword: str = "", number: int | None = None, limit: int = 2000) -> dict[str, Any]:
    draws = california_history(5000)
    available_years = sorted({int(draw["date"][:4]) for draw in draws if draw.get("date")})
    if not available_years:
        return {"history": [], "total": 0, "availableYears": [], "searchedYears": [], "limited": False}
    start = max(min(from_year, to_year), available_years[0])
    end = min(max(from_year, to_year), available_years[-1])
    searched_years = list(range(start, end + 1))
    rows = [draw for draw in draws if draw.get("date") and start <= int(draw["date"][:4]) <= end]
    rows = filter_history_rows(rows, keyword, number)
    rows.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    persist_draw_history(rows)
    return {
        "history": public_draws(rows[:limit]),
        "total": len(rows),
        "availableYears": available_years,
        "searchedYears": searched_years,
        "limited": len(rows) > limit,
    }



def number_stats(draws: list[dict[str, Any]], max_number: int = 39) -> dict[str, Any]:
    frequency = {n: 0 for n in range(1, max_number + 1)}
    last_seen = {n: None for n in range(1, max_number + 1)}
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    for index, draw in enumerate(ordered):
        for number in draw["numbers"]:
            frequency[number] += 1
            if last_seen[number] is None:
                last_seen[number] = index
    gaps = {n: (last_seen[n] if last_seen[n] is not None else len(ordered)) for n in frequency}
    recent_window = ordered[: min(18, len(ordered))]
    recent_frequency = {n: 0 for n in range(1, max_number + 1)}
    for draw in recent_window:
        for number in draw["numbers"]:
            recent_frequency[number] += 1
    window_frequencies = {}
    for window_size in (6, 12, 18, 24, 36, 60, 90):
        window_rows = ordered[:window_size]
        window_counts = {n: 0 for n in range(1, max_number + 1)}
        for draw in window_rows:
            for number in draw["numbers"]:
                window_counts[number] += 1
        window_frequencies[str(window_size)] = window_counts
    appearance_streak = {n: 0 for n in range(1, max_number + 1)}
    for number in appearance_streak:
        for draw in ordered:
            if number not in draw["numbers"]:
                break
            appearance_streak[number] += 1
    return {
        "ordered": ordered,
        "frequency": frequency,
        "recentFrequency": recent_frequency,
        "windowFrequencies": window_frequencies,
        "appearanceStreak": appearance_streak,
        "gaps": gaps,
    }


MODEL_PROFILES = {
    "classic": {
        "label": "熱遺平衡",
        "number": {"heat": 0.45, "recent": 0.18, "trend": 0.0, "gap": 0.27, "neighbor": 0.0, "tail": 0.0, "pair": 0.0, "drag": 0.0, "repeatSignal": 0.0, "interval": 0.0},
        "combo": {"spread": 1.0, "zone": 0.0, "odd": 0.0, "low": 0.0, "sum": 0.0, "tail": 0.0, "repeat": 0.0, "interval": 0.0},
    },
    "balanced": {
        "label": "綜合版路",
        "number": {"heat": 0.14, "recent": 0.16, "trend": 0.10, "gap": 0.12, "neighbor": 0.06, "tail": 0.05, "pair": 0.05, "drag": 0.06, "repeatSignal": 0.04, "interval": 0.06, "multiWindow": 0.10, "tailMomentum": 0.04, "streak": 0.02},
        "combo": {"spread": 0.16, "zone": 0.17, "odd": 0.13, "low": 0.09, "sum": 0.16, "tail": 0.08, "repeat": 0.11, "interval": 0.10},
    },
    "momentum": {
        "label": "近期動能",
        "number": {"heat": 0.10, "recent": 0.23, "trend": 0.15, "gap": 0.04, "neighbor": 0.06, "tail": 0.04, "pair": 0.05, "drag": 0.07, "repeatSignal": 0.05, "interval": 0.07, "multiWindow": 0.09, "tailMomentum": 0.08, "streak": 0.05},
        "combo": {"spread": 0.13, "zone": 0.15, "odd": 0.11, "low": 0.09, "sum": 0.14, "tail": 0.08, "repeat": 0.20, "interval": 0.10},
    },
    "cycle": {
        "label": "遺漏週期",
        "number": {"heat": 0.12, "recent": 0.08, "trend": 0.05, "gap": 0.24, "neighbor": 0.07, "tail": 0.05, "pair": 0.07, "drag": 0.07, "repeatSignal": 0.05, "interval": 0.07, "multiWindow": 0.09, "tailMomentum": 0.02, "streak": 0.02},
        "combo": {"spread": 0.18, "zone": 0.16, "odd": 0.11, "low": 0.11, "sum": 0.16, "tail": 0.09, "repeat": 0.09, "interval": 0.10},
    },
    "shape": {
        "label": "區間尾數",
        "number": {"heat": 0.10, "recent": 0.10, "trend": 0.06, "gap": 0.08, "neighbor": 0.05, "tail": 0.13, "pair": 0.13, "drag": 0.05, "repeatSignal": 0.02, "interval": 0.13, "multiWindow": 0.08, "tailMomentum": 0.05, "streak": 0.02},
        "combo": {"spread": 0.15, "zone": 0.22, "odd": 0.14, "low": 0.10, "sum": 0.12, "tail": 0.09, "repeat": 0.04, "interval": 0.14},
    },
    "adaptive": {
        "label": "自適應集成",
        "number": {"heat": 0.12, "recent": 0.14, "trend": 0.10, "gap": 0.10, "neighbor": 0.06, "tail": 0.05, "pair": 0.08, "drag": 0.07, "repeatSignal": 0.05, "interval": 0.08, "multiWindow": 0.10, "tailMomentum": 0.08, "streak": 0.05},
        "combo": {"spread": 0.13, "zone": 0.14, "odd": 0.10, "low": 0.07, "sum": 0.13, "tail": 0.07, "repeat": 0.11, "interval": 0.15, "shape": 0.10},
    },
}

SHORT_TERM_WINDOWS = (10, 20, 36)
RESEARCH_FEATURE_KEYS = (
    "heat",
    "recent",
    "trend",
    "gap",
    "neighbor",
    "tail",
    "pair",
    "drag",
    "repeatSignal",
    "interval",
    "multiWindow",
    "tailMomentum",
    "streak",
    "momentum",
)
RESEARCH_FEATURE_LABELS = {
    "heat": "長期熱度",
    "recent": "近期熱度",
    "trend": "趨勢動能",
    "gap": "遺漏週期",
    "neighbor": "鄰近號",
    "tail": "尾數熱度",
    "pair": "哥倆好",
    "drag": "拖牌",
    "repeatSignal": "連莊",
    "interval": "區間",
    "multiWindow": "多窗口",
    "tailMomentum": "尾數動能",
    "streak": "連續開出",
    "momentum": "近期差值",
}


def safe_divide(value: float, total: float) -> float:
    return value / total if total else 0


def closeness(value: float, target: float, width: float) -> float:
    if width <= 0:
        return 1.0 if value == target else 0.0
    return max(0.0, 1.0 - abs(value - target) / width)


def zone_signature(numbers: list[int]) -> tuple[int, int, int, int]:
    zones = [0, 0, 0, 0]
    for number in numbers:
        zones[min(3, (number - 1) // 10)] += 1
    return tuple(zones)


def interval_windows(max_number: int) -> list[tuple[int, int]]:
    windows = [(1, 15), (10, 20), (15, 25), (20, 30), (25, 35), (30, max_number)]
    return [(start, min(end, max_number)) for start, end in windows if start <= max_number]


def signature_score(value: Any, counts: dict[Any, int]) -> float:
    if not counts:
        return 0.0
    return safe_divide(counts.get(value, 0), max(counts.values()) or 1)


def pattern_profile(draws: list[dict[str, Any]], max_number: int = 39) -> dict[str, Any]:
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    recent6 = ordered[:6]
    recent12 = ordered[:12]
    recent24 = ordered[:24]
    recent30 = ordered[:30]
    recent36 = ordered[:36]
    older30 = ordered[30:60]
    recent60 = ordered[:60]
    recent90 = ordered[:90]

    def frequencies(rows: list[dict[str, Any]]) -> dict[int, int]:
        values = {n: 0 for n in range(1, max_number + 1)}
        for draw in rows:
            for number in draw["numbers"]:
                values[number] += 1
        return values

    recent6_freq = frequencies(recent6)
    recent12_freq = frequencies(recent12)
    recent24_freq = frequencies(recent24)
    recent30_freq = frequencies(recent30)
    recent36_freq = frequencies(recent36)
    older30_freq = frequencies(older30)
    all_freq = frequencies(ordered)
    stats = number_stats(ordered, max_number)
    gaps = stats["gaps"]
    appearance_streak = stats["appearanceStreak"]

    window_specs = ((6, 0.30), (12, 0.25), (24, 0.20), (36, 0.14), (60, 0.07), (90, 0.04))
    window_rows = {size: ordered[:size] for size, _ in window_specs}
    window_frequencies = {size: frequencies(rows) for size, rows in window_rows.items()}
    multi_window_raw = {
        n: sum(
            safe_divide(window_frequencies[size][n], len(window_rows[size])) * weight
            for size, weight in window_specs
            if window_rows[size]
        )
        for n in range(1, max_number + 1)
    }
    max_multi_window = max(multi_window_raw.values()) or 1

    max_all = max(all_freq.values()) or 1
    max_recent12 = max(recent12_freq.values()) or 1
    max_trend = max((max(0, recent30_freq[n] - older30_freq[n]) for n in range(1, max_number + 1)), default=1) or 1
    max_gap = max(gaps.values()) or 1

    tails = {n: 0 for n in range(10)}
    for draw in recent30:
        for number in draw["numbers"]:
            tails[number % 10] += 1
    max_tail = max(tails.values()) or 1
    tail_recent12 = {n: 0 for n in range(10)}
    tail_prior24 = {n: 0 for n in range(10)}
    for draw in recent12:
        for number in draw["numbers"]:
            tail_recent12[number % 10] += 1
    for draw in ordered[12:36]:
        for number in draw["numbers"]:
            tail_prior24[number % 10] += 1
    tail_momentum_raw = {
        tail: max(
            0.0,
            safe_divide(tail_recent12[tail], len(recent12)) - safe_divide(tail_prior24[tail], len(ordered[12:36])),
        )
        for tail in range(10)
    }
    max_tail_momentum = max(tail_momentum_raw.values()) or 1

    pair_counts: dict[tuple[int, int], int] = {}
    for draw in recent60:
        nums = sorted(draw["numbers"])
        for left_index, left in enumerate(nums):
            for right in nums[left_index + 1 :]:
                pair_counts[(left, right)] = pair_counts.get((left, right), 0) + 1
    pair_number_score = {n: 0 for n in range(1, max_number + 1)}
    for (left, right), count in pair_counts.items():
        pair_number_score[left] += count
        pair_number_score[right] += count
    max_pair_number = max(pair_number_score.values()) or 1

    latest_numbers = set(ordered[0]["numbers"]) if ordered else set()
    neighbor_numbers = set()
    for number in latest_numbers:
        for nearby in (number - 1, number + 1):
            if 1 <= nearby <= max_number:
                neighbor_numbers.add(nearby)

    transitions = []
    for newer, older in zip(ordered, ordered[1:]):
        transitions.append(len(set(newer["numbers"]) & set(older["numbers"])))
    repeat_target = sum(transitions[:30]) / min(30, len(transitions)) if transitions else 0.65

    drag_counts: dict[tuple[int, int], int] = {}
    drag_source_totals = {n: 0 for n in range(1, max_number + 1)}
    drag_number_score = {n: 0 for n in range(1, max_number + 1)}
    repeat_counts = {n: 0 for n in range(1, max_number + 1)}
    repeat_source_totals = {n: 0 for n in range(1, max_number + 1)}
    for newer, older in zip(ordered[:80], ordered[1:81]):
        newer_numbers = set(newer["numbers"])
        older_numbers = set(older["numbers"])
        for source in older_numbers:
            drag_source_totals[source] += 1
            repeat_source_totals[source] += 1
            if source in newer_numbers:
                repeat_counts[source] += 1
            for target in newer_numbers:
                if target == source:
                    continue
                drag_counts[(source, target)] = drag_counts.get((source, target), 0) + 1
                if source in latest_numbers:
                    drag_number_score[target] += 1

    max_drag_number = max(drag_number_score.values()) or 1
    max_repeat_number = max(repeat_counts.values()) or 1

    intervals = interval_windows(max_number)
    interval_hit_counts = {window: 0 for window in intervals}
    interval_focus_counts = {window: 0 for window in intervals}
    interval_number_score = {n: 0 for n in range(1, max_number + 1)}
    for draw in recent60:
        numbers = draw["numbers"]
        recency_weight = 1.35 if draw in recent12 else 1.0
        for window in intervals:
            start, end = window
            hits = sum(1 for number in numbers if start <= number <= end)
            interval_hit_counts[window] += hits
            if hits >= 3:
                interval_focus_counts[window] += 1
                for number in range(start, end + 1):
                    interval_number_score[number] += recency_weight * hits
            elif hits == 2:
                for number in range(start, end + 1):
                    interval_number_score[number] += recency_weight * 0.45
    max_interval_number = max(interval_number_score.values()) or 1

    zone_counts: dict[tuple[int, int, int, int], int] = {}
    shape_counts: dict[tuple[tuple[int, int, int, int], int, int], int] = {}
    odd_counts: dict[int, int] = {}
    low_counts: dict[int, int] = {}
    sum_values = []
    for draw in recent60:
        numbers = draw["numbers"]
        zone = zone_signature(numbers)
        zone_counts[zone] = zone_counts.get(zone, 0) + 1
        odd = sum(1 for n in numbers if n % 2)
        odd_counts[odd] = odd_counts.get(odd, 0) + 1
        low = sum(1 for n in numbers if n <= max_number // 2)
        low_counts[low] = low_counts.get(low, 0) + 1
        shape = (zone, odd, low)
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
        sum_values.append(sum(numbers))
    sorted_sums = sorted(sum_values)
    center_sum = sorted_sums[len(sorted_sums) // 2] if sorted_sums else (max_number + 1) * 2.5
    low_sum = sorted_sums[max(0, int(len(sorted_sums) * 0.2) - 1)] if sorted_sums else center_sum - 24
    high_sum = sorted_sums[min(len(sorted_sums) - 1, int(len(sorted_sums) * 0.8))] if sorted_sums else center_sum + 24
    sum_width = max(18, (high_sum - low_sum) / 2)
    max_streak = max(appearance_streak.values()) or 1

    number_scores = {}
    for n in range(1, max_number + 1):
        trend = max(0, recent30_freq[n] - older30_freq[n])
        short_rate = safe_divide(recent6_freq[n], len(recent6))
        mid_rate = safe_divide(recent36_freq[n], len(recent36))
        long_rate = safe_divide(all_freq[n], len(ordered))
        momentum = max(0.0, short_rate - long_rate) * 0.65 + max(0.0, mid_rate - long_rate) * 0.35
        number_scores[n] = {
            "heat": safe_divide(all_freq[n], max_all),
            "recent": safe_divide(recent12_freq[n], max_recent12),
            "trend": safe_divide(trend, max_trend),
            "gap": safe_divide(gaps[n], max_gap),
            "neighbor": 1.0 if n in neighbor_numbers else (0.45 if n in latest_numbers else 0.0),
            "tail": safe_divide(tails[n % 10], max_tail),
            "pair": safe_divide(pair_number_score[n], max_pair_number),
            "drag": safe_divide(drag_number_score[n], max_drag_number),
            "repeatSignal": safe_divide(repeat_counts[n], max_repeat_number) if n in latest_numbers else 0.0,
            "interval": safe_divide(interval_number_score[n], max_interval_number),
            "multiWindow": safe_divide(multi_window_raw[n], max_multi_window),
            "tailMomentum": safe_divide(tail_momentum_raw[n % 10], max_tail_momentum),
            "streak": safe_divide(min(appearance_streak[n], 3), min(3, max_streak)),
            "momentum": momentum,
        }

    return {
        "ordered": ordered,
        "numberScores": number_scores,
        "pairCounts": pair_counts,
        "zoneCounts": zone_counts,
        "shapeCounts": shape_counts,
        "oddCounts": odd_counts,
        "lowCounts": low_counts,
        "centerSum": center_sum,
        "sumWidth": sum_width,
        "repeatTarget": repeat_target,
        "latestNumbers": latest_numbers,
        "tailCounts": tails,
        "dragCounts": drag_counts,
        "dragSourceTotals": drag_source_totals,
        "dragNumberScore": drag_number_score,
        "repeatCounts": repeat_counts,
        "repeatSourceTotals": repeat_source_totals,
        "intervalHitCounts": interval_hit_counts,
        "intervalFocusCounts": interval_focus_counts,
        "multiWindowScores": multi_window_raw,
        "tailMomentum": tail_momentum_raw,
        "appearanceStreak": appearance_streak,
        "windowSizes": [size for size, _ in window_specs if window_rows[size]],
    }


def research_feature_evidence(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    lookback: int = 60,
) -> dict[str, Any]:
    """Measure each signal with walk-forward data before letting it affect a pick.

    The target draw is never included in its own feature profile.  Precision is
    compared with the uniform 5/39 single-number baseline and shrunk toward 1
    when the sample is small or unstable between recent and older rows.
    """
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    target_count = min(max(0, int(lookback)), max(0, len(ordered) - 36))
    evidence_rows: dict[str, list[float]] = {key: [] for key in RESEARCH_FEATURE_KEYS}
    for index in range(target_count):
        target = ordered[index]
        training = ordered[index + 1 : index + 91]
        if len(training) < 36:
            continue
        profile = pattern_profile(training, max_number)
        actual = set(target["numbers"])
        for feature in RESEARCH_FEATURE_KEYS:
            ranked = sorted(
                range(1, max_number + 1),
                key=lambda number: (-profile["numberScores"][number].get(feature, 0.0), number),
            )
            top_five = ranked[:5]
            top_eight = ranked[:8]
            precision = (len(actual & set(top_five)) / 5) * 0.75 + (len(actual & set(top_eight)) / 8) * 0.25
            evidence_rows[feature].append(precision)

    baseline_precision = 5 / max_number
    feature_rows = []
    for feature in RESEARCH_FEATURE_KEYS:
        values = evidence_rows[feature]
        midpoint = max(1, len(values) // 2)
        recent_values = values[:midpoint]
        older_values = values[midpoint:]
        recent_precision = safe_divide(sum(recent_values), len(recent_values))
        older_precision = safe_divide(sum(older_values), len(older_values)) if older_values else recent_precision
        blended_precision = recent_precision * 0.65 + older_precision * 0.35
        raw_lift = safe_divide(blended_precision - baseline_precision, baseline_precision)
        sample_shrink = len(values) / (len(values) + 24)
        stability = max(
            0.0,
            1.0 - safe_divide(abs(recent_precision - older_precision), baseline_precision * 1.5),
        )
        multiplier = 1.0 + max(-0.30, min(0.30, raw_lift * 0.45 * sample_shrink * (0.7 + stability * 0.3)))
        feature_rows.append(
            {
                "id": feature,
                "label": RESEARCH_FEATURE_LABELS.get(feature, feature),
                "multiplier": round(multiplier, 3),
                "lift": round(raw_lift * 100, 1),
                "recentPrecision": round(recent_precision * 100, 1),
                "olderPrecision": round(older_precision * 100, 1),
                "testedCount": len(values),
                "stability": round(stability * 100, 1),
            }
        )
    feature_rows.sort(key=lambda item: (-item["multiplier"], -item["stability"], item["id"]))
    return {
        "baselinePrecision": round(baseline_precision * 100, 1),
        "testedCount": max((len(values) for values in evidence_rows.values()), default=0),
        "features": feature_rows,
    }


def combo_spread_score(numbers: list[int], max_number: int = 39) -> float:
    sorted_numbers = sorted(numbers)
    span = sorted_numbers[-1] - sorted_numbers[0]
    zones = len({(n - 1) // 10 for n in sorted_numbers})
    odd_count = sum(1 for n in sorted_numbers if n % 2)
    consecutive_pairs = sum(1 for left, right in zip(sorted_numbers, sorted_numbers[1:]) if right - left == 1)
    return (
        (span / (max_number - 1)) * 0.42
        + (zones / 4) * 0.32
        + (1 - abs(odd_count - 2.5) / 2.5) * 0.18
        + max(0, 1 - consecutive_pairs / 3) * 0.08
    )


def combo_pattern_score(numbers: list[int], profile: dict[str, Any], model: dict[str, Any], max_number: int = 39) -> float:
    combo_weights = model["combo"]
    sorted_numbers = sorted(numbers)
    pair_counts = profile["pairCounts"]
    pair_values = []
    for left_index, left in enumerate(sorted_numbers):
        for right in sorted_numbers[left_index + 1 :]:
            pair_values.append(pair_counts.get((left, right), 0))
    pair_score = safe_divide(sum(pair_values) / len(pair_values), max(pair_counts.values()) or 1) if pair_values else 0
    odd = sum(1 for n in sorted_numbers if n % 2)
    low = sum(1 for n in sorted_numbers if n <= max_number // 2)
    repeat = len(set(sorted_numbers) & profile["latestNumbers"])
    tail_diversity = len({n % 10 for n in sorted_numbers}) / min(5, 10)
    shape = (zone_signature(sorted_numbers), odd, low)
    scores = {
        "spread": combo_spread_score(sorted_numbers, max_number),
        "zone": signature_score(zone_signature(sorted_numbers), profile["zoneCounts"]),
        "odd": signature_score(odd, profile["oddCounts"]),
        "low": signature_score(low, profile["lowCounts"]),
        "sum": closeness(sum(sorted_numbers), profile["centerSum"], profile["sumWidth"]),
        "tail": tail_diversity,
        "repeat": closeness(repeat, profile["repeatTarget"], 1.6),
        "shape": signature_score(shape, profile.get("shapeCounts", {})),
        "interval": max(
            (
                (sum(1 for n in sorted_numbers if start <= n <= end) / 5)
                * safe_divide(profile["intervalFocusCounts"].get((start, end), 0), max(profile["intervalFocusCounts"].values()) or 1)
                for start, end in interval_windows(max_number)
            ),
            default=0,
        ),
    }
    base = sum(scores[key] * combo_weights.get(key, 0) for key in scores)
    return base * 0.86 + pair_score * 0.14


def score_number(
    n: int,
    profile: dict[str, Any],
    model: dict[str, Any],
    evidence: dict[str, float] | None = None,
) -> float:
    features = profile["numberScores"][n]
    weights = model["number"]
    base_score = sum(features[key] * weights.get(key, 0) for key in features)
    if not evidence:
        return base_score
    weighted_total = sum(abs(weights.get(key, 0)) for key in features) or 1.0
    evidence_score = sum(
        features[key] * weights.get(key, 0) * evidence.get(key, 1.0)
        for key in features
    ) / weighted_total
    return base_score * 0.84 + evidence_score * 0.16


def model_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    seed_label: str = "",
    profile_name: str = "balanced",
    candidate_budget: int | None = None,
    evidence: dict[str, float] | None = None,
) -> list[int]:
    if profile_name == "classic":
        return classic_recommendation(
            draws,
            max_number=max_number,
            pick_count=pick_count,
            seed_label=seed_label,
            candidate_budget=candidate_budget,
            evidence=evidence,
        )
    model = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["balanced"])
    profile = pattern_profile(draws, max_number)
    number_scores = {}
    for n in range(1, max_number + 1):
        number_scores[n] = score_number(n, profile, model, evidence=evidence) + random.Random(f"{seed_label}:{profile_name}:{n}").random() * 0.035

    pool = sorted(number_scores, key=lambda n: (-number_scores[n], n))[: min(24, max_number)]
    rng = random.Random(f"lotto-lab:{profile_name}:{seed_label}:{','.join(map(str, pool))}")
    candidates: set[tuple[int, ...]] = set()
    candidates.add(tuple(sorted(pool[:pick_count])))
    for _ in range(candidate_budget or 420):
        weighted = sorted(pool, key=lambda n: number_scores[n] + rng.random() * 0.28, reverse=True)
        candidates.add(tuple(sorted(weighted[:pick_count])))
        if len(pool) >= pick_count:
            candidates.add(tuple(sorted(rng.sample(pool, pick_count))))

    def score_combo(combo: tuple[int, ...]) -> float:
        score = sum(number_scores[n] for n in combo) / pick_count
        return score * 0.58 + combo_pattern_score(list(combo), profile, model, max_number) * 0.42

    best = max(candidates, key=lambda combo: (score_combo(combo), combo_spread_score(list(combo), max_number), combo))
    return list(best)


def flagship_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 6,
    profile_name: str = "balanced",
    evidence: dict[str, float] | None = None,
) -> list[int]:
    """Return the highest-ranked number pool for the flagship tier.

    This is a six-number candidate pool, not a claim that any number has a
    guaranteed higher physical lottery probability.
    """
    profile = pattern_profile(draws, max_number)
    model = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["balanced"])
    ranked = sorted(
        range(1, max_number + 1),
        key=lambda number: (-score_number(number, profile, model, evidence=evidence), number),
    )
    return ranked[: min(pick_count, max_number)]


def classic_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    seed_label: str = "",
    candidate_budget: int | None = None,
    evidence: dict[str, float] | None = None,
) -> list[int]:
    stats = number_stats(draws, max_number)
    frequency = stats["frequency"]
    recent_frequency = stats["recentFrequency"]
    gaps = stats["gaps"]
    max_freq = max(frequency.values()) or 1
    max_recent = max(recent_frequency.values()) or 1
    max_gap = max(gaps.values()) or 1
    research_profile = pattern_profile(draws, max_number) if evidence else None
    number_scores = {}
    for n in range(1, max_number + 1):
        heat = frequency[n] / max_freq
        recent = recent_frequency[n] / max_recent
        overdue = gaps[n] / max_gap
        base_score = heat * 0.45 + recent * 0.18 + overdue * 0.27
        if research_profile:
            features = research_profile["numberScores"][n]
            research_score = sum(
                features[key] * evidence.get(key, 1.0)
                for key in RESEARCH_FEATURE_KEYS
            ) / len(RESEARCH_FEATURE_KEYS)
            base_score = base_score * 0.84 + research_score * 0.16
        number_scores[n] = base_score + random.Random(f"{seed_label}:{n}").random() * 0.10

    pool = sorted(number_scores, key=lambda n: (-number_scores[n], n))[: min(22, max_number)]
    rng = random.Random(f"lotto-lab:{seed_label}:{','.join(map(str, pool))}")
    candidates: set[tuple[int, ...]] = set()
    candidates.add(tuple(sorted(pool[:pick_count])))
    for _ in range(candidate_budget or 260):
        weighted = sorted(pool, key=lambda n: number_scores[n] + rng.random() * 0.34, reverse=True)
        candidates.add(tuple(sorted(weighted[:pick_count])))
        if len(pool) >= pick_count:
            candidates.add(tuple(sorted(rng.sample(pool, pick_count))))

    def score_combo(combo: tuple[int, ...]) -> float:
        score = sum(number_scores[n] for n in combo) / pick_count
        return score * 0.72 + combo_spread_score(list(combo), max_number) * 0.28

    best = max(candidates, key=lambda combo: (score_combo(combo), combo_spread_score(list(combo), max_number), combo))
    return list(best)


def short_term_consensus(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    profile_name: str = "balanced",
) -> dict[str, Any]:
    """Compare short windows so a recent cluster can be seen without hiding longer context."""
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    views = []
    weighted_votes: dict[int, float] = {n: 0.0 for n in range(1, max_number + 1)}
    weights = {10: 0.50, 20: 0.30, 36: 0.20}
    for window in SHORT_TERM_WINDOWS:
        rows = ordered[:window]
        if len(rows) < 5:
            continue
        profile = pattern_profile(rows, max_number)
        model = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["balanced"])
        ranked = sorted(
            range(1, max_number + 1),
            key=lambda number: (-score_number(number, profile, model), number),
        )
        leaders = ranked[: min(8, max_number)]
        for rank, number in enumerate(leaders):
            weighted_votes[number] += weights[window] * (1.0 - rank / max(8, len(leaders)))
        views.append(
            {
                "window": window,
                "drawCount": len(rows),
                "leaders": leaders,
                "recommendation": model_recommendation(
                    rows,
                    max_number=max_number,
                    pick_count=pick_count,
                    seed_label=f"short-{window}",
                    profile_name=profile_name,
                ),
            }
        )
    if not views:
        return {"windows": [], "leaders": [], "recommendations": []}
    leaders = sorted(
        (number for number in range(1, max_number + 1)),
        key=lambda number: (-weighted_votes[number], number),
    )[: min(10, max_number)]
    return {
        "windows": views,
        "leaders": [
            {
                "number": number,
                "agreement": sum(1 for view in views if number in view["leaders"]),
                "score": round(weighted_votes[number] * 100),
            }
            for number in leaders
        ],
        "recommendations": [
            {"window": view["window"], "numbers": view["recommendation"]}
            for view in views
        ],
    }


def rolling_backtest(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    profile_name: str = "balanced",
    backtest_limit: int = BACKTEST_DEFAULT_LIMIT,
) -> dict[str, Any]:
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    distribution = {str(n): 0 for n in range(pick_count + 1)}
    rows = []
    requested_limit = max(BACKTEST_MIN_LIMIT, min(BACKTEST_MAX_LIMIT, int(backtest_limit)))
    sample_size = min(requested_limit, max(0, len(ordered) - 25))
    candidate_budget = 420 if requested_limit <= 35 else 220 if requested_limit <= 90 else 100 if requested_limit <= 180 else 60
    for index in range(sample_size):
        target = ordered[index]
        training = ordered[index + 1 : index + 91]
        if len(training) < 20:
            continue
        pick = model_recommendation(
            training,
            max_number=max_number,
            pick_count=pick_count,
            seed_label=f"bt-{target.get('date')}-{target.get('period')}",
            profile_name=profile_name,
            candidate_budget=candidate_budget,
        )
        hits = len(set(pick) & set(target["numbers"]))
        distribution[str(hits)] += 1
        rows.append(
            {
                "period": target.get("period", ""),
                "date": target.get("date", ""),
                "pick": pick,
                "actual": target["numbers"],
                "hits": hits,
            }
        )
    tested = len(rows)
    hit_sum = sum(row["hits"] for row in rows)
    one_plus = sum(1 for row in rows if row["hits"] >= 1)
    two_plus = sum(1 for row in rows if row["hits"] >= 2)
    three_plus = sum(1 for row in rows if row["hits"] >= 3)
    best_hit = max((row["hits"] for row in rows), default=0)
    midpoint = max(1, tested // 2)
    recent_segment = rows[:midpoint]
    older_segment = rows[midpoint:]
    recent_average = safe_divide(sum(row["hits"] for row in recent_segment), len(recent_segment))
    older_average = safe_divide(sum(row["hits"] for row in older_segment), len(older_segment))
    stability = max(0.0, round(100 - abs(recent_average - older_average) * 35, 1)) if older_segment else 0.0
    return {
        "requestedCount": requested_limit,
        "testedCount": tested,
        "averageHit": round(hit_sum / tested, 2) if tested else 0,
        "onePlusCount": one_plus,
        "onePlusRate": round((one_plus / tested) * 100, 1) if tested else 0,
        "twoPlusCount": two_plus,
        "twoPlusRate": round((two_plus / tested) * 100, 1) if tested else 0,
        "threePlusCount": three_plus,
        "threePlusRate": round((three_plus / tested) * 100, 1) if tested else 0,
        "bestHit": best_hit,
        "recentAverageHit": round(recent_average, 2),
        "stability": stability,
        "distribution": distribution,
        "recentRows": rows[:10],
        "method": f"每一期只用該期以前的歷史資料產生推薦，再與實際開獎比對；採用多視窗、版路支持度與近期穩定度；目前採用「{MODEL_PROFILES.get(profile_name, MODEL_PROFILES['balanced'])['label']}」。",
    }


def model_quality(backtest: dict[str, Any]) -> float:
    """Score a backtest while rewarding repeatability over one lucky hit."""
    return (
        backtest["averageHit"] * 100
        + backtest["recentAverageHit"] * 22
        + backtest["onePlusRate"] * 0.45
        + backtest["twoPlusRate"] * 1.25
        + backtest["threePlusRate"] * 2.5
        + backtest["bestHit"] * 10
        + backtest["distribution"].get("2", 0) * 1.7
        + backtest["stability"] * 0.22
    )


def choose_model_profile(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    backtest_limit: int = BACKTEST_DEFAULT_LIMIT,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    results = []
    validation_limit = max(requested := int(backtest_limit), BACKTEST_MIN_LIMIT)
    if validation_limit < 90 and len(draws) >= 85:
        validation_limit = min(90, max(60, validation_limit * 2))
    for profile_name, config in MODEL_PROFILES.items():
        backtest = rolling_backtest(
            draws,
            max_number=max_number,
            pick_count=pick_count,
            profile_name=profile_name,
            backtest_limit=backtest_limit,
        )
        validation = backtest
        if validation_limit > max(requested, BACKTEST_MIN_LIMIT) and len(draws) >= validation_limit + 25:
            validation = rolling_backtest(
                draws,
                max_number=max_number,
                pick_count=pick_count,
                profile_name=profile_name,
                backtest_limit=validation_limit,
            )
        primary_quality = model_quality(backtest)
        validation_quality = model_quality(validation)
        quality = primary_quality * 0.70 + validation_quality * 0.30
        results.append(
            {
                "id": profile_name,
                "label": config["label"],
                "quality": round(quality, 2),
                "averageHit": backtest["averageHit"],
                "onePlusRate": backtest["onePlusRate"],
                "twoPlusRate": backtest["twoPlusRate"],
                "threePlusRate": backtest["threePlusRate"],
                "bestHit": backtest["bestHit"],
                "testedCount": backtest["testedCount"],
                "recentAverageHit": backtest["recentAverageHit"],
                "stability": backtest["stability"],
                "validationCount": validation["testedCount"],
                "validationAverageHit": validation["averageHit"],
                "validationTwoPlusRate": validation["twoPlusRate"],
                "validationThreePlusRate": validation["threePlusRate"],
            }
        )
    results.sort(key=lambda item: (-item["quality"], -item["averageHit"], -item["threePlusRate"], item["id"]))
    selected = results[0]["id"] if results else "balanced"
    return selected, rolling_backtest(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        profile_name=selected,
        backtest_limit=backtest_limit,
    ), results


def pattern_summary(draws: list[dict[str, Any]], max_number: int, selected_profile: str) -> dict[str, Any]:
    profile = pattern_profile(draws, max_number)
    ordered = profile["ordered"]
    recent = ordered[:30]
    zone_rows = sorted(profile["zoneCounts"].items(), key=lambda item: (-item[1], item[0]))[:3]
    odd_rows = sorted(profile["oddCounts"].items(), key=lambda item: (-item[1], item[0]))[:3]
    low_rows = sorted(profile["lowCounts"].items(), key=lambda item: (-item[1], item[0]))[:3]
    tail_rows = sorted(profile["tailCounts"].items(), key=lambda item: (-item[1], item[0]))[:5]
    pair_rows = sorted(profile["pairCounts"].items(), key=lambda item: (-item[1], item[0]))[:5]
    interval_rows = sorted(
        interval_windows(max_number),
        key=lambda window: (
            -profile["intervalFocusCounts"].get(window, 0),
            -profile["intervalHitCounts"].get(window, 0),
            window[0],
        ),
    )[:5]
    max_multi_window = max(profile["multiWindowScores"].values()) or 1
    multi_window_rows = sorted(
        profile["multiWindowScores"].items(), key=lambda item: (-item[1], item[0])
    )[:8]
    tail_momentum_rows = sorted(
        profile["tailMomentum"].items(), key=lambda item: (-item[1], item[0])
    )[:5]
    selected_model = MODEL_PROFILES.get(selected_profile, MODEL_PROFILES["balanced"])
    signal_rows = []
    for number, features in profile["numberScores"].items():
        support_count = sum(
            1
            for key in ("multiWindow", "tailMomentum", "pair", "drag", "repeatSignal", "interval")
            if features.get(key, 0) >= 0.55
        )
        signal_rows.append(
            {
                "number": number,
                "score": round(score_number(number, profile, selected_model) * 100),
                "support": support_count,
            }
        )
    signal_rows.sort(key=lambda item: (-item["score"], -item["support"], item["number"]))
    transitions = [len(set(newer["numbers"]) & set(older["numbers"])) for newer, older in zip(ordered, ordered[1:])]
    repeat_avg = round(sum(transitions[:30]) / min(30, len(transitions)), 2) if transitions else 0
    latest = ordered[0]["numbers"] if ordered else []
    neighbors = sorted({nearby for number in latest for nearby in (number - 1, number + 1) if 1 <= nearby <= max_number})
    drag_rows = []
    for source in latest:
        source_total = profile["dragSourceTotals"].get(source, 0) or 1
        source_targets = [
            {
                "base": source,
                "follow": target,
                "count": count,
                "rate": round((count / source_total) * 100, 1),
            }
            for (src, target), count in profile["dragCounts"].items()
            if src == source
        ]
        source_targets.sort(key=lambda item: (-item["count"], -item["rate"], item["follow"]))
        drag_rows.extend(source_targets[:2])
    drag_rows.sort(key=lambda item: (-item["count"], -item["rate"], item["base"], item["follow"]))
    repeat_rows = []
    for number in latest:
        total = profile["repeatSourceTotals"].get(number, 0)
        count = profile["repeatCounts"].get(number, 0)
        repeat_rows.append(
            {
                "number": number,
                "count": count,
                "rate": round((count / total) * 100, 1) if total else 0,
            }
        )
    repeat_rows.sort(key=lambda item: (-item["count"], -item["rate"], item["number"]))
    sums = [sum(draw["numbers"]) for draw in recent]
    span_values = [max(draw["numbers"]) - min(draw["numbers"]) for draw in recent]
    return {
        "selectedProfile": selected_profile,
        "selectedLabel": MODEL_PROFILES.get(selected_profile, MODEL_PROFILES["balanced"])["label"],
        "zonePatterns": [{"pattern": "-".join(map(str, pattern)), "count": count} for pattern, count in zone_rows],
        "oddPatterns": [{"odd": odd, "even": 5 - odd, "count": count} for odd, count in odd_rows],
        "lowPatterns": [{"low": low, "high": 5 - low, "count": count} for low, count in low_rows],
        "tails": [{"tail": tail, "count": count} for tail, count in tail_rows],
        "intervals": [
            {
                "start": start,
                "end": end,
                "label": f"{start:02d}-{end:02d}",
                "hits": profile["intervalHitCounts"].get((start, end), 0),
                "focusCount": profile["intervalFocusCounts"].get((start, end), 0),
                "rate": round((profile["intervalFocusCounts"].get((start, end), 0) / len(recent)) * 100, 1) if recent else 0,
            }
            for start, end in interval_rows
        ],
        "pairCombos": [{"numbers": list(pair), "count": count} for pair, count in pair_rows],
        "dragCards": drag_rows[:6],
        "repeatCandidates": repeat_rows,
        "repeatAverage": repeat_avg,
        "neighborNumbers": neighbors[:12],
        "sumRange": {
            "min": min(sums) if sums else 0,
            "max": max(sums) if sums else 0,
            "center": profile["centerSum"],
        },
        "spanAverage": round(sum(span_values) / len(span_values), 1) if span_values else 0,
        "multiWindowNumbers": [
            {"number": number, "score": round((score / max_multi_window) * 100)}
            for number, score in multi_window_rows
        ],
        "tailMomentum": [{"tail": tail, "score": round(score * 100, 1)} for tail, score in tail_momentum_rows],
        "signalLeaders": signal_rows[:8],
    }


def analyze(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    reference_draws: list[dict[str, Any]] | None = None,
    backtest_limit: int = BACKTEST_DEFAULT_LIMIT,
) -> dict[str, Any]:
    stats = number_stats(draws, max_number)
    frequency = stats["frequency"]
    gaps = stats["gaps"]
    hot = sorted(frequency, key=lambda n: (-frequency[n], n))[:10]
    cold = sorted(frequency, key=lambda n: (frequency[n], n))[:10]
    overdue = sorted(gaps, key=lambda n: (-gaps[n], n))[:10]

    scored = []
    max_freq = max(frequency.values()) or 1
    max_gap = max(gaps.values()) or 1
    for n in frequency:
        score = (frequency[n] / max_freq) * 0.58 + (gaps[n] / max_gap) * 0.42
        scored.append((score, n))
    seed_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    selected_profile, backtest, model_results = choose_model_profile(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        backtest_limit=backtest_limit,
    )
    research_evidence = research_feature_evidence(reference_draws or draws, max_number=max_number)
    evidence_map = {
        item["id"]: item["multiplier"] for item in research_evidence.get("features", [])
    }
    recommendation = model_recommendation(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        seed_label=seed_label,
        profile_name=selected_profile,
        evidence=evidence_map,
    )
    flagship_numbers = flagship_recommendation(
        draws,
        max_number=max_number,
        pick_count=6,
        profile_name=selected_profile,
        evidence=evidence_map,
    )
    patterns = pattern_summary(draws, max_number, selected_profile)
    short_consensus = short_term_consensus(
        reference_draws or draws,
        max_number=max_number,
        pick_count=pick_count,
        profile_name=selected_profile,
    )

    return {
        "drawCount": len(draws),
        "hot": [{"number": n, "count": frequency[n]} for n in hot],
        "cold": [{"number": n, "count": frequency[n]} for n in cold],
        "overdue": [{"number": n, "gap": gaps[n]} for n in overdue],
        "frequency": [{"number": n, "count": frequency[n], "gap": gaps[n]} for n in frequency],
        "recommendation": recommendation,
        "flagshipRecommendation": flagship_numbers,
        "backtest": backtest,
        "modelProfiles": model_results,
        "patterns": patterns,
        "researchEvidence": research_evidence,
        "shortTermConsensus": short_consensus,
        "note": "這是用多視窗熱度、近期動能、遺漏週期、尾數動能、區間集中、奇偶大小、總和版路、拖牌連莊、鄰近號與穩定度回測做的交叉統計參考；彩券每期仍是隨機事件，不代表可預測或保證中獎。",
    }


def analyze_with_stable_backtest(
    draws: list[dict[str, Any]],
    backtest_draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    backtest_limit: int = BACKTEST_DEFAULT_LIMIT,
) -> dict[str, Any]:
    requested_limit = max(BACKTEST_MIN_LIMIT, min(BACKTEST_MAX_LIMIT, int(backtest_limit)))
    analysis = analyze(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        reference_draws=backtest_draws,
        backtest_limit=requested_limit,
    )
    current_backtest = analysis.get("backtest", {})
    needs_longer_history = len(draws) < requested_limit + 25
    if (current_backtest.get("testedCount") and not needs_longer_history) or len(backtest_draws) < BACKTEST_MIN_HISTORY:
        return analysis

    fallback_draws = backtest_draws[: max(BACKTEST_FALLBACK_LIMIT, requested_limit + 90)]
    selected_profile, fallback_backtest, model_results = choose_model_profile(
        fallback_draws,
        max_number=max_number,
        pick_count=pick_count,
        backtest_limit=requested_limit,
    )
    if not fallback_backtest.get("testedCount"):
        return analysis

    research_evidence = research_feature_evidence(fallback_draws, max_number=max_number)
    evidence_map = {
        item["id"]: item["multiplier"] for item in research_evidence.get("features", [])
    }
    analysis["backtest"] = fallback_backtest
    analysis["modelProfiles"] = model_results
    analysis["researchEvidence"] = research_evidence
    analysis["recommendation"] = model_recommendation(
        fallback_draws,
        max_number=max_number,
        pick_count=pick_count,
        seed_label=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        profile_name=selected_profile,
        evidence=evidence_map,
    )
    analysis["flagshipRecommendation"] = flagship_recommendation(
        fallback_draws,
        max_number=max_number,
        pick_count=6,
        profile_name=selected_profile,
        evidence=evidence_map,
    )
    analysis["shortTermConsensus"] = short_term_consensus(
        fallback_draws,
        max_number=max_number,
        pick_count=pick_count,
        profile_name=selected_profile,
    )
    analysis["patterns"]["selectedProfile"] = selected_profile
    analysis["patterns"]["selectedLabel"] = MODEL_PROFILES.get(selected_profile, MODEL_PROFILES["balanced"])["label"]
    analysis["backtest"]["method"] = (
        f"目前選擇近 {len(draws)} 期，短期樣本不足以單獨回測；"
        f"模型回測已自動改用近 {len(fallback_draws)} 期穩定樣本。"
        f"{fallback_backtest.get('method', '')}"
    )
    return analysis


def build_payload(game: str, limit: int, backtest_limit: int = BACKTEST_DEFAULT_LIMIT) -> dict[str, Any]:
    requested_backtest_limit = max(BACKTEST_MIN_LIMIT, min(BACKTEST_MAX_LIMIT, int(backtest_limit)))
    fetch_limit = min(5000, max(limit, requested_backtest_limit + 90, BACKTEST_FALLBACK_LIMIT))
    if game == "tw539":
        latest = taiwan_latest()
        history = taiwan_history(fetch_limit)
        if history and not same_draw(history[0], latest):
            history = [latest] + [item for item in history if item.get("period") != latest.get("period") and not same_draw(item, latest)]
        persist_draw_history([latest, *history])
        draws = history[:limit]
        analysis_key = f"{cache_key_for_draws('analysis', game, fetch_limit, history)}-selected-{limit}-backtest-{requested_backtest_limit}"
        analysis = cached(analysis_key, lambda: analyze_with_stable_backtest(draws, history, backtest_limit=requested_backtest_limit))
        return {"latest": public_draw(latest), "history": public_draws(draws), "analysis": analysis}
    if game == "ca-fantasy5":
        history = california_history(fetch_limit)
        if not history:
            raise RuntimeError("加州天天樂資料頁目前沒有可解析的開獎資料")
        persist_draw_history(history)
        draws = history[:limit]
        analysis_key = f"{cache_key_for_draws('analysis', game, fetch_limit, history)}-selected-{limit}-backtest-{requested_backtest_limit}"
        analysis = cached(analysis_key, lambda: analyze_with_stable_backtest(draws, history, backtest_limit=requested_backtest_limit))
        return {"latest": public_draw(history[0]), "history": public_draws(draws), "analysis": analysis}
    raise ValueError("unknown game")


def public_draw(draw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in draw.items() if key not in {"source", "sourceUrl"}}


def public_draws(draws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_draw(draw) for draw in draws]


class Handler(SimpleHTTPRequestHandler):
    server_version = "LottoLab"
    sys_version = ""

    def translate_path(self, path: str) -> str:
        clean = posixpath.normpath(unquote(urlparse(path).path))
        if clean.startswith("/api/"):
            return str(PUBLIC / "index.html")
        if clean == "/":
            return str(PUBLIC / "index.html")
        target = (PUBLIC / clean.lstrip("/")).resolve()
        public_root = PUBLIC.resolve()
        if target == public_root or public_root in target.parents:
            return str(target)
        return str(PUBLIC / "index.html")

    def client_key(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def rate_limited(self, path: str) -> tuple[bool, int]:
        limit = API_RATE_LIMITS.get(path)
        if not limit:
            return False, 0
        max_hits, window_seconds = limit
        now = time.time()
        key = (self.client_key(), path)
        hits = [hit for hit in rate_limit_hits.get(key, []) if now - hit < window_seconds]
        if len(hits) >= max_hits:
            retry_after = max(1, int(window_seconds - (now - hits[0])))
            rate_limit_hits[key] = hits
            return True, retry_after
        hits.append(now)
        rate_limit_hits[key] = hits
        return False, 0

    def verify_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        origin_host = urlparse(origin).netloc
        return origin_host == self.headers.get("Host", "")

    def reject_if_rate_limited(self, path: str) -> bool:
        limited, retry_after = self.rate_limited(path)
        if not limited:
            return False
        self.send_json({"ok": False, "error": "請求太頻繁，請稍後再試"}, status=429, extra_headers={"Retry-After": str(retry_after)})
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and self.reject_if_rate_limited(parsed.path):
            return
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "service": "lotto-lab", "time": datetime.now().isoformat(timespec="seconds")})
            return
        if parsed.path == "/api/config":
            self.send_json(
                {
                    "ok": True,
                    "subscription": {
                        "enabled": bool(STRIPE_PAYMENT_LINK or STRIPE_FLAGSHIP_PAYMENT_LINK),
                        "paymentLink": STRIPE_PAYMENT_LINK,
                        "plans": [
                            {
                                "id": "pro",
                                "name": "Pro 訂閱",
                                "price": "$9 / 月起",
                                "features": ["120-365 期進階分析", "跨年歷史查詢", "模型回測與版路模式", "高分組合排序"],
                            },
                            {
                                "id": "flagship",
                                "name": "摘星狙擊手｜量化旗艦版",
                                "price": "高階會員",
                                "paymentLink": STRIPE_FLAGSHIP_PAYMENT_LINK,
                                "features": ["每期模型高分 6 碼候選池", "訊號證據與穩定度校準", "短中長期多窗口交叉排名", "優先查看研究版路分析"],
                            },
                        ],
                        "flagshipPaymentLink": STRIPE_FLAGSHIP_PAYMENT_LINK,
                    },
                    "notifications": {
                        "supported": bool(PUSH_PUBLIC_KEY),
                        "serverReady": push_server_ready(),
                        "autoNotify": AUTO_NOTIFY_ENABLED,
                        "autoNotifyIntervalSeconds": max(60, AUTO_NOTIFY_INTERVAL_SECONDS),
                        "autoNotifyGames": AUTO_NOTIFY_GAMES,
                        "publicKey": PUSH_PUBLIC_KEY,
                        "subscriberCount": len(load_push_subscriptions()),
                    },
                    "storage": {
                        "backend": database_backend() if database_ready else "file-fallback",
                        "databaseReady": database_ready,
                        "persistent": bool(DATABASE_URL) or not bool(os.environ.get("RENDER_SERVICE_ID")),
                    },
                }
            )
            return
        if parsed.path == "/api/lottery":
            params = parse_qs(parsed.query)
            try:
                game = clean_game(params.get("game", ["tw539"])[0])
                limit = clamp_int(params.get("limit", ["180"])[0], 180, 10, 365)
                backtest_limit = clamp_int(
                    params.get("backtestLimit", [str(BACKTEST_DEFAULT_LIMIT)])[0],
                    BACKTEST_DEFAULT_LIMIT,
                    BACKTEST_MIN_LIMIT,
                    BACKTEST_MAX_LIMIT,
                )
                payload = build_payload(game, limit, backtest_limit=backtest_limit)
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **payload})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/history-search":
            params = parse_qs(parsed.query)
            current_year = datetime.now().year
            try:
                game = clean_game(params.get("game", ["tw539"])[0])
                from_year = clamp_int(params.get("fromYear", [str(current_year - 2)])[0], current_year - 2, 1990, current_year)
                to_year = clamp_int(params.get("toYear", [str(current_year)])[0], current_year, 1990, current_year)
                if from_year > to_year:
                    from_year, to_year = to_year, from_year
                keyword = params.get("keyword", [""])[0].strip()[:40]
                number_value = params.get("number", [""])[0]
                number = clamp_int(number_value, 0, 1, 39) if number_value else None
                limit = clamp_int(params.get("limit", ["2000"])[0], 2000, 50, 5000)
                if game == "tw539":
                    payload = search_taiwan_history(from_year, to_year, keyword=keyword, number=number, limit=limit)
                elif game == "ca-fantasy5":
                    payload = search_california_history(from_year, to_year, keyword=keyword, number=number, limit=limit)
                else:
                    raise ValueError("不支援的遊戲種類")
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **payload})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/") and self.reject_if_rate_limited(parsed.path):
            return
        if not self.verify_origin():
            self.send_json({"ok": False, "error": "不允許的請求來源"}, status=403)
            return
        if parsed.path == "/api/push-subscription":
            try:
                payload = self.read_json_body()
                action = payload.get("action", "subscribe")
                subscription = payload.get("subscription", {})
                if action in {"subscribe", "sync-picks"}:
                    count = upsert_push_subscription(
                        subscription,
                        payload.get("game", "all"),
                        payload.get("savedPicks", []),
                    )
                    self.send_json({"ok": True, "subscriberCount": count})
                    return
                if action == "unsubscribe":
                    count = remove_push_subscription(subscription)
                    self.send_json({"ok": True, "subscriberCount": count})
                    return
                raise ValueError("不支援的通知操作")
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
        if parsed.path == "/api/notify-latest":
            try:
                payload = self.read_json_body()
                if not NOTIFY_SECRET:
                    self.send_json({"ok": False, "error": "尚未設定通知密鑰"}, status=403)
                    return
                supplied = self.headers.get("X-Lotto-Notify-Secret", "") or str(payload.get("secret", ""))
                if supplied != NOTIFY_SECRET:
                    self.send_json({"ok": False, "error": "通知密鑰不正確"}, status=403)
                    return
                if not push_server_ready():
                    self.send_json({"ok": False, "error": "尚未設定完整推播金鑰"}, status=400)
                    return
                game = clean_game(payload.get("game", "tw539"))
                with notify_lock:
                    self.send_json(notify_latest_game(game))
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
                return
        self.send_json({"ok": False, "error": "not found"}, status=404)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError("資料量過大")
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON 格式不正確")
        return payload

    def broadcast_notification(self, message: dict[str, Any]) -> tuple[int, int, int]:
        return broadcast_push_message(message)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest"):
            self.send_header("Cache-Control", "no-cache")
        elif parsed.path.startswith(("/app.js", "/styles.css", "/icon")):
            self.send_header("Cache-Control", "public, max-age=86400")
        super().end_headers()

    def send_json(self, payload: dict[str, Any], status: int = 200, extra_headers: dict[str, str] | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        init_database()
        print(f"storage enabled: {database_backend()}")
    except Exception as exc:
        print(f"storage unavailable, using legacy file fallback: {exc}")
    server = ThreadingHTTPServer((host, port), Handler)
    if AUTO_NOTIFY_ENABLED:
        threading.Thread(target=auto_notify_loop, name="lotto-auto-notify", daemon=True).start()
        print(f"auto notify enabled every {max(60, AUTO_NOTIFY_INTERVAL_SECONDS)}s for {', '.join(AUTO_NOTIFY_GAMES) or 'no games'}")
    print(f"摘星狙擊手 running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
