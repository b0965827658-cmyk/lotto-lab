from __future__ import annotations

import csv
import hashlib
import html
import io
import itertools
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
LATEST_CACHE_TTL_SECONDS = int(os.environ.get("LOTTO_LATEST_CACHE_TTL_SECONDS", "10"))
BACKTEST_FALLBACK_LIMIT = 90
BACKTEST_MIN_HISTORY = 36
BACKTEST_DEFAULT_LIMIT = 24
BACKTEST_MIN_LIMIT = 7
BACKTEST_MAX_LIMIT = 365
MAX_JSON_BODY_BYTES = 64 * 1024
MAX_PUSH_SUBSCRIPTIONS = int(os.environ.get("LOTTO_MAX_PUSH_SUBSCRIPTIONS", "5000"))
MAX_SAVED_PICKS_PER_SUBSCRIPTION = 20
API_RATE_LIMITS = {
    "/api/latest": (120, 60),
    "/api/lottery": (90, 60),
    "/api/flagship-history": (60, 60),
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
AUTO_NOTIFY_INTERVAL_SECONDS = int(os.environ.get("LOTTO_AUTO_NOTIFY_INTERVAL_SECONDS", "30"))
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
cache_lock = threading.RLock()
cache_inflight: dict[str, threading.Event] = {}
flagship_snapshot_memory: dict[str, list[int]] = {}
flagship_snapshot_lock = threading.RLock()
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
    database_execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_snapshots (
            snapshot_key TEXT PRIMARY KEY,
            game TEXT NOT NULL,
            latest_period TEXT NOT NULL,
            latest_date TEXT NOT NULL,
            selected_limit INTEGER NOT NULL,
            numbers_json TEXT NOT NULL,
            profile_name TEXT NOT NULL,
            history_fingerprint TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    database_execute(
        """
        CREATE TABLE IF NOT EXISTS flagship_analysis_history (
            snapshot_key TEXT PRIMARY KEY,
            game TEXT NOT NULL,
            latest_period TEXT NOT NULL,
            latest_date TEXT NOT NULL,
            selected_limit INTEGER NOT NULL,
            numbers_json TEXT NOT NULL,
            method TEXT NOT NULL,
            components_json TEXT NOT NULL,
            reasoning_json TEXT NOT NULL,
            profile_name TEXT NOT NULL,
            history_fingerprint TEXT NOT NULL,
            actual_period TEXT NOT NULL DEFAULT '',
            actual_date TEXT NOT NULL DEFAULT '',
            actual_numbers_json TEXT NOT NULL DEFAULT '[]',
            hit_count INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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


def flagship_reasoning_summary(
    analysis: dict[str, Any],
    numbers: list[int],
    selected_limit: int,
) -> dict[str, Any]:
    """Keep a compact, readable explanation beside each published flagship pick."""
    patterns = analysis.get("patterns") or {}
    backtest = analysis.get("backtest") or {}
    support = backtest.get("numberSupport") or {}
    top_support = sorted(
        (
            {"number": int(number), "score": round(float(value), 4)}
            for number, value in support.items()
            if str(number).isdigit()
        ),
        key=lambda item: (-item["score"], item["number"]),
    )[:8]
    selected_support = [
        {"number": number, "score": round(float(support.get(str(number), support.get(number, 0))), 4)}
        for number in numbers
    ]
    adaptive_numbers = [
        int(number) for number in (analysis.get("adaptiveRecommendation") or [])[:5]
    ]
    if len(adaptive_numbers) != 5:
        adaptive_numbers = [int(number) for number in numbers[:5]]
    return {
        "analysisLimit": int(selected_limit),
        "selectedNumbers": list(numbers),
        "adaptiveNumbers": adaptive_numbers if len(adaptive_numbers) == 5 else [],
        "recentHot": (analysis.get("hot") or [])[:8],
        "intervals": (patterns.get("intervals") or [])[:3],
        "pairCombos": (patterns.get("pairCombos") or [])[:3],
        "dragCards": (patterns.get("dragCards") or [])[:3],
        "repeatCandidates": (patterns.get("repeatCandidates") or [])[:3],
        "multiWindowNumbers": (patterns.get("multiWindowNumbers") or [])[:8],
        "tailMomentum": (patterns.get("tailMomentum") or [])[:3],
        "backtestSummary": {
            "testedCount": backtest.get("testedCount", 0),
            "averageHit": backtest.get("averageHit", 0),
            "onePlusRate": backtest.get("onePlusRate", 0),
            "twoPlusRate": backtest.get("twoPlusRate", 0),
            "threePlusRate": backtest.get("threePlusRate", 0),
            "bestHit": backtest.get("bestHit", 0),
        },
        "backtestLeaders": top_support,
        "selectedBacktestSupport": selected_support,
    }


def load_flagship_analysis_history(game: str, limit: int = 30) -> list[dict[str, Any]]:
    if not database_ready or game not in ALLOWED_GAMES:
        return []
    rows = database_query(
        """
        SELECT snapshot_key, game, latest_period, latest_date, selected_limit,
               numbers_json, method, components_json, reasoning_json,
               profile_name, history_fingerprint, actual_period, actual_date,
               actual_numbers_json, hit_count, created_at, updated_at
        FROM flagship_analysis_history
        WHERE game = ?
        ORDER BY latest_date DESC, latest_period DESC, created_at DESC
        LIMIT ?
        """,
        (game, max(1, min(int(limit), 100))),
    )
    history = []
    for row in rows:
        try:
            numbers = json.loads(row[5])
            components = json.loads(row[7])
            reasoning = json.loads(row[8])
            actual_numbers = json.loads(row[13])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(numbers, list) or len(numbers) not in (5, 6):
            continue
        if not isinstance(actual_numbers, list):
            actual_numbers = []
        history.append(
            {
                "snapshotKey": row[0],
                "game": row[1],
                "latestPeriod": row[2],
                "latestDate": row[3],
                "selectedLimit": row[4],
                "numbers": normalize_numbers(numbers),
                "method": row[6],
                "components": components if isinstance(components, list) else [],
                "reasoning": reasoning if isinstance(reasoning, dict) else {},
                "profile": row[9],
                "historyFingerprint": row[10],
                "actualPeriod": row[11] or "",
                "actualDate": row[12] or "",
                "actualNumbers": normalize_numbers(actual_numbers) if actual_numbers else [],
                "hitCount": row[14],
                "createdAt": row[15],
                "updatedAt": row[16],
            }
        )
    return history


def persist_flagship_analysis_history(
    game: str,
    latest: dict[str, Any],
    selected_limit: int,
    history: list[dict[str, Any]],
    analysis: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    """Persist the flagship reasoning and fill outcomes when the next draw arrives."""
    if not database_ready or game not in ALLOWED_GAMES:
        return
    snapshot_key = str(snapshot.get("key", "")).strip()
    numbers = [int(number) for number in (analysis.get("flagshipRecommendation") or [])[:5]]
    if not snapshot_key or len(numbers) != 5:
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    components = analysis.get("flagshipComponents") or [
        {"id": "recent", "label": "近期熱牌", "weight": 26},
        {"id": "interval", "label": "區間", "weight": 20},
        {"id": "backtest", "label": "回測", "weight": 18},
        {"id": "pattern", "label": "版路", "weight": 16},
        {"id": "drag", "label": "拖牌", "weight": 10},
        {"id": "tail", "label": "尾數", "weight": 10},
    ]
    reasoning = flagship_reasoning_summary(analysis, numbers, selected_limit)
    profile_name = str((analysis.get("patterns") or {}).get("selectedProfile", "balanced"))
    fingerprint = str(snapshot.get("historyFingerprint") or draw_fingerprint(history))
    database_execute(
        """
        INSERT INTO flagship_analysis_history
            (snapshot_key, game, latest_period, latest_date, selected_limit,
             numbers_json, method, components_json, reasoning_json, profile_name,
             history_fingerprint, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (snapshot_key) DO UPDATE SET
            selected_limit = excluded.selected_limit,
            numbers_json = excluded.numbers_json,
            method = excluded.method,
            components_json = excluded.components_json,
            reasoning_json = excluded.reasoning_json,
            profile_name = excluded.profile_name,
            history_fingerprint = excluded.history_fingerprint,
            updated_at = excluded.updated_at
        """,
        (
            snapshot_key,
            game,
            str(latest.get("period", "")),
            str(latest.get("date", "")),
            int(selected_limit),
            json.dumps(numbers, ensure_ascii=False),
            str(analysis.get("flagshipMethod", "")),
            json.dumps(components, ensure_ascii=False),
            json.dumps(reasoning, ensure_ascii=False),
            profile_name,
            fingerprint,
            now,
            now,
        ),
    )

    ordered_history = canonical_analysis_draws(history)
    if not ordered_history:
        return
    current_key = (str(latest.get("date", "")), str(latest.get("period", "")))
    open_rows = database_query(
        """
        SELECT snapshot_key, latest_date, latest_period, numbers_json
        FROM flagship_analysis_history
        WHERE game = ? AND actual_period = ''
        ORDER BY latest_date DESC, latest_period DESC
        LIMIT 100
        """,
        (game,),
    )
    for row in open_rows:
        snapshot_draw_key = (str(row[1]), str(row[2]))
        if snapshot_draw_key >= current_key:
            continue
        newer_draws = [
            draw
            for draw in ordered_history
            if (str(draw.get("date", "")), str(draw.get("period", ""))) > snapshot_draw_key
        ]
        if not newer_draws:
            continue
        actual = min(
            newer_draws,
            key=lambda draw: (str(draw.get("date", "")), str(draw.get("period", ""))),
        )
        try:
            stored_numbers = json.loads(row[3])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(stored_numbers, list) or len(stored_numbers) not in (5, 6):
            continue
        actual_numbers = normalize_numbers(actual.get("numbers", []))
        hits = len(set(int(number) for number in stored_numbers) & set(actual_numbers))
        database_execute(
            """
            UPDATE flagship_analysis_history
            SET actual_period = ?, actual_date = ?, actual_numbers_json = ?,
                hit_count = ?, updated_at = ?
            WHERE snapshot_key = ? AND actual_period = ''
            """,
            (
                str(actual.get("period", "")),
                str(actual.get("date", "")),
                json.dumps(actual_numbers, ensure_ascii=False),
                hits,
                now,
                row[0],
            ),
        )


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


def draw_source_priority(draw: dict[str, Any]) -> int:
    source = str(draw.get("source", ""))
    if "台灣彩券" in source or "政府資料" in source:
        return 3
    if "Pilio" in source or "樂透彩" in source:
        return 2
    return 1


def canonical_analysis_draws(draws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and deterministically order rows before any model calculation."""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for draw in draws:
        if not isinstance(draw, dict):
            continue
        game, period, date = database_draw_key(draw)
        numbers = normalize_numbers(draw.get("numbers", []))
        if game not in ALLOWED_GAMES or not period or not date or len(numbers) != 5 or len(set(numbers)) != 5:
            continue
        clean = dict(draw)
        clean.update({"game": game, "period": period, "date": date, "numbers": numbers})
        key = (game, period, date)
        existing = merged.get(key)
        if existing is None or (
            draw_source_priority(clean), tuple(clean["numbers"])
        ) > (
            draw_source_priority(existing), tuple(existing["numbers"])
        ):
            merged[key] = clean
    values = list(merged.values())
    values.sort(key=lambda item: (item.get("date", ""), str(item.get("period", ""))), reverse=True)
    return values


def draw_fingerprint(draws: list[dict[str, Any]]) -> str:
    rows = canonical_analysis_draws(draws)
    value = "|".join(
        f"{item.get('game', '')}:{item.get('date', '')}:{item.get('period', '')}:{','.join(map(str, item.get('numbers', [])))}"
        for item in rows
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def stable_analysis_seed(draws: list[dict[str, Any]], label: str = "") -> str:
    rows = canonical_analysis_draws(draws)
    latest = rows[0] if rows else {}
    return (
        f"{label}|{latest.get('date', '')}|{latest.get('period', '')}|"
        f"{len(rows)}|{draw_fingerprint(rows)}"
    )


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
        if game not in ALLOWED_GAMES or not isinstance(raw_numbers, list) or not 5 <= len(raw_numbers) <= 8:
            continue
        try:
            numbers = sorted({int(number) for number in raw_numbers})
        except (TypeError, ValueError):
            continue
        if not 5 <= len(numbers) <= 8 or any(number < 1 or number > 39 for number in numbers):
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
    subscriptions = load_push_subscriptions()
    if not subscriptions:
        return {"ok": True, "game": game, "sent": 0, "failed": 0, "subscriberCount": 0, "skipped": True, "message": "目前沒有訂閱用戶"}

    # 推播只需要最新一期，不應為了通知重跑完整回測與旗艦分析。
    # 這讓背景輪詢可以維持在 30 秒左右，也避免開獎時通知被模型計算拖住。
    if game == "tw539":
        lottery = taiwan_latest()
    else:
        latest_history = california_history(1)
        if not latest_history:
            raise RuntimeError("加州天天樂目前沒有可用的最新資料")
        lottery = latest_history[0]
    if already_notified(game, lottery):
        return {"ok": True, "game": game, "sent": 0, "failed": 0, "subscriberCount": len(subscriptions), "skipped": True, "message": "這一期已通知過"}
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
        time.sleep(max(30, AUTO_NOTIFY_INTERVAL_SECONDS))


def cached(key: str, loader, ttl_seconds: int | None = None):
    ttl = CACHE_TTL_SECONDS if ttl_seconds is None else max(1, ttl_seconds)
    while True:
        with cache_lock:
            hit = cache.get(key)
            if hit and time.time() - hit.created_at < ttl:
                return hit.value
            pending = cache_inflight.get(key)
            if pending is None:
                pending = threading.Event()
                cache_inflight[key] = pending
                break
        pending.wait(timeout=120)

    try:
        value = loader()
    except Exception:
        with cache_lock:
            cache_inflight.pop(key, None)
            pending.set()
        raise

    with cache_lock:
        cache[key] = CacheItem(value=value, created_at=time.time())
        cache_inflight.pop(key, None)
        pending.set()
    return value


def cache_key_for_draws(prefix: str, game: str, limit: int, draws: list[dict[str, Any]]) -> str:
    latest = draws[0] if draws else {}
    return (
        f"{prefix}-{game}-{limit}-{latest.get('date', '')}-"
        f"{latest.get('period', '')}-{draw_fingerprint(draws)}"
    )


def _freeze_flagship_recommendation(
    game: str,
    latest: dict[str, Any],
    selected_limit: int,
    analysis: dict[str, Any],
    history: list[dict[str, Any]],
    recommendation_key: str = "flagshipRecommendation",
    snapshot_tag: str = "pick-5",
    profile_name_override: str | None = None,
) -> tuple[list[int], dict[str, Any]]:
    """Publish one flagship pool per draw/window so every visitor sees the same result."""
    snapshot_key = (
        f"{game}:{latest.get('date', '')}:{latest.get('period', '')}:"
        f"window-{selected_limit}:{snapshot_tag}"
    )
    if snapshot_key in flagship_snapshot_memory:
        numbers = list(flagship_snapshot_memory[snapshot_key])
        return numbers, {
            "key": snapshot_key,
            "status": "published",
            "profile": profile_name_override or "balanced",
            "source": "memory",
        }

    if database_ready:
        try:
            rows = database_query(
                "SELECT numbers_json, profile_name, history_fingerprint, created_at FROM analysis_snapshots WHERE snapshot_key = ?",
                (snapshot_key,),
            )
            if rows:
                numbers = json.loads(rows[0][0])
                if isinstance(numbers, list) and len(numbers) == 5:
                    numbers = [int(number) for number in numbers]
                    flagship_snapshot_memory[snapshot_key] = numbers
                    return numbers, {
                        "key": snapshot_key,
                        "status": "published",
                        "profile": profile_name_override or rows[0][1],
                        "historyFingerprint": rows[0][2],
                        "createdAt": rows[0][3],
                    }
        except Exception:
            pass

    numbers = [int(number) for number in (analysis.get(recommendation_key) or [])[:5]]
    if len(numbers) != 5:
        return numbers, {"key": snapshot_key, "status": "unavailable"}
    profile_name = profile_name_override or str(
        (analysis.get("patterns") or {}).get("selectedProfile", "balanced")
    )
    fingerprint = draw_fingerprint(history)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if database_ready:
        try:
            database_execute(
                """
                INSERT INTO analysis_snapshots
                    (snapshot_key, game, latest_period, latest_date, selected_limit,
                     numbers_json, profile_name, history_fingerprint, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (snapshot_key) DO NOTHING
                """,
                (
                    snapshot_key,
                    game,
                    str(latest.get("period", "")),
                    str(latest.get("date", "")),
                    int(selected_limit),
                    json.dumps(numbers),
                    profile_name,
                    fingerprint,
                    created_at,
                ),
            )
            rows = database_query(
                "SELECT numbers_json, profile_name, history_fingerprint, created_at FROM analysis_snapshots WHERE snapshot_key = ?",
                (snapshot_key,),
            )
            if rows:
                stored_numbers = json.loads(rows[0][0])
                if isinstance(stored_numbers, list) and len(stored_numbers) == 5:
                    numbers = [int(number) for number in stored_numbers]
                    profile_name = rows[0][1]
                    fingerprint = rows[0][2]
                    created_at = rows[0][3]
        except Exception:
            pass

    flagship_snapshot_memory[snapshot_key] = numbers
    return numbers, {
        "key": snapshot_key,
        "status": "published",
        "profile": profile_name,
        "historyFingerprint": fingerprint,
        "createdAt": created_at,
    }


def freeze_flagship_recommendation(
    game: str,
    latest: dict[str, Any],
    selected_limit: int,
    analysis: dict[str, Any],
    history: list[dict[str, Any]],
    recommendation_key: str = "flagshipRecommendation",
    snapshot_tag: str = "pick-5",
    profile_name_override: str | None = None,
) -> tuple[list[int], dict[str, Any]]:
    with flagship_snapshot_lock:
        return _freeze_flagship_recommendation(
            game,
            latest,
            selected_limit,
            analysis,
            history,
            recommendation_key=recommendation_key,
            snapshot_tag=snapshot_tag,
            profile_name_override=profile_name_override,
        )


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
    for window_size in (6, 10, 12, 18, 20, 24, 36, 60, 90):
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
        "label": "核心分析",
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
    "recent",
    "heat",
    "gap",
    "interval",
)
RESEARCH_FEATURE_LABELS = {
    "recent": "近期熱度",
    "heat": "長期熱度",
    "gap": "遺漏平衡",
    "interval": "區間分布",
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


def tail_analysis_summary(draws: list[dict[str, Any]], max_number: int = 39) -> dict[str, Any]:
    """Show a simple, independent tail summary without feeding the main model."""
    ordered = canonical_analysis_draws(draws)
    stats = number_stats(ordered, max_number)
    window_sizes = (10, 20, 36)

    def window_tail_counts(rows: list[dict[str, Any]]) -> tuple[dict[int, int], dict[int, int]]:
        counts = {tail: 0 for tail in range(10)}
        coverage = {tail: 0 for tail in range(10)}
        for draw in rows:
            seen = {number % 10 for number in draw["numbers"]}
            for tail in seen:
                coverage[tail] += 1
            for number in draw["numbers"]:
                counts[number % 10] += 1
        return counts, coverage

    def tail_gap(tail: int) -> int:
        return next(
            (
                index
                for index, draw in enumerate(ordered)
                if any(number % 10 == tail for number in draw["numbers"])
            ),
            len(ordered),
        )

    window_data: dict[int, dict[str, dict[int, int]]] = {}
    for size in window_sizes:
        rows = ordered[: min(size, len(ordered))]
        counts, coverage = window_tail_counts(rows)
        window_data[size] = {"counts": counts, "coverage": coverage}

    tail_rows: list[dict[str, Any]] = []
    for tail in range(10):
        count10 = window_data[10]["counts"][tail]
        count20 = window_data[20]["counts"][tail]
        count36 = window_data[36]["counts"][tail]
        size10 = max(1, min(10, len(ordered)))
        size20 = max(1, min(20, len(ordered)))
        size36 = max(1, min(36, len(ordered)))
        rate10 = count10 / (size10 * 5)
        rate20 = count20 / (size20 * 5)
        rate36 = count36 / (size36 * 5)
        gap = tail_gap(tail)
        raw_score = rate10 * 0.50 + rate20 * 0.30 + rate36 * 0.20
        if gap >= 4:
            raw_score *= 0.55
        tail_rows.append(
            {
                "tail": tail,
                "label": f"{tail}尾",
                "recent10": count10,
                "recent20": count20,
                "recent36": count36,
                "coverage10": window_data[10]["coverage"][tail],
                "coverage20": window_data[20]["coverage"][tail],
                "coverage36": window_data[36]["coverage"][tail],
                "gap": gap,
                "momentum": round((rate10 - rate36) * 100, 1),
                "rawScore": raw_score,
            }
        )

    ranked = sorted(tail_rows, key=lambda item: (-item["rawScore"], item["gap"], item["tail"]))
    recommended_tails = [item for item in ranked if item["gap"] < 4][:5]
    recommended_tail_set = {item["tail"] for item in recommended_tails}
    top_score = max((item["rawScore"] for item in ranked), default=0.0) or 1.0

    numbers_by_tail: dict[int, list[int]] = {tail: [] for tail in range(10)}
    for number in range(1, max_number + 1):
        numbers_by_tail[number % 10].append(number)
    number_candidates: list[tuple[float, int]] = []
    for item in recommended_tails:
        tail_score = item["rawScore"] / top_score
        numbers = sorted(
            numbers_by_tail[item["tail"]],
            key=lambda number: (
                -(
                    stats["windowFrequencies"].get("10", {}).get(number, 0) * 1.8
                    + stats["windowFrequencies"].get("20", {}).get(number, 0) * 0.75
                    + stats["windowFrequencies"].get("36", {}).get(number, 0) * 0.25
                    + (1 / (1 + stats["gaps"].get(number, len(ordered)))) * 2
                ),
                number,
            ),
        )
        numbers_by_tail[item["tail"]] = numbers
        for number in numbers:
            if stats["gaps"].get(number, len(ordered)) <= 25:
                number_score = (
                    stats["windowFrequencies"].get("10", {}).get(number, 0) * 1.8
                    + stats["windowFrequencies"].get("20", {}).get(number, 0) * 0.75
                    + stats["windowFrequencies"].get("36", {}).get(number, 0) * 0.25
                    + (1 / (1 + stats["gaps"].get(number, len(ordered)))) * 2
                )
                number_candidates.append((tail_score * 10 + number_score, number))

    recommendation: list[int] = []
    for item in recommended_tails:
        for number in numbers_by_tail[item["tail"]]:
            if number not in recommendation and stats["gaps"].get(number, len(ordered)) <= 25:
                recommendation.append(number)
                break
        if len(recommendation) == 5:
            break
    for _, number in sorted(number_candidates, key=lambda item: (-item[0], item[1])):
        if number not in recommendation:
            recommendation.append(number)
        if len(recommendation) == 5:
            break

    selected_tail_ranks = {item["tail"]: index for index, item in enumerate(ranked)}
    for item in tail_rows:
        item["score"] = round((item["rawScore"] / top_score) * 100, 1)
        if item["gap"] >= 4:
            item["status"] = "避開"
        elif item["tail"] in recommended_tail_set and selected_tail_ranks[item["tail"]] < 3:
            item["status"] = "優先"
        else:
            item["status"] = "觀察"
        item.pop("rawScore", None)
        item["numbers"] = [
            number
            for number in numbers_by_tail[item["tail"]]
            if stats["gaps"].get(number, len(ordered)) <= 25
        ][:4]

    tail_rows.sort(key=lambda item: (-item["score"], item["gap"], item["tail"]))
    return {
        "version": "獨立",
        "windows": [size for size in window_sizes if ordered[:size]],
        "rows": tail_rows,
        "recommendedTails": [item["tail"] for item in recommended_tails],
        "avoidTails": [item["tail"] for item in tail_rows if item["gap"] >= 4],
        "recommendation": sorted(recommendation[:5]),
        "method": "獨立尾數統計：近10期 50%・近20期 30%・近36期 20%；只供查看，不參與主推薦、旗艦版或自適應集成。",
        "note": "尾數只反映歷史分布與近期動能，不代表下一期必然開出；彩券每期仍是隨機事件。",
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
        components = simple_core_score_components(training, max_number)
        actual = set(target["numbers"])
        for feature in RESEARCH_FEATURE_KEYS:
            ranked = sorted(
                range(1, max_number + 1),
                key=lambda number: (-components[number].get(feature, 0.0), number),
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


CORE_FEATURE_ORDER = ("recent", "heat", "gap", "interval")
CORE_BASE_WEIGHTS = {
    "recent": 0.45,
    "heat": 0.20,
    "gap": 0.15,
    "interval": 0.20,
}
ADAPTIVE_PATTERN_VERSION = "dynamic-v1"
CORE_ANALYSIS_METHOD = "核心基準：近期熱度 45%・長期熱度 20%・遺漏平衡 15%・區間分布 20%"


def normalize_core_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    """Keep the adaptive layer bounded and deterministic."""
    source = weights or CORE_BASE_WEIGHTS
    values = {
        key: max(0.0, float(source.get(key, CORE_BASE_WEIGHTS[key])))
        for key in CORE_FEATURE_ORDER
    }
    total = sum(values.values()) or 1.0
    return {key: values[key] / total for key in CORE_FEATURE_ORDER}


def adaptive_core_weights(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    evaluation_limit: int = 36,
    training_limit: int = 90,
) -> dict[str, Any]:
    """Calibrate the four core signals with walk-forward recent evidence.

    Each target draw is scored only from older draws.  The result is gently
    shrunk toward the explainable baseline so a small lucky streak cannot
    replace the whole model.
    """
    ordered = canonical_analysis_draws(draws)
    base_weights = normalize_core_weights()
    target_limit = min(max(0, int(evaluation_limit)), max(0, len(ordered) - 20))
    samples: dict[str, list[dict[str, float]]] = {key: [] for key in CORE_FEATURE_ORDER}

    for target_index in range(target_limit):
        target = ordered[target_index]
        training = ordered[target_index + 1 : target_index + 1 + training_limit]
        if len(training) < 20:
            continue
        components = simple_core_score_components(training, max_number)
        recency_weight = 0.50 if target_index < 10 else 0.30 if target_index < 20 else 0.20
        actual = set(target["numbers"])
        for feature in CORE_FEATURE_ORDER:
            ranked = sorted(
                range(1, max_number + 1),
                key=lambda number: (-components[number].get(feature, 0.0), number),
            )
            pick = ranked[: min(pick_count, max_number)]
            hits = len(actual & set(pick))
            samples[feature].append(
                {
                    "index": float(target_index),
                    "weight": recency_weight,
                    "hits": float(hits),
                    "twoPlus": 1.0 if hits >= 2 else 0.0,
                }
            )

    def metric(rows: list[dict[str, float]]) -> tuple[float, float, float]:
        total_weight = sum(row["weight"] for row in rows)
        if not rows or not total_weight:
            return 0.0, 0.0, 0.0
        average_hit = sum(row["hits"] * row["weight"] for row in rows) / total_weight
        two_plus_rate = sum(row["twoPlus"] * row["weight"] for row in rows) / total_weight
        quality = average_hit + two_plus_rate * 0.35
        return quality, average_hit, two_plus_rate

    quality_rows: dict[str, float] = {}
    for feature in CORE_FEATURE_ORDER:
        quality_rows[feature] = metric(samples[feature])[0]
    quality_reference = sum(quality_rows.values()) / len(CORE_FEATURE_ORDER) if quality_rows else 0.0

    raw_weights: dict[str, float] = {}
    components: list[dict[str, Any]] = []
    for feature in CORE_FEATURE_ORDER:
        rows = samples[feature]
        quality, average_hit, two_plus_rate = metric(rows)
        recent_quality = metric([row for row in rows if row["index"] < 10])[0]
        older_quality = metric([row for row in rows if row["index"] >= 10])[0]
        if older_quality:
            stability = max(
                0.0,
                1.0 - abs(recent_quality - older_quality) / max(0.65, abs(quality) + 0.65),
            )
        else:
            stability = 0.75
        sample_shrink = len(rows) / (len(rows) + 18) if rows else 0.0
        relative_lift = (quality - quality_reference) / max(0.65, abs(quality_reference))
        multiplier = 1.0 + max(
            -0.22,
            min(0.22, relative_lift * 0.55 * sample_shrink * (0.70 + stability * 0.30)),
        )
        raw_weights[feature] = base_weights[feature] * multiplier
        components.append(
            {
                "id": feature,
                "label": RESEARCH_FEATURE_LABELS.get(feature, feature),
                "baseWeight": round(base_weights[feature] * 100),
                "multiplier": round(multiplier, 3),
                "averageHit": round(average_hit, 2),
                "twoPlusRate": round(two_plus_rate * 100, 1),
                "testedCount": len(rows),
                "stability": round(stability * 100, 1),
            }
        )

    weights = normalize_core_weights(raw_weights)
    for item in components:
        item["weight"] = round(weights[item["id"]] * 100)
        item["delta"] = item["weight"] - item["baseWeight"]
    components.sort(key=lambda item: (-item["weight"], item["id"]))
    tested_count = max((len(rows) for rows in samples.values()), default=0)
    leader = max(components, key=lambda item: (item["delta"], item["weight"]), default=None)
    meaningful_change = bool(leader and leader["delta"] >= 2 and tested_count >= 8)
    selected_label = leader["label"] if meaningful_change and leader else "綜合平衡"
    weight_text = "・".join(f"{item['label']} {item['weight']}%" for item in components)
    if meaningful_change and leader:
        reason = (
            f"{leader['label']}在近 {tested_count} 次逐期回測的平均命中 "
            f"{leader['averageHit']:.2f}、2 中以上 {leader['twoPlusRate']:.1f}%，本期提高權重。"
        )
    else:
        reason = "各訊號近期表現差距不大，維持平衡權重，避免追逐短期波動。"
    method = f"近期動態版路：{selected_label}；{weight_text}。"
    return {
        "version": ADAPTIVE_PATTERN_VERSION,
        "selected": leader["id"] if meaningful_change and leader else "balanced",
        "selectedLabel": selected_label,
        "weights": weights,
        "components": components,
        "testedCount": tested_count,
        "evaluationLimit": target_limit,
        "method": method,
        "reason": reason,
        "note": "每逢新一期資料進來才重新校準；同一期不反覆改寫推薦。回測只作權重參考，不代表預測或保證中獎。",
    }


def simple_core_score_components(
    draws: list[dict[str, Any]],
    max_number: int = 39,
) -> dict[int, dict[str, float]]:
    """Build the small, explainable score used by every published pick."""
    ordered = canonical_analysis_draws(draws)
    stats = number_stats(ordered, max_number)
    windows = ((10, 0.50), (20, 0.30), (36, 0.20))
    recent_raw = {number: 0.0 for number in range(1, max_number + 1)}
    for size, weight in windows:
        rows = ordered[: min(size, len(ordered))]
        if not rows:
            continue
        denominator = max(1, len(rows) * 5)
        counts = stats["windowFrequencies"].get(str(size), {})
        for number in recent_raw:
            recent_raw[number] += weight * counts.get(number, 0) / denominator

    def normalize(values: dict[int, float]) -> dict[int, float]:
        highest = max(values.values(), default=0.0)
        return {number: (value / highest if highest else 0.0) for number, value in values.items()}

    recent = normalize(recent_raw)
    long_term = normalize({
        number: stats["frequency"].get(number, 0) / max(1, len(ordered) * 5)
        for number in range(1, max_number + 1)
    })
    gap_balance = {
        number: max(0.0, 1.0 - abs(stats["gaps"].get(number, len(ordered)) - 8) / 20)
        for number in range(1, max_number + 1)
    }
    for number, gap in stats["gaps"].items():
        if gap > 25:
            gap_balance[number] *= 0.25

    recent_rows = ordered[: min(36, len(ordered))]
    zone_counts = [0, 0, 0, 0]
    for draw in recent_rows:
        for number in draw["numbers"]:
            zone_counts[min(3, (number - 1) // 10)] += 1
    max_zone = max(zone_counts, default=0) or 1
    zone = {
        number: zone_counts[min(3, (number - 1) // 10)] / max_zone
        for number in range(1, max_number + 1)
    }
    return {
        number: {
            "recent": round(recent[number], 6),
            "heat": round(long_term[number], 6),
            "gap": round(gap_balance[number], 6),
            "interval": round(zone[number], 6),
        }
        for number in range(1, max_number + 1)
    }


def simple_core_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    core_weights: dict[str, float] | None = None,
) -> list[int]:
    """Return one deterministic recommendation from the four core signals."""
    components = simple_core_score_components(draws, max_number)
    weights = normalize_core_weights(core_weights)
    scores = {
        number: sum(values[key] * weight for key, weight in weights.items())
        for number, values in components.items()
    }
    pool = sorted(scores, key=lambda number: (-scores[number], number))[: min(18, max_number)]
    if len(pool) <= pick_count:
        return sorted(pool)
    best_combo: tuple[int, ...] | None = None
    best_score = float("-inf")
    for combo in itertools.combinations(pool, pick_count):
        sorted_combo = tuple(sorted(combo))
        number_score = sum(scores[number] for number in sorted_combo) / pick_count
        shape_score = combo_spread_score(list(sorted_combo), max_number)
        combo_score = number_score * 0.85 + shape_score * 0.15
        if combo_score > best_score or (
            combo_score == best_score and (best_combo is None or sorted_combo < best_combo)
        ):
            best_score = combo_score
            best_combo = sorted_combo
    return list(best_combo or tuple(pool[:pick_count]))


def simple_core_candidate_pool(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    candidate_count: int = 15,
    core_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Return a deterministic ranked pool built from the same core signals.

    The primary five-number pick is always kept inside the pool so members can
    make their own combination without introducing a second, conflicting model.
    Scores are ranking indexes, not winning probabilities.
    """
    components = simple_core_score_components(draws, max_number)
    weights = normalize_core_weights(core_weights)
    scores = {
        number: sum(values[key] * weight for key, weight in weights.items())
        for number, values in components.items()
    }
    ranked = sorted(scores, key=lambda number: (-scores[number], number))
    target = max(1, min(int(candidate_count), max_number))
    core_pick = set(
        simple_core_recommendation(
            draws,
            max_number=max_number,
            pick_count=pick_count,
            core_weights=weights,
        )
    )
    selected = set(core_pick)
    for number in ranked:
        if len(selected) >= target:
            break
        selected.add(number)
    ordered = sorted(selected, key=lambda number: (-scores[number], number))
    return [
        {
            "rank": index + 1,
            "number": number,
            "score": round(scores[number] * 100, 1),
            "isCorePick": number in core_pick,
        }
        for index, number in enumerate(ordered[:target])
    ]


def model_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    seed_label: str = "",
    profile_name: str = "balanced",
    candidate_budget: int | None = None,
    evidence: dict[str, float] | None = None,
    core_weights: dict[str, float] | None = None,
) -> list[int]:
    return simple_core_recommendation(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        core_weights=core_weights,
    )


def flagship_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    profile_name: str = "balanced",
    evidence: dict[str, float] | None = None,
    backtest: dict[str, Any] | None = None,
    core_weights: dict[str, float] | None = None,
) -> list[int]:
    """Compatibility wrapper: flagship uses the same explainable core pick."""
    return simple_core_recommendation(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        core_weights=core_weights,
    )

    # Kept below for old snapshots only; new requests never run this legacy
    # six-signal branch.
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    model = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["balanced"])
    current_profile = pattern_profile(ordered, max_number)

    def normalize(values: dict[int, float]) -> dict[int, float]:
        if not values:
            return {number: 0.5 for number in range(1, max_number + 1)}
        low = min(values.values())
        high = max(values.values())
        if high <= low:
            return {number: 0.5 for number in values}
        return {number: (value - low) / (high - low) for number, value in values.items()}

    # 1) Recent hot numbers: the short windows get explicit votes so a fresh
    # cluster can move the flagship result without discarding the full sample.
    recent_raw = {number: 0.0 for number in range(1, max_number + 1)}
    recent_weights = ((10, 0.50), (20, 0.30), (36, 0.20))
    for window_size, window_weight in recent_weights:
        rows = ordered[:window_size]
        if not rows:
            continue
        counts = number_stats(rows, max_number)["frequency"]
        max_count = max(counts.values()) or 1
        for number in recent_raw:
            recent_raw[number] += window_weight * safe_divide(counts[number], max_count)
    recent_scores = normalize(recent_raw)

    # 2) Interval concentration: reward numbers in the strongest recent
    # 1-15 / 10-20 / ... interval bands, while keeping existing overlap data.
    interval_focus = current_profile["intervalFocusCounts"]
    interval_hits = current_profile["intervalHitCounts"]
    max_focus = max(interval_focus.values()) or 1
    max_hits = max(interval_hits.values()) or 1
    interval_strengths = {
        window: safe_divide(interval_focus.get(window, 0), max_focus) * 0.70
        + safe_divide(interval_hits.get(window, 0), max_hits) * 0.30
        for window in interval_windows(max_number)
    }
    strongest_intervals = sorted(
        interval_strengths,
        key=lambda window: (-interval_strengths[window], window[0], window[1]),
    )[:3]
    interval_raw = {
        number: current_profile["numberScores"][number].get("interval", 0.0) * 0.30
        for number in range(1, max_number + 1)
    }
    for rank, window in enumerate(strongest_intervals):
        rank_weight = max(0.55, 1.0 - rank * 0.18)
        start, end = window
        for number in range(start, end + 1):
            interval_raw[number] += interval_strengths[window] * rank_weight
    interval_scores = normalize(interval_raw)

    # 3) Walk-forward backtest support: only numbers selected by historical
    # training windows contribute, so the target draw never leaks into the pick.
    backtest_support = (backtest or {}).get("numberSupport", {})
    backtest_raw = {
        number: float(backtest_support.get(str(number), backtest_support.get(number, 0.0)))
        for number in range(1, max_number + 1)
    }
    backtest_scores = normalize(backtest_raw)

    # 4) Pattern signals: pair/repeat, neighbours, and multi-window agreement
    # form the版路 component; interval, drag cards, and tails stay separate so
    # the flagship explanation matches the actual scoring model.
    pattern_keys = (
        "pair",
        "repeatSignal",
        "neighbor",
        "multiWindow",
        "streak",
        "momentum",
    )
    pattern_raw = {}
    for number in range(1, max_number + 1):
        features = current_profile["numberScores"][number]
        pattern_raw[number] = sum(
            features.get(key, 0.0) * (float((evidence or {}).get(key, 1.0)) if evidence else 1.0)
            for key in pattern_keys
        ) / len(pattern_keys)
    pattern_scores = normalize(pattern_raw)

    # 5) Drag-card support: numbers that historically followed the latest
    # draw's numbers. Repeat support is a small stabilizer when drag samples
    # are sparse, but it never replaces the direct drag signal.
    drag_raw = {}
    for number in range(1, max_number + 1):
        features = current_profile["numberScores"][number]
        drag_raw[number] = (
            features.get("drag", 0.0)
            * (float((evidence or {}).get("drag", 1.0)) if evidence else 1.0)
            * 0.72
            + features.get("repeatSignal", 0.0)
            * (float((evidence or {}).get("repeatSignal", 1.0)) if evidence else 1.0)
            * 0.28
        )
    drag_scores = normalize(drag_raw)

    # 6) Tail support: combine recent tail heat with tail momentum so a hot
    # tail can help without allowing one crowded ending to dominate the pool.
    tail_raw = {}
    for number in range(1, max_number + 1):
        features = current_profile["numberScores"][number]
        tail_raw[number] = (
            features.get("tail", 0.0)
            * (float((evidence or {}).get("tail", 1.0)) if evidence else 1.0)
            * 0.62
            + features.get("tailMomentum", 0.0)
            * (float((evidence or {}).get("tailMomentum", 1.0)) if evidence else 1.0)
            * 0.38
        )
    tail_scores = normalize(tail_raw)

    component_scores = {
        number: recent_scores[number] * 0.26
        + interval_scores[number] * 0.20
        + backtest_scores[number] * 0.18
        + pattern_scores[number] * 0.16
        + drag_scores[number] * 0.10
        + tail_scores[number] * 0.10
        for number in range(1, max_number + 1)
    }
    candidate_pool = sorted(
        component_scores,
        key=lambda number: (-component_scores[number], number),
    )[: min(18, max_number)]
    if len(candidate_pool) <= pick_count:
        return sorted(candidate_pool)

    # Select the best five-number shape from the top pool.  The small search is
    # deterministic and lets the interval/pattern evidence affect the group,
    # not only each number independently.
    best_combo: tuple[int, ...] | None = None
    best_score = float("-inf")
    for combo in itertools.combinations(candidate_pool, pick_count):
        sorted_combo = tuple(sorted(combo))
        combo_score = sum(component_scores[number] for number in sorted_combo) / pick_count
        combo_score = combo_score * 0.76 + combo_pattern_score(
            list(sorted_combo), current_profile, model, max_number
        ) * 0.24
        if combo_score > best_score or (
            combo_score == best_score and (best_combo is None or sorted_combo < best_combo)
        ):
            best_score = combo_score
            best_combo = sorted_combo
    return list(best_combo or tuple(candidate_pool[:pick_count]))


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
    for _ in range(candidate_budget or 140):
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
    core_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Show the same core model over the three short windows."""
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    views = []
    weights = {10: 0.50, 20: 0.30, 36: 0.20}
    for window in SHORT_TERM_WINDOWS:
        rows = ordered[:window]
        if len(rows) < 5:
            continue
        components = simple_core_score_components(rows, max_number)
        weights_for_number = normalize_core_weights(core_weights)
        scores = {
            number: sum(values[key] * weight for key, weight in weights_for_number.items())
            for number, values in components.items()
        }
        ranked = sorted(scores, key=lambda number: (-scores[number], number))
        leaders = ranked[: min(8, max_number)]
        views.append(
            {
                "window": window,
                "drawCount": len(rows),
                "leaders": leaders,
                "recommendation": simple_core_recommendation(
                    rows,
                    max_number=max_number,
                    pick_count=pick_count,
                    core_weights=weights_for_number,
                ),
            }
        )
    if not views:
        return {"windows": [], "leaders": [], "recommendations": []}
    weighted_votes: dict[int, float] = {n: 0.0 for n in range(1, max_number + 1)}
    for view in views:
        window_weight = weights.get(view["window"], 0.0)
        for rank, number in enumerate(view["leaders"]):
            weighted_votes[number] += window_weight * (1.0 - rank / max(8, len(view["leaders"])))
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


def recent_flagship_selection(
    base_numbers: list[int],
    patterns: dict[str, Any],
    consensus: dict[str, Any],
    max_number: int = 39,
    pick_count: int = 5,
) -> list[int]:
    """Blend the existing model with the 10/20/36-period recent consensus."""
    scores = {number: 0.0 for number in range(1, max_number + 1)}
    for item in consensus.get("leaders", []):
        number = int(item.get("number", 0))
        if number in scores:
            scores[number] += float(item.get("score", 0)) + int(item.get("agreement", 0)) * 16
    for view in consensus.get("windows", []):
        for rank, number in enumerate(view.get("leaders", [])[:8]):
            if number in scores:
                scores[number] += max(0, 8 - rank) * 2.2
        for rank, number in enumerate(view.get("recommendation", [])[:6]):
            if number in scores:
                scores[number] += max(0, 6 - rank) * 3.2
    for item in patterns.get("signalLeaders", [])[:8]:
        number = int(item.get("number", 0))
        if number in scores:
            scores[number] += float(item.get("score", 0)) * 0.5 + int(item.get("support", 0)) * 5
    for number in base_numbers:
        if number in scores:
            scores[number] += 14
    ranked = sorted(scores, key=lambda number: (-scores[number], number))
    return ranked[: min(pick_count, max_number)]


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
    candidate_budget = 180 if requested_limit <= 35 else 120 if requested_limit <= 90 else 80 if requested_limit <= 180 else 50
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
    number_support: dict[int, float] = {number: 0.0 for number in range(1, max_number + 1)}
    for row in rows:
        pick = row.get("pick") or []
        actual = set(row.get("actual") or [])
        for rank, number in enumerate(pick[:pick_count]):
            # A historical pick that also appeared in the target gets the
            # strongest support; non-hit selections retain a small signal so
            # a number is not discarded only because of one miss.
            rank_weight = 1.0 - (rank / max(1, pick_count))
            hit_weight = 1.0 if number in actual else 0.25
            number_support[int(number)] += rank_weight * hit_weight
    support_scale = max(number_support.values()) or 1.0
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
        "numberSupport": {
            str(number): round(value / support_scale, 5)
            for number, value in number_support.items()
        },
        "recentRows": rows[:10],
        "method": f"每一期只用該期以前的歷史資料產生推薦，再與實際開獎比對；只驗證核心四訊號，不用回測結果反覆改寫選號。",
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
    selected = "balanced"
    backtest = rolling_backtest(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        profile_name=selected,
        backtest_limit=backtest_limit,
    )
    results = [{
        "id": selected,
        "label": "核心分析",
        "quality": round(model_quality(backtest), 2),
        "averageHit": backtest["averageHit"],
        "onePlusRate": backtest["onePlusRate"],
        "twoPlusRate": backtest["twoPlusRate"],
        "threePlusRate": backtest["threePlusRate"],
        "bestHit": backtest["bestHit"],
        "testedCount": backtest["testedCount"],
        "recentAverageHit": backtest["recentAverageHit"],
        "stability": backtest["stability"],
        "validationCount": backtest["testedCount"],
        "validationAverageHit": backtest["averageHit"],
        "validationTwoPlusRate": backtest["twoPlusRate"],
        "validationThreePlusRate": backtest["threePlusRate"],
    }]
    return selected, backtest, results


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
    draws = canonical_analysis_draws(draws)
    reference_draws = canonical_analysis_draws(reference_draws or draws)
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
    seed_label = stable_analysis_seed(draws, f"analysis-window-{len(draws)}")
    selected_profile, backtest, model_results = choose_model_profile(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        backtest_limit=backtest_limit,
    )
    adaptive_pattern = adaptive_core_weights(
        reference_draws,
        max_number=max_number,
        pick_count=pick_count,
        evaluation_limit=min(36, max(0, len(reference_draws) - 20)),
    )
    core_weights = adaptive_pattern["weights"]
    research_evidence = research_feature_evidence(reference_draws, max_number=max_number)
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
        core_weights=core_weights,
    )
    # All published tiers use the same deterministic core pick.  Pro adds
    # validation and history; the flagship tier adds presentation and saved
    # reasoning, rather than a second competing algorithm.
    flagship_numbers = simple_core_recommendation(
        draws,
        max_number=max_number,
        pick_count=5,
        core_weights=core_weights,
    )
    adaptive_numbers = list(flagship_numbers)
    core_candidate_pool = simple_core_candidate_pool(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        candidate_count=15,
        core_weights=core_weights,
    )
    patterns = pattern_summary(draws, max_number, selected_profile)
    patterns["adaptiveRecent"] = adaptive_pattern
    short_consensus = short_term_consensus(
        reference_draws,
        max_number=max_number,
        pick_count=pick_count,
        profile_name=selected_profile,
        core_weights=core_weights,
    )
    tail_analysis = tail_analysis_summary(draws, max_number)

    return {
        "drawCount": len(draws),
        "hot": [{"number": n, "count": frequency[n]} for n in hot],
        "cold": [{"number": n, "count": frequency[n]} for n in cold],
        "overdue": [{"number": n, "gap": gaps[n]} for n in overdue],
        "frequency": [{"number": n, "count": frequency[n], "gap": gaps[n]} for n in frequency],
        "recommendation": recommendation,
        "coreCandidatePool": core_candidate_pool,
        "coreCandidateMethod": "同一套核心分析排序；15 碼是會員自選候選池，不代表 15 碼同時推薦或保證中獎。",
        "flagshipRecommendation": flagship_numbers,
        "adaptiveRecommendation": adaptive_numbers,
        "adaptiveMethod": adaptive_pattern["method"],
        "adaptiveRecentPattern": adaptive_pattern,
        "flagshipMethod": adaptive_pattern["method"],
        "flagshipComponents": adaptive_pattern["components"],
        "backtest": backtest,
        "modelProfiles": model_results,
        "patterns": patterns,
        "tailAnalysis": tail_analysis,
        "researchEvidence": research_evidence,
        "shortTermConsensus": short_consensus,
        "note": f"主模型保留四個容易理解的訊號：近期熱度、長期熱度、遺漏平衡與區間分布；會依近 {adaptive_pattern['testedCount']} 次逐期回測平滑校準權重，新一期資料進來後才重新計算，同一期不反覆改寫推薦。彩券每期仍是隨機事件，不代表可預測或保證中獎。",
    }


def analyze_with_stable_backtest(
    draws: list[dict[str, Any]],
    backtest_draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    backtest_limit: int = BACKTEST_DEFAULT_LIMIT,
    recommendation_draws: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    draws = canonical_analysis_draws(draws)
    backtest_draws = canonical_analysis_draws(backtest_draws)
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

    display_draws = canonical_analysis_draws(recommendation_draws or fallback_draws)
    research_evidence = research_feature_evidence(fallback_draws, max_number=max_number)
    evidence_map = {
        item["id"]: item["multiplier"] for item in research_evidence.get("features", [])
    }
    adaptive_pattern = adaptive_core_weights(
        fallback_draws,
        max_number=max_number,
        pick_count=pick_count,
        evaluation_limit=min(36, max(0, len(fallback_draws) - 20)),
    )
    core_weights = adaptive_pattern["weights"]
    analysis["backtest"] = fallback_backtest
    analysis["modelProfiles"] = model_results
    analysis["researchEvidence"] = research_evidence
    analysis["tailAnalysis"] = tail_analysis_summary(display_draws, max_number)
    analysis["recommendation"] = model_recommendation(
        display_draws,
        max_number=max_number,
        pick_count=pick_count,
        seed_label=stable_analysis_seed(fallback_draws, f"fallback-window-{len(draws)}-backtest-{requested_limit}"),
        profile_name=selected_profile,
        evidence=evidence_map,
        core_weights=core_weights,
    )
    analysis["flagshipRecommendation"] = simple_core_recommendation(
        display_draws,
        max_number=max_number,
        pick_count=5,
        core_weights=core_weights,
    )
    analysis["coreCandidatePool"] = simple_core_candidate_pool(
        display_draws,
        max_number=max_number,
        pick_count=pick_count,
        candidate_count=15,
        core_weights=core_weights,
    )
    analysis["coreCandidateMethod"] = "同一套核心分析排序；15 碼是會員自選候選池，不代表 15 碼同時推薦或保證中獎。"
    analysis["adaptiveRecommendation"] = list(analysis["flagshipRecommendation"])
    analysis["adaptiveMethod"] = adaptive_pattern["method"]
    analysis["adaptiveRecentPattern"] = adaptive_pattern
    analysis["flagshipMethod"] = adaptive_pattern["method"]
    analysis["flagshipComponents"] = adaptive_pattern["components"]
    analysis["shortTermConsensus"] = short_term_consensus(
        display_draws,
        max_number=max_number,
        pick_count=pick_count,
        profile_name=selected_profile,
        core_weights=core_weights,
    )
    analysis["patterns"]["adaptiveRecent"] = adaptive_pattern
    analysis["patterns"]["selectedProfile"] = selected_profile
    analysis["patterns"]["selectedLabel"] = MODEL_PROFILES.get(selected_profile, MODEL_PROFILES["balanced"])["label"]
    analysis["backtest"]["method"] = (
        f"目前選擇近 {len(draws)} 期，短期樣本不足以單獨回測；"
        f"模型回測已自動改用近 {len(fallback_draws)} 期穩定樣本。"
        f"{fallback_backtest.get('method', '')}"
    )
    analysis["note"] = (
        f"主模型保留四個容易理解的訊號，會依近 {adaptive_pattern['testedCount']} 次逐期回測平滑校準權重；"
        "新一期資料進來後才重新計算，同一期不反覆改寫推薦。彩券每期仍是隨機事件，不代表可預測或保證中獎。"
    )
    return analysis


def attach_flagship_analysis(
    game: str,
    latest: dict[str, Any],
    flagship_limit: int,
    history: list[dict[str, Any]],
    analysis: dict[str, Any],
    flagship_analysis: dict[str, Any],
) -> dict[str, Any]:
    flagship_numbers, snapshot = freeze_flagship_recommendation(
        game,
        latest,
        flagship_limit,
        flagship_analysis,
        history,
    )
    adaptive_numbers, adaptive_snapshot = freeze_flagship_recommendation(
        game,
        latest,
        flagship_limit,
        flagship_analysis,
        history,
        recommendation_key="adaptiveRecommendation",
        snapshot_tag="adaptive-pick-5",
        profile_name_override="adaptive",
    )
    adaptive_fallback = False
    if len(adaptive_numbers) != 5:
        # 舊快取或短暫資料不足時，仍發布一組穩定的五碼，避免所有訪客一直看到
        # 「資料累積中」。下一次新資料進來後，快取鍵會更新並重新校準自適應模型。
        fallback_numbers = [int(number) for number in (flagship_analysis.get("recommendation") or [])[:5]]
        if len(fallback_numbers) != 5:
            fallback_numbers = [int(number) for number in (flagship_numbers or [])[:5]]
        if len(fallback_numbers) == 5:
            fallback_analysis = dict(flagship_analysis)
            fallback_analysis["adaptiveRecommendation"] = fallback_numbers
            adaptive_numbers, adaptive_snapshot = freeze_flagship_recommendation(
                game,
                latest,
                flagship_limit,
                fallback_analysis,
                history,
                recommendation_key="adaptiveRecommendation",
                snapshot_tag="adaptive-pick-5",
                profile_name_override="adaptive",
            )
            adaptive_fallback = True
    result = dict(analysis)
    result["flagshipRecommendation"] = flagship_numbers
    result["flagshipSnapshot"] = snapshot
    result["flagshipAnalysisLimit"] = flagship_limit
    result["adaptiveRecommendation"] = adaptive_numbers
    result["adaptiveSnapshot"] = adaptive_snapshot
    result["adaptiveFallback"] = adaptive_fallback
    result["adaptiveMethod"] = flagship_analysis.get(
        "adaptiveMethod",
        "自適應集成：熱度、近期、趨勢、遺漏、版路、拖牌、連莊、區間與尾數動能加權",
    )
    if adaptive_fallback:
        result["adaptiveMethod"] += "；資料同步期間先沿用穩定綜合候選"
    result["flagshipProfile"] = (flagship_analysis.get("patterns") or {}).get("selectedProfile", "balanced")
    result["flagshipResearchEvidence"] = flagship_analysis.get("researchEvidence", {})
    history_analysis = dict(flagship_analysis)
    history_analysis["flagshipRecommendation"] = flagship_numbers
    history_analysis["adaptiveRecommendation"] = adaptive_numbers
    persist_flagship_analysis_history(
        game,
        latest,
        flagship_limit,
        history,
        history_analysis,
        snapshot,
    )
    return result


def build_payload(
    game: str,
    limit: int,
    backtest_limit: int = BACKTEST_DEFAULT_LIMIT,
    flagship_limit: int | None = None,
) -> dict[str, Any]:
    requested_backtest_limit = max(BACKTEST_MIN_LIMIT, min(BACKTEST_MAX_LIMIT, int(backtest_limit)))
    requested_flagship_limit = max(10, min(BACKTEST_MAX_LIMIT, int(flagship_limit if flagship_limit is not None else limit)))
    fetch_limit = min(5000, max(limit, requested_flagship_limit, requested_backtest_limit + 90, BACKTEST_FALLBACK_LIMIT))
    if game == "tw539":
        latest = taiwan_latest()
        history = canonical_analysis_draws([latest, *taiwan_history(fetch_limit)])
        persist_draw_history([latest, *history])
        draws = history[:limit]
        analysis_key = f"{cache_key_for_draws('analysis', game, fetch_limit, history)}-selected-{limit}-backtest-{requested_backtest_limit}-{ADAPTIVE_PATTERN_VERSION}"
        analysis = dict(cached(analysis_key, lambda: analyze_with_stable_backtest(draws, history, backtest_limit=requested_backtest_limit)))
        flagship_draws = history[:requested_flagship_limit]
        flagship_key = f"{cache_key_for_draws('flagship-analysis', game, requested_flagship_limit, history)}-backtest-{BACKTEST_DEFAULT_LIMIT}-{ADAPTIVE_PATTERN_VERSION}"
        flagship_analysis = analysis if (
            requested_flagship_limit == limit and requested_backtest_limit == BACKTEST_DEFAULT_LIMIT
        ) else cached(
            flagship_key,
            lambda: analyze_with_stable_backtest(
                flagship_draws,
                history,
                backtest_limit=BACKTEST_DEFAULT_LIMIT,
                recommendation_draws=flagship_draws,
            ),
        )
        analysis = attach_flagship_analysis(game, latest, requested_flagship_limit, history, analysis, flagship_analysis)
        return {
            "latest": public_draw(latest),
            "history": public_draws(draws),
            "flagshipLimit": requested_flagship_limit,
            "analysis": analysis,
        }
    if game == "ca-fantasy5":
        history = canonical_analysis_draws(california_history(fetch_limit))
        if not history:
            raise RuntimeError("加州天天樂資料頁目前沒有可解析的開獎資料")
        persist_draw_history(history)
        draws = history[:limit]
        analysis_key = f"{cache_key_for_draws('analysis', game, fetch_limit, history)}-selected-{limit}-backtest-{requested_backtest_limit}-{ADAPTIVE_PATTERN_VERSION}"
        latest = history[0]
        analysis = dict(cached(analysis_key, lambda: analyze_with_stable_backtest(draws, history, backtest_limit=requested_backtest_limit)))
        flagship_draws = history[:requested_flagship_limit]
        flagship_key = f"{cache_key_for_draws('flagship-analysis', game, requested_flagship_limit, history)}-backtest-{BACKTEST_DEFAULT_LIMIT}-{ADAPTIVE_PATTERN_VERSION}"
        flagship_analysis = analysis if (
            requested_flagship_limit == limit and requested_backtest_limit == BACKTEST_DEFAULT_LIMIT
        ) else cached(
            flagship_key,
            lambda: analyze_with_stable_backtest(
                flagship_draws,
                history,
                backtest_limit=BACKTEST_DEFAULT_LIMIT,
                recommendation_draws=flagship_draws,
            ),
        )
        analysis = attach_flagship_analysis(game, latest, requested_flagship_limit, history, analysis, flagship_analysis)
        return {
            "latest": public_draw(latest),
            "history": public_draws(draws),
            "flagshipLimit": requested_flagship_limit,
            "analysis": analysis,
        }
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
                                "features": ["120-365 期進階分析", "跨年歷史查詢", "核心模型回測", "簡潔版路摘要"],
                            },
                            {
                                "id": "flagship",
                                "name": "摘星狙擊手｜量化旗艦版",
                                "price": "高階會員",
                                "paymentLink": STRIPE_FLAGSHIP_PAYMENT_LINK,
                                "features": ["每期核心摘星 5 碼", "四項邏輯完整說明", "固定結果與分析紀錄", "回測驗證與命中追蹤"],
                            },
                        ],
                        "flagshipPaymentLink": STRIPE_FLAGSHIP_PAYMENT_LINK,
                    },
                    "notifications": {
                        "supported": bool(PUSH_PUBLIC_KEY),
                        "serverReady": push_server_ready(),
                        "autoNotify": AUTO_NOTIFY_ENABLED,
                        "autoNotifyIntervalSeconds": max(30, AUTO_NOTIFY_INTERVAL_SECONDS),
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
        if parsed.path == "/api/latest":
            params = parse_qs(parsed.query)
            try:
                game = clean_game(params.get("game", ["tw539"])[0])
                if game == "tw539":
                    latest = taiwan_latest()
                elif game == "ca-fantasy5":
                    history = california_history(1)
                    if not history:
                        raise RuntimeError("加州天天樂資料頁目前沒有可解析的最新開獎資料")
                    latest = history[0]
                else:
                    raise ValueError("不支援的遊戲種類")
                self.send_json(
                    {
                        "ok": True,
                        "latest": public_draw(latest),
                        "updatedAt": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
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
                flagship_limit = clamp_int(
                    params.get("flagshipLimit", [str(limit)])[0],
                    limit,
                    10,
                    BACKTEST_MAX_LIMIT,
                )
                payload = build_payload(
                    game,
                    limit,
                    backtest_limit=backtest_limit,
                    flagship_limit=flagship_limit,
                )
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **payload})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/flagship-history":
            params = parse_qs(parsed.query)
            try:
                game = clean_game(params.get("game", ["tw539"])[0])
                limit = clamp_int(params.get("limit", ["30"])[0], 30, 1, 100)
                if game not in ALLOWED_GAMES:
                    raise ValueError("不支援的遊戲種類")
                self.send_json(
                    {
                        "ok": True,
                        "game": game,
                        "history": load_flagship_analysis_history(game, limit),
                    }
                )
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
        print(f"auto notify enabled every {max(30, AUTO_NOTIFY_INTERVAL_SECONDS)}s for {', '.join(AUTO_NOTIFY_GAMES) or 'no games'}")
    print(f"摘星狙擊手 running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
