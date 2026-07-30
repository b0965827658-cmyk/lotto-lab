# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import html
import io
import itertools
import json
import math
import os
import random
import re
import socket
import ssl
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
CACHE_TTL_SECONDS = int(os.environ.get("LOTTO_CACHE_TTL_SECONDS", "30"))
LATEST_CACHE_TTL_SECONDS = int(os.environ.get("LOTTO_LATEST_CACHE_TTL_SECONDS", "10"))
ANALYSIS_ENGINE_VERSION = "2026.07-multimodel-candidate-pool-v1"
DEEP_ANALYSIS_WINDOW_SECONDS = max(
    60,
    int(os.environ.get("LOTTO_DEEP_ANALYSIS_WINDOW_SECONDS", str(8 * 60 * 60))),
)
DEEP_ANALYSIS_WINDOWS = (14, 36, 90, 180, 365)
BACKTEST_FALLBACK_LIMIT = 90
BACKTEST_MIN_HISTORY = 36
BACKTEST_SAMPLE_LIMIT = 24
MODEL_539_WINDOW = 300
BACKTEST_539_WINDOW = 500
BACKTEST_539_MIN_TRAIN = 300
AUTO_WINDOW_CANDIDATES = (36, 60, 90, 120, 180, 240, 300, 365)
MAX_JSON_BODY_BYTES = 64 * 1024
MAX_PUSH_SUBSCRIPTIONS = int(os.environ.get("LOTTO_MAX_PUSH_SUBSCRIPTIONS", "5000"))
API_RATE_LIMITS = {
    "/api/latest": (180, 60),
    "/api/lottery": (90, 60),
    "/api/history-search": (45, 60),
    "/api/config": (120, 60),
    "/api/push-subscription": (20, 60),
    "/api/notify-latest": (5, 600),
}
ALLOWED_GAMES = {"tw539", "ca-fantasy5"}
STRIPE_PAYMENT_LINK = os.environ.get("LOTTO_STRIPE_PAYMENT_LINK", "").strip()
PUSH_PUBLIC_KEY = os.environ.get("LOTTO_VAPID_PUBLIC_KEY", "").strip()
PUSH_PRIVATE_KEY = os.environ.get("LOTTO_VAPID_PRIVATE_KEY", "").strip().replace("\\n", "\n")
PUSH_CONTACT_EMAIL = os.environ.get("LOTTO_PUSH_CONTACT_EMAIL", "admin@example.com").strip()
NOTIFY_SECRET = os.environ.get("LOTTO_NOTIFY_SECRET", "").strip()
SUBSCRIPTIONS_FILE = Path(os.environ.get("LOTTO_SUBSCRIPTIONS_FILE", ROOT / "data" / "push_subscriptions.json"))
NOTIFY_STATE_FILE = Path(os.environ.get("LOTTO_NOTIFY_STATE_FILE", ROOT / "data" / "notify_state.json"))
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
rate_limit_hits: dict[tuple[str, str], list[float]] = {}
notify_lock = threading.Lock()


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


def load_push_subscriptions() -> list[dict[str, Any]]:
    try:
        if not SUBSCRIPTIONS_FILE.exists():
            return []
        payload = json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def save_push_subscriptions(subscriptions: list[dict[str, Any]]) -> None:
    SUBSCRIPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUBSCRIPTIONS_FILE.write_text(json.dumps(subscriptions, ensure_ascii=False, indent=2), encoding="utf-8")


def subscription_id(subscription: dict[str, Any]) -> str:
    endpoint = str(subscription.get("endpoint", ""))
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def upsert_push_subscription(subscription: dict[str, Any], game: str = "all") -> int:
    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        raise ValueError("缺少有效的通知訂閱資料")
    validate_push_subscription(subscription)
    subscriptions = load_push_subscriptions()
    if len(subscriptions) >= MAX_PUSH_SUBSCRIPTIONS and subscription_id(subscription) not in {item.get("id") for item in subscriptions}:
        raise ValueError("通知訂閱數已達上限")
    item_id = subscription_id(subscription)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = {"id": item_id, "subscription": subscription, "game": game if game in ALLOWED_GAMES else "all", "updatedAt": now}
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
        if not NOTIFY_STATE_FILE.exists():
            return {}
        payload = json.loads(NOTIFY_STATE_FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def save_notify_state(state: dict[str, Any]) -> None:
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


def latest_notification_message(game: str, lottery: dict[str, Any]) -> dict[str, Any]:
    numbers = "、".join(f"{number:02d}" for number in lottery.get("numbers", []))
    return {
        "title": f"{lottery.get('name', '摘星狙擊手')} 已開獎",
        "body": f"第 {lottery.get('period', '-')} 期：{numbers}",
        "url": f"/?game={game}",
        "tag": f"lotto-lab-{game}-{lottery.get('period', lottery.get('date', 'latest'))}",
    }


def broadcast_push_message(message: dict[str, Any]) -> tuple[int, int, int]:
    subscriptions = load_push_subscriptions()
    sent = 0
    failed = 0
    alive = []
    for item in subscriptions:
        subscription = item.get("subscription", {})
        try:
            send_push_message(subscription, message)
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
    # Notifications must not wait for the expensive model/backtest pipeline.
    # Read only the latest draw so the background loop can finish promptly.
    lottery = taiwan_latest() if game == "tw539" else california_latest()
    if already_notified(game, lottery):
        return {"ok": True, "game": game, "sent": 0, "failed": 0, "subscriberCount": len(load_push_subscriptions()), "skipped": True, "message": "這一期已通知過"}
    message = latest_notification_message(game, lottery)
    sent, failed, alive = broadcast_push_message(message)
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
    hit = cache.get(key)
    ttl = CACHE_TTL_SECONDS if ttl_seconds is None else max(1, ttl_seconds)
    if hit and time.time() - hit.created_at < ttl:
        return hit.value
    value = loader()
    cache[key] = CacheItem(value=value, created_at=time.time())
    return value


def cache_key_for_draws(prefix: str, game: str, limit: int, draws: list[dict[str, Any]]) -> str:
    latest = draws[0] if draws else {}
    return f"{prefix}-{ANALYSIS_ENGINE_VERSION}-{game}-{limit}-{latest.get('date', '')}-{latest.get('period', '')}"


def fetch_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
        },
    )
    with open_url(req, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_bytes(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
        },
    )
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


def cache_busted_url(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}_lotto_ts={time.time_ns()}"


def normalize_numbers(nums: list[int]) -> list[int]:
    return sorted(int(n) for n in nums)


def same_draw(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left.get("date") == right.get("date") and normalize_numbers(left.get("numbers", [])) == normalize_numbers(right.get("numbers", []))


def validate_draw(draw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one draw and reject malformed source rows before analysis."""
    if not isinstance(draw, dict):
        raise ValueError("開獎資料格式不正確")
    numbers = [int(number) for number in draw.get("numbers", [])]
    date = str(draw.get("date", "")).strip()
    period = str(draw.get("period", "")).strip()
    if len(numbers) != 5 or len(set(numbers)) != 5 or any(number < 1 or number > 39 for number in numbers):
        raise ValueError("開獎號碼數量或範圍不正確")
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date) or not period:
        raise ValueError("開獎期別或日期格式不正確")
    normalized = dict(draw)
    normalized["date"] = date
    normalized["period"] = period
    normalized["numbers"] = normalize_numbers(numbers)
    return normalized


def dedupe_draws(draws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove repeated source rows without changing the order of the draws."""
    unique = []
    seen = set()
    for draw in draws:
        try:
            draw = validate_draw(draw)
        except (TypeError, ValueError):
            continue
        numbers = tuple(draw["numbers"])
        date = draw["date"]
        period = draw["period"]
        key = (draw.get("game", ""), date, numbers)
        if key in seen:
            continue
        seen.add(key)
        unique.append(draw)
    return unique


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


def pilio_taiwan_history(limit: int = 90) -> list[dict[str, Any]]:
    def load():
        draws = []
        page_count = max(1, min(8, (limit + 22) // 23))
        for page in range(1, page_count + 1):
            url = PILIO_TAIWAN_URL.format(page=page)
            text = fetch_text(cache_busted_url(url), timeout=15)
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
        return dedupe_draws(draws)[:limit]

    return cached(f"pilio-taiwan-history-{limit}", load)


def taiwan_latest() -> dict[str, Any]:
    def load():
        official = None
        try:
            payload = json.loads(fetch_text(cache_busted_url(TAIWAN_LAST_URL), timeout=10))
            entries = payload.get("content", {}).get("lastNumberList", [])
            daily_cash = next((item for item in entries if item.get("gameCode") == 5120), None)
            if not daily_cash:
                raise RuntimeError("台灣彩券 API 目前沒有回傳今彩 539 最新資料")
            official = {
                "game": "tw539",
                "name": "今彩 539",
                "period": daily_cash.get("period", ""),
                "date": parse_date(daily_cash.get("drawDate", "")),
                "numbers": normalize_numbers(daily_cash.get("lotNumber", [])),
                "source": "台灣彩券 LastNumber API",
                "sourceUrl": TAIWAN_LAST_URL,
            }
            official = validate_draw(official)
        except Exception:
            official = None

        try:
            fallback = pilio_taiwan_history(1)
        except Exception:
            fallback = []
        if not official:
            if fallback:
                return fallback[0]
            raise RuntimeError("官方與備援來源目前都沒有回傳今彩 539 最新資料")
        if fallback and fallback[0].get("date", "") > official.get("date", ""):
            return fallback[0]
        return official

    return cached("taiwan-latest", load, ttl_seconds=LATEST_CACHE_TTL_SECONDS)


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
    return dedupe_draws(parsed)


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
        return dedupe_draws(rows)

    return cached("bundled-taiwan-539-history", load)


def taiwan_history(limit: int = 180) -> list[dict[str, Any]]:
    fast_history = pilio_taiwan_history(limit)
    # The public screen may only show a short window, but the 539 engine needs
    # a real 300/500-draw context.  Prefer the bundled official-history file
    # for larger requests so the model is not silently limited to eight pages.
    if limit <= 180 and len(fast_history) >= min(limit, 20):
        return dedupe_draws(fast_history)[:limit]
    bundled = bundled_taiwan_history()
    if bundled:
        latest = taiwan_latest()
        if latest and not any(same_draw(latest, draw) for draw in bundled):
            bundled = [latest, *bundled]
        return dedupe_draws(bundled)[:limit]
    try:
        rows = taiwan_dataset_rows()
        latest_row = max(rows, key=lambda row: int(row.get("資料所屬年度", "0") or "0"))
        latest_year = int(latest_row.get("資料所屬年度", "0") or "0") + 1911
        return dedupe_draws(taiwan_year_history(latest_year))[:limit]
    except Exception:
        return dedupe_draws(pilio_taiwan_history(limit))[:limit]


def search_taiwan_history(from_year: int, to_year: int, keyword: str = "", number: int | None = None, limit: int = 2000) -> dict[str, Any]:
    bundled = bundled_taiwan_history()
    if bundled:
        available_years = sorted({int(draw["date"][:4]) for draw in bundled if draw.get("date")})
    else:
        rows = taiwan_dataset_rows()
        available_years = sorted(int(row.get("資料所屬年度", "0") or "0") + 1911 for row in rows)
    if not available_years:
        return {"history": [], "availableYears": [], "searchedYears": []}
    start = max(min(from_year, to_year), available_years[0])
    end = min(max(from_year, to_year), available_years[-1])
    searched_years = list(range(start, end + 1))
    if bundled:
        draws = [draw for draw in bundled if draw.get("date") and start <= int(draw["date"][:4]) <= end]
        try:
            latest = taiwan_latest()
            latest_year = int(latest["date"][:4]) if latest.get("date") else None
            if latest_year in searched_years and not any(same_draw(latest, draw) for draw in draws):
                draws.append(latest)
        except Exception:
            pass
    else:
        draws = []
        for year in searched_years:
            draws.extend(taiwan_year_history(year))
        latest = taiwan_latest()
        latest_year = int(latest["date"][:4]) if latest.get("date") else None
        if latest_year in searched_years and not any(same_draw(latest, draw) for draw in draws):
            draws.append(latest)
    query = keyword.strip().lower()
    if query or number:
        draws = filter_history_rows(draws, query, number)
    draws = dedupe_draws(draws)
    draws.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
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


def parse_california_history(source_html: str) -> list[dict[str, Any]]:
    text = re.sub(r"<[^>]+>", "\n", source_html)
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
    values = dedupe_draws(parsed)
    values.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    return values


def california_history(limit: int = 180) -> list[dict[str, Any]]:
    def load():
        return parse_california_history(fetch_text(CALIFORNIA_FANTASY5_URL))

    return cached("california-history", load)[:limit]


def california_latest() -> dict[str, Any]:
    def load():
        values = parse_california_history(fetch_text(CALIFORNIA_FANTASY5_URL, timeout=15))
        if not values:
            raise RuntimeError("加州天天樂資料頁目前沒有可解析的最新開獎資料")
        return values[0]

    return cached("california-latest", load, ttl_seconds=LATEST_CACHE_TTL_SECONDS)


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
    return {
        "ordered": ordered,
        "frequency": frequency,
        "recentFrequency": recent_frequency,
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
        "number": {"heat": 0.19, "recent": 0.18, "trend": 0.11, "gap": 0.14, "neighbor": 0.07, "tail": 0.06, "pair": 0.06, "drag": 0.08, "repeatSignal": 0.04, "interval": 0.07},
        "combo": {"spread": 0.16, "zone": 0.17, "odd": 0.13, "low": 0.09, "sum": 0.16, "tail": 0.08, "repeat": 0.11, "interval": 0.10},
    },
    "momentum": {
        "label": "近期動能",
        "number": {"heat": 0.14, "recent": 0.27, "trend": 0.17, "gap": 0.06, "neighbor": 0.06, "tail": 0.04, "pair": 0.05, "drag": 0.08, "repeatSignal": 0.05, "interval": 0.08},
        "combo": {"spread": 0.13, "zone": 0.15, "odd": 0.11, "low": 0.09, "sum": 0.14, "tail": 0.08, "repeat": 0.20, "interval": 0.10},
    },
    "cycle": {
        "label": "遺漏週期",
        "number": {"heat": 0.15, "recent": 0.09, "trend": 0.06, "gap": 0.28, "neighbor": 0.08, "tail": 0.06, "pair": 0.08, "drag": 0.07, "repeatSignal": 0.05, "interval": 0.08},
        "combo": {"spread": 0.18, "zone": 0.16, "odd": 0.11, "low": 0.11, "sum": 0.16, "tail": 0.09, "repeat": 0.09, "interval": 0.10},
    },
    "shape": {
        "label": "區間尾數",
        "number": {"heat": 0.13, "recent": 0.12, "trend": 0.08, "gap": 0.10, "neighbor": 0.06, "tail": 0.15, "pair": 0.15, "drag": 0.05, "repeatSignal": 0.02, "interval": 0.14},
        "combo": {"spread": 0.15, "zone": 0.22, "odd": 0.14, "low": 0.10, "sum": 0.12, "tail": 0.09, "repeat": 0.04, "interval": 0.14},
    },
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
    recent12 = ordered[:12]
    recent30 = ordered[:30]
    older30 = ordered[30:60]
    recent60 = ordered[:60]

    def frequencies(rows: list[dict[str, Any]]) -> dict[int, int]:
        values = {n: 0 for n in range(1, max_number + 1)}
        for draw in rows:
            for number in draw["numbers"]:
                values[number] += 1
        return values

    recent12_freq = frequencies(recent12)
    recent30_freq = frequencies(recent30)
    older30_freq = frequencies(older30)
    all_freq = frequencies(ordered)
    gaps = number_stats(ordered, max_number)["gaps"]

    max_all = max(all_freq.values()) or 1
    max_recent12 = max(recent12_freq.values()) or 1
    max_trend = max((max(0, recent30_freq[n] - older30_freq[n]) for n in range(1, max_number + 1)), default=1) or 1
    max_gap = max(gaps.values()) or 1

    tails = {n: 0 for n in range(10)}
    for draw in recent30:
        for number in draw["numbers"]:
            tails[number % 10] += 1
    max_tail = max(tails.values()) or 1

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
        sum_values.append(sum(numbers))
    sorted_sums = sorted(sum_values)
    center_sum = sorted_sums[len(sorted_sums) // 2] if sorted_sums else (max_number + 1) * 2.5
    low_sum = sorted_sums[max(0, int(len(sorted_sums) * 0.2) - 1)] if sorted_sums else center_sum - 24
    high_sum = sorted_sums[min(len(sorted_sums) - 1, int(len(sorted_sums) * 0.8))] if sorted_sums else center_sum + 24
    sum_width = max(18, (high_sum - low_sum) / 2)

    number_scores = {}
    for n in range(1, max_number + 1):
        trend = max(0, recent30_freq[n] - older30_freq[n])
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
        }

    return {
        "ordered": ordered,
        "numberScores": number_scores,
        "pairCounts": pair_counts,
        "zoneCounts": zone_counts,
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
    scores = {
        "spread": combo_spread_score(sorted_numbers, max_number),
        "zone": signature_score(zone_signature(sorted_numbers), profile["zoneCounts"]),
        "odd": signature_score(odd, profile["oddCounts"]),
        "low": signature_score(low, profile["lowCounts"]),
        "sum": closeness(sum(sorted_numbers), profile["centerSum"], profile["sumWidth"]),
        "tail": tail_diversity,
        "repeat": closeness(repeat, profile["repeatTarget"], 1.6),
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


def score_number(n: int, profile: dict[str, Any], model: dict[str, Any]) -> float:
    features = profile["numberScores"][n]
    weights = model["number"]
    return sum(features[key] * weights.get(key, 0) for key in features)


def recommendation_number_scores(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    seed_label: str = "",
    profile_name: str = "balanced",
) -> dict[int, float]:
    if profile_name == "classic":
        stats = number_stats(draws, max_number)
        frequency = stats["frequency"]
        recent_frequency = stats["recentFrequency"]
        gaps = stats["gaps"]
        max_freq = max(frequency.values()) or 1
        max_recent = max(recent_frequency.values()) or 1
        max_gap = max(gaps.values()) or 1
        return {
            n: (
                (frequency[n] / max_freq) * 0.45
                + (recent_frequency[n] / max_recent) * 0.18
                + (gaps[n] / max_gap) * 0.27
                + random.Random(f"{seed_label}:{n}").random() * 0.10
            )
            for n in range(1, max_number + 1)
        }

    model = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["balanced"])
    profile = pattern_profile(draws, max_number)
    return {
        n: score_number(n, profile, model) + random.Random(f"{seed_label}:{profile_name}:{n}").random() * 0.035
        for n in range(1, max_number + 1)
    }


def model_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    seed_label: str = "",
    profile_name: str = "balanced",
) -> list[int]:
    if profile_name == "classic":
        return classic_recommendation(draws, max_number=max_number, pick_count=pick_count, seed_label=seed_label)
    model = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["balanced"])
    profile = pattern_profile(draws, max_number)
    number_scores = recommendation_number_scores(
        draws,
        max_number=max_number,
        seed_label=seed_label,
        profile_name=profile_name,
    )

    pool = sorted(number_scores, key=lambda n: (-number_scores[n], n))[: min(24, max_number)]
    rng = random.Random(f"lotto-lab:{profile_name}:{seed_label}:{','.join(map(str, pool))}")
    candidates: set[tuple[int, ...]] = set()
    candidates.add(tuple(sorted(pool[:pick_count])))
    for _ in range(420):
        weighted = sorted(pool, key=lambda n: number_scores[n] + rng.random() * 0.28, reverse=True)
        candidates.add(tuple(sorted(weighted[:pick_count])))
        if len(pool) >= pick_count:
            candidates.add(tuple(sorted(rng.sample(pool, pick_count))))

    def score_combo(combo: tuple[int, ...]) -> float:
        score = sum(number_scores[n] for n in combo) / pick_count
        return score * 0.58 + combo_pattern_score(list(combo), profile, model, max_number) * 0.42

    best = max(candidates, key=lambda combo: (score_combo(combo), combo_spread_score(list(combo), max_number), combo))
    return list(best)


def deep_analysis_slot(now: float | None = None) -> tuple[int, int]:
    """Return the shared eight-hour analysis slot boundaries."""
    timestamp = float(time.time() if now is None else now)
    start = int(timestamp // DEEP_ANALYSIS_WINDOW_SECONDS) * DEEP_ANALYSIS_WINDOW_SECONDS
    return start, start + DEEP_ANALYSIS_WINDOW_SECONDS


def deep_sniper_analysis(
    draws: list[dict[str, Any]],
    game: str,
    max_number: int = 39,
    pick_count: int = 5,
) -> dict[str, Any]:
    """Cross-check several history windows and publish one deterministic five-number set."""
    ordered = sorted(draws, key=lambda item: (item.get("date", ""), item.get("period", "")), reverse=True)
    available = [window for window in DEEP_ANALYSIS_WINDOWS if window <= len(ordered)]
    if not available and ordered:
        available = [len(ordered)]
    if not available:
        return {"numbers": [], "windowsUsed": [], "windowPicks": [], "method": "資料不足"}

    configured_weights = {14: 0.30, 36: 0.25, 90: 0.20, 180: 0.15, 365: 0.10}
    total_weight = sum(configured_weights.get(window, 0.10) for window in available) or 1
    aggregate = {number: 0.0 for number in range(1, max_number + 1)}
    window_picks = []

    for window in available:
        window_draws = ordered[:window]
        weight = configured_weights.get(window, 0.10) / total_weight
        if game == "ca-fantasy5":
            pick = california_recommendation(window_draws, max_number=max_number, pick_count=pick_count)
            logic = california_logic_scores(window_draws, max_number=max_number)
            label = "天天樂專屬整合邏輯"
        else:
            variant, _, _ = choose_tw539_strategy(window_draws, max_number=max_number, pick_count=pick_count)
            pick = tw539_recommendation(
                window_draws,
                max_number=max_number,
                pick_count=pick_count,
                variant=variant,
            )
            logic = tw539_logic_scores(window_draws, max_number=max_number, variant=variant)
            label = TW539_VARIANTS.get(variant, TW539_VARIANTS["cycle"])["label"]

        scores = logic.get("scores", {})
        if scores:
            low = min(scores.values())
            high = max(scores.values())
            spread = high - low or 1
            for number in aggregate:
                aggregate[number] += weight * ((scores.get(number, low) - low) / spread)
        for rank, number in enumerate(pick, start=1):
            aggregate[number] += weight * ((pick_count - rank + 1) / pick_count) * 0.45
        window_picks.append({"limit": window, "numbers": sorted(pick), "label": label})

    pool = sorted(aggregate, key=lambda number: (-aggregate[number], number))[: min(16, max_number)]
    if len(pool) < pick_count:
        numbers = sorted(pool)
    else:
        candidates = itertools.combinations(pool, pick_count)
        numbers = sorted(
            max(
                candidates,
                key=lambda combo: (
                    sum(aggregate[number] for number in combo) / pick_count
                    + combo_spread_score(list(combo), max_number) * 0.16,
                    tuple(-number for number in combo),
                ),
            )
        )
    return {
        "numbers": numbers,
        "windowsUsed": available,
        "windowPicks": window_picks,
        "method": "多視窗交叉：近期熱度、遺漏、區間、尾數與滾動回測訊號綜合排序。",
    }


def classic_recommendation(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5, seed_label: str = "") -> list[int]:
    number_scores = recommendation_number_scores(
        draws,
        max_number=max_number,
        seed_label=seed_label,
        profile_name="classic",
    )

    pool = sorted(number_scores, key=lambda n: (-number_scores[n], n))[: min(22, max_number)]
    rng = random.Random(f"lotto-lab:{seed_label}:{','.join(map(str, pool))}")
    candidates: set[tuple[int, ...]] = set()
    candidates.add(tuple(sorted(pool[:pick_count])))
    for _ in range(260):
        weighted = sorted(pool, key=lambda n: number_scores[n] + rng.random() * 0.34, reverse=True)
        candidates.add(tuple(sorted(weighted[:pick_count])))
        if len(pool) >= pick_count:
            candidates.add(tuple(sorted(rng.sample(pool, pick_count))))

    def score_combo(combo: tuple[int, ...]) -> float:
        score = sum(number_scores[n] for n in combo) / pick_count
        return score * 0.72 + combo_spread_score(list(combo), max_number) * 0.28

    best = max(candidates, key=lambda combo: (score_combo(combo), combo_spread_score(list(combo), max_number), combo))
    return list(best)


def california_logic_scores(draws: list[dict[str, Any]], max_number: int = 39) -> dict[str, Any]:
    """Score California Fantasy 5 with its own short-cycle selection rules."""
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    stats = number_stats(ordered, max_number)
    recent10 = ordered[:10]
    recent5 = ordered[:5]
    recent3 = ordered[:3]
    recent20 = ordered[:20]

    def frequencies(rows: list[dict[str, Any]]) -> dict[int, int]:
        values = {number: 0 for number in range(1, max_number + 1)}
        for draw in rows:
            for number in draw["numbers"]:
                if number in values:
                    values[number] += 1
        return values

    recent5_frequency = frequencies(recent5)
    recent10_frequency = frequencies(recent10)
    recent20_frequency = frequencies(recent20)
    recent3_signal = {number: 0 for number in range(1, max_number + 1)}
    edge_signal = {number: 0.0 for number in range(1, max_number + 1)}
    for draw_index, draw in enumerate(recent3):
        draw_weight = 3 - draw_index
        for number in draw["numbers"]:
            if number in recent3_signal:
                recent3_signal[number] += draw_weight
            for offset in (-1, 1):
                nearby = number + offset
                if 1 <= nearby <= max_number:
                    edge_signal[nearby] += draw_weight * (1.0 if abs(offset) == 1 else 0.0)

    tail_last_seen = {tail: None for tail in range(10)}
    for draw_index, draw in enumerate(ordered):
        for number in draw["numbers"]:
            tail = number % 10
            if tail_last_seen[tail] is None:
                tail_last_seen[tail] = draw_index
    tail_gaps = {
        tail: (last_seen if last_seen is not None else len(ordered))
        for tail, last_seen in tail_last_seen.items()
    }

    max_recent5 = max(recent5_frequency.values(), default=0) or 1
    max_recent10 = max(recent10_frequency.values(), default=0) or 1
    max_recent20 = max(recent20_frequency.values(), default=0) or 1
    max_recent3 = max(recent3_signal.values(), default=0) or 1
    max_edge = max(edge_signal.values(), default=0) or 1
    scores: dict[int, float] = {}
    blocked_numbers: list[int] = []
    blocked_tails: list[int] = []
    for number in range(1, max_number + 1):
        gap = stats["gaps"].get(number, len(ordered))
        tail_gap = tail_gaps[number % 10]
        number_blocked = gap >= 20
        tail_blocked = tail_gap >= 4
        if number_blocked:
            blocked_numbers.append(number)
        if tail_blocked and number % 10 not in blocked_tails:
            blocked_tails.append(number % 10)

        hot_score = recent5_frequency[number] / max_recent5
        ten_period_score = recent10_frequency[number] / max_recent10
        twenty_period_score = recent20_frequency[number] / max_recent20
        recent_draw_score = recent3_signal[number] / max_recent3
        edge_score = edge_signal[number] / max_edge
        freshness_score = max(0.0, 1.0 - min(gap, 20) / 20)
        tail_freshness_score = max(0.0, 1.0 - min(tail_gap, 6) / 6)
        score = (
            hot_score * 0.17
            + ten_period_score * 0.19
            + twenty_period_score * 0.08
            + recent_draw_score * 0.16
            + edge_score * 0.24
            + freshness_score * 0.09
            + tail_freshness_score * 0.07
        )
        # 長遺漏與長冷尾只降權，不硬排除，避免把隨機事件誤當成絕對規律。
        if number_blocked:
            score -= 0.18
        if tail_blocked:
            score -= 0.10
        scores[number] = round(score, 6)

    return {
        "ordered": ordered,
        "scores": scores,
        "gaps": stats["gaps"],
        "tailGaps": tail_gaps,
        "blockedNumbers": blocked_numbers,
        "blockedTails": sorted(blocked_tails),
        "recent5Frequency": recent5_frequency,
        "recent10Frequency": recent10_frequency,
        "recent20Frequency": recent20_frequency,
        "recent3Signal": recent3_signal,
        "edgeSignal": edge_signal,
    }


def california_recommendation(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> list[int]:
    logic = california_logic_scores(draws, max_number=max_number)
    scores = logic["scores"]
    # 長遺漏與冷尾在分數中降權，但保留所有號碼作為低權重候選。
    ranked = sorted(range(1, max_number + 1), key=lambda number: (-scores[number], number))
    return sorted(ranked[:pick_count])


def california_rolling_backtest(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
) -> dict[str, Any]:
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    distribution = {str(number): 0 for number in range(pick_count + 1)}
    rows = []
    sample_size = min(36, max(0, len(ordered) - 10))
    for index in range(sample_size):
        target = ordered[index]
        training = ordered[index + 1 : index + 91]
        if len(training) < 10:
            continue
        pick = california_recommendation(training, max_number=max_number, pick_count=pick_count)
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
    return {
        "testedCount": tested,
        "averageHit": round(hit_sum / tested, 2) if tested else 0,
        "onePlusCount": one_plus,
        "onePlusRate": round((one_plus / tested) * 100, 1) if tested else 0,
        "twoPlusCount": two_plus,
        "twoPlusRate": round((two_plus / tested) * 100, 1) if tested else 0,
        "threePlusCount": three_plus,
        "threePlusRate": round((three_plus / tested) * 100, 1) if tested else 0,
        "bestHit": best_hit,
        "distribution": distribution,
        "recentRows": rows[:10],
        "method": "天天樂專屬滾動回測：近3期邊號、近5/10期熱度、近20期背景與尾數新鮮度交叉；長遺漏只降權，不硬排除。",
    }


TW539_VARIANTS = {
    "cycle": {
        "label": "539 遺漏週期",
        "summary": "近 14 期熱度、近 36 期背景與遺漏週期交叉；再補上期鄰近與尾數訊號。",
        "shape": False,
        "weights": {
            "heat": 0.15,
            "recent": 0.09,
            "trend": 0.06,
            "gap": 0.28,
            "neighbor": 0.08,
            "tail": 0.06,
            "pair": 0.08,
            "drag": 0.07,
            "repeatSignal": 0.05,
            "interval": 0.08,
        },
    },
    "cycle-shape": {
        "label": "539 週期版路",
        "summary": "以遺漏週期為主，加入近期常見的奇偶、區間、總和與分散形狀。",
        "shape": True,
        "weights": {
            "heat": 0.15,
            "recent": 0.09,
            "trend": 0.06,
            "gap": 0.28,
            "neighbor": 0.08,
            "tail": 0.06,
            "pair": 0.08,
            "drag": 0.07,
            "repeatSignal": 0.05,
            "interval": 0.08,
        },
    },
    "recent": {
        "label": "539 近期動能",
        "summary": "提高近 14 期與近 36 期訊號，避免只看長期總頻率。",
        "shape": False,
        "weights": {
            "heat": 0.14,
            "recent": 0.27,
            "trend": 0.17,
            "gap": 0.06,
            "neighbor": 0.06,
            "tail": 0.04,
            "pair": 0.05,
            "drag": 0.08,
            "repeatSignal": 0.05,
            "interval": 0.08,
        },
    },
}


def tw539_logic_scores(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    variant: str = "cycle",
) -> dict[str, Any]:
    """539 專屬計分：短期、週期與版路分開計算，不沿用天天樂的硬篩選。"""
    profile = pattern_profile(draws, max_number)
    config = TW539_VARIANTS.get(variant, TW539_VARIANTS["cycle"])
    weights = config["weights"]
    scores = {
        number: round(
            sum(profile["numberScores"][number][feature] * weight for feature, weight in weights.items()),
            6,
        )
        for number in range(1, max_number + 1)
    }
    ranked = sorted(scores, key=lambda number: (-scores[number], number))
    return {
        "profile": profile,
        "scores": scores,
        "ranked": ranked,
        "candidatePool": ranked[: min(15, max_number)],
        "variant": variant,
        "label": config["label"],
    }


def tw539_shape_score(numbers: tuple[int, ...], profile: dict[str, Any], number_scores: dict[int, float]) -> float:
    """把常見版路當成輕微加分，避免版路規則反過來主導號碼。"""
    combo_model = {
        "combo": {
            "spread": 0.22,
            "zone": 0.20,
            "odd": 0.16,
            "low": 0.10,
            "sum": 0.16,
            "tail": 0.06,
            "repeat": 0.06,
            "interval": 0.04,
        }
    }
    individual = sum(number_scores[number] for number in numbers) / len(numbers)
    shape = combo_pattern_score(list(numbers), profile, combo_model, max_number=39)
    return individual * 0.84 + shape * 0.16


def tw539_recommendation(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    variant: str = "cycle",
) -> list[int]:
    logic = tw539_logic_scores(draws, max_number=max_number, variant=variant)
    scores = logic["scores"]
    pool = logic["candidatePool"]
    if TW539_VARIANTS.get(variant, TW539_VARIANTS["cycle"])["shape"] and len(pool) >= pick_count:
        candidates = itertools.combinations(pool, pick_count)
        best = max(
            candidates,
            key=lambda combo: (
                tw539_shape_score(combo, logic["profile"], scores),
                tuple(-number for number in combo),
            ),
        )
        return list(best)
    return sorted(pool[:pick_count])


def tw539_rolling_backtest(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    variant: str = "cycle",
) -> dict[str, Any]:
    ordered = sorted(draws, key=lambda item: (item["date"], item["period"]), reverse=True)
    distribution = {str(number): 0 for number in range(pick_count + 1)}
    rows = []
    sample_size = min(36, max(0, len(ordered) - 25))
    for index in range(sample_size):
        target = ordered[index]
        training = ordered[index + 1 : index + 91]
        if len(training) < 20:
            continue
        pick = tw539_recommendation(training, max_number=max_number, pick_count=pick_count, variant=variant)
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
    config = TW539_VARIANTS.get(variant, TW539_VARIANTS["cycle"])
    return {
        "testedCount": tested,
        "averageHit": round(hit_sum / tested, 2) if tested else 0,
        "onePlusCount": one_plus,
        "onePlusRate": round(one_plus / tested * 100, 1) if tested else 0,
        "twoPlusCount": two_plus,
        "twoPlusRate": round(two_plus / tested * 100, 1) if tested else 0,
        "threePlusCount": three_plus,
        "threePlusRate": round(three_plus / tested * 100, 1) if tested else 0,
        "bestHit": best_hit,
        "distribution": distribution,
        "recentRows": rows[:10],
        "method": f"539 專屬滾動回測：每次只用目標期以前資料，採用「{config['label']}」，不把未來開獎資料倒灌回模型。",
    }


def choose_tw539_strategy(
    draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    results = []
    for variant, config in TW539_VARIANTS.items():
        backtest = tw539_rolling_backtest(draws, max_number=max_number, pick_count=pick_count, variant=variant)
        quality = (
            backtest["averageHit"] * 100
            + backtest["onePlusRate"] * 0.95
            + backtest["twoPlusRate"] * 1.55
            + backtest["threePlusRate"] * 3.2
            + backtest["bestHit"] * 14
        )
        results.append(
            {
                "id": variant,
                "label": config["label"],
                "quality": round(quality, 2),
                "averageHit": backtest["averageHit"],
                "onePlusRate": backtest["onePlusRate"],
                "twoPlusRate": backtest["twoPlusRate"],
                "threePlusRate": backtest["threePlusRate"],
                "bestHit": backtest["bestHit"],
                "testedCount": backtest["testedCount"],
            }
        )
    results.sort(key=lambda item: (-item["quality"], -item["onePlusRate"], -item["averageHit"], item["id"]))
    selected = results[0]["id"] if results else "cycle"
    return selected, tw539_rolling_backtest(draws, max_number=max_number, pick_count=pick_count, variant=selected), results


MODEL_539_FEATURE_KEYS = (
    "recent30",
    "recent100",
    "recent300",
    "omission",
    "returnRate",
    "repeatRate",
    "tailBalance",
    "oddBalance",
    "sizeBalance",
    "rangeBalance",
    "sumFit",
    "spanFit",
    "acFit",
    "sameTailFit",
    "consecutiveFit",
    "previousRepeatFit",
)

MODEL_539_DEFAULT_WEIGHTS = {
    "recent30": 0.22,
    "recent100": 0.16,
    "recent300": 0.08,
    "omission": 0.12,
    "returnRate": 0.08,
    "repeatRate": 0.05,
    "tailBalance": 0.07,
    "oddBalance": 0.05,
    "sizeBalance": 0.05,
    "rangeBalance": 0.07,
    "sumFit": 0.03,
    "spanFit": 0.02,
    "acFit": 0.02,
    "sameTailFit": 0.03,
    "consecutiveFit": 0.03,
    "previousRepeatFit": 0.02,
}


def _539_ordered(draws: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    rows = list(draws or [])
    rows.sort(key=lambda item: (item.get("date", ""), item.get("period", "")))
    if limit:
        rows = rows[-limit:]
    return rows


def _539_clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _539_ac_value(numbers: list[int]) -> int:
    values = sorted(numbers)
    differences = {right - left for index, left in enumerate(values) for right in values[index + 1 :]}
    return max(0, len(differences) - max(0, len(values) - 1))


def _539_draw_shape(numbers: list[int]) -> dict[str, Any]:
    values = sorted(numbers)
    tails = [number % 10 for number in values]
    return {
        "odd": sum(number % 2 for number in values),
        "small": sum(number <= 19 for number in values),
        "zones": [
            sum(1 for number in values if 1 <= number <= 13),
            sum(1 for number in values if 14 <= number <= 26),
            sum(1 for number in values if 27 <= number <= 39),
        ],
        "sum": sum(values),
        "span": values[-1] - values[0] if values else 0,
        "ac": _539_ac_value(values),
        "sameTail": sum(tails.count(tail) - 1 for tail in set(tails)),
        "consecutive": sum(right - left == 1 for left, right in zip(values, values[1:])),
    }


def _539_average_shapes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shapes = [_539_draw_shape(draw["numbers"]) for draw in rows]
    count = len(shapes) or 1
    return {
        "odd": round(sum(shape["odd"] for shape in shapes) / count, 3),
        "small": round(sum(shape["small"] for shape in shapes) / count, 3),
        "zones": [round(sum(shape["zones"][index] for shape in shapes) / count, 3) for index in range(3)],
        "sum": round(sum(shape["sum"] for shape in shapes) / count, 3),
        "span": round(sum(shape["span"] for shape in shapes) / count, 3),
        "ac": round(sum(shape["ac"] for shape in shapes) / count, 3),
        "sameTail": round(sum(shape["sameTail"] for shape in shapes) / count, 3),
        "consecutive": round(sum(shape["consecutive"] for shape in shapes) / count, 3),
    }


def _539_rate_features(rows: list[dict[str, Any]], max_number: int) -> tuple[dict[int, dict[str, float]], dict[str, Any]]:
    """Build per-number signals from a newest-first 300-draw context.

    The signals intentionally mix frequency and counter-signals.  A number is
    never promoted solely because it is hot, and a long omission is not a hard
    inclusion rule.
    """
    newest = sorted(rows, key=lambda item: (item.get("date", ""), item.get("period", "")), reverse=True)[:MODEL_539_WINDOW]
    chronological = list(reversed(newest))
    total = len(chronological)
    windows = {30: newest[:30], 100: newest[:100], 300: newest[:300]}
    frequencies = {
        window: {number: 0 for number in range(1, max_number + 1)}
        for window in windows
    }
    for window, window_rows in windows.items():
        for draw in window_rows:
            for number in draw.get("numbers", []):
                if number in frequencies[window]:
                    frequencies[window][number] += 1

    def normalized_rates(window: int) -> dict[int, float]:
        rates = {number: safe_divide(frequencies[window][number], max(1, len(windows[window]))) for number in range(1, max_number + 1)}
        low = min(rates.values(), default=0.0)
        high = max(rates.values(), default=1.0)
        spread = high - low or 1.0
        return {number: round((rates[number] - low) / spread, 6) for number in rates}

    recent30 = normalized_rates(30)
    recent100 = normalized_rates(100)
    recent300 = normalized_rates(300)
    gaps = {number: len(newest) for number in range(1, max_number + 1)}
    occurrences: dict[int, list[int]] = {number: [] for number in range(1, max_number + 1)}
    for index, draw in enumerate(newest):
        for number in draw.get("numbers", []):
            if number in gaps:
                gaps[number] = index
                occurrences[number].append(index)

    repeat_count = {number: 0 for number in range(1, max_number + 1)}
    repeat_total = {number: 0 for number in range(1, max_number + 1)}
    for newer, older in zip(newest, newest[1:]):
        newer_numbers = set(newer.get("numbers", []))
        older_numbers = set(older.get("numbers", []))
        for number in older_numbers:
            repeat_total[number] += 1
            if number in newer_numbers:
                repeat_count[number] += 1

    runs = {number: 0 for number in range(1, max_number + 1)}
    for draw in newest:
        present = set(draw.get("numbers", []))
        for number in runs:
            if runs[number] == 0 and number in present:
                runs[number] += 1
            elif runs[number] > 0 and number in present:
                runs[number] += 1
            elif runs[number] > 0:
                # The leading run has ended; retain it and skip further draws.
                runs[number] = -runs[number]
    runs = {number: max(0, -value if value < 0 else value) for number, value in runs.items()}

    return_events = {number: 0 for number in range(1, max_number + 1)}
    for number, seen_indices in occurrences.items():
        for current, previous in zip(seen_indices, seen_indices[1:]):
            gap = current - previous - 1
            if 8 <= gap <= 15:
                return_events[number] += 1
    return_rates = {
        number: safe_divide(return_events[number], max(1, len(occurrences[number]) - 1))
        for number in range(1, max_number + 1)
    }

    previous_overlap = [
        len(set(newer.get("numbers", [])) & set(older.get("numbers", [])))
        for newer, older in zip(newest, newest[1:])
    ]

    tail_counts = {tail: 0 for tail in range(10)}
    for draw in newest[:30]:
        for number in draw.get("numbers", []):
            tail_counts[number % 10] += 1
    tail_expected = safe_divide(5 * min(30, total), 10)
    odd_average = safe_divide(sum(sum(number % 2 for number in draw.get("numbers", [])) for draw in newest), max(1, total))
    small_average = safe_divide(sum(sum(number <= 19 for number in draw.get("numbers", [])) for draw in newest), max(1, total))
    shape_average = _539_average_shapes(newest)
    recent_shape = _539_average_shapes(newest[:30])
    recent_odd_average = recent_shape["odd"]
    recent_small_average = recent_shape["small"]
    range_averages = shape_average["zones"]

    features: dict[int, dict[str, float]] = {}
    for number in range(1, max_number + 1):
        gap = gaps[number]
        omission = (
            1.0 if 8 <= gap <= 15 else
            0.55 if 5 <= gap < 8 else
            0.15 if gap < 5 else
            max(-0.9, 0.45 - (gap - 15) * 0.07)
        )
        run_penalty = -min(1.0, max(0, runs[number] - 1) / 3)
        tail_balance = _539_clamp((tail_expected - tail_counts[number % 10]) / max(3.0, tail_expected))
        odd_direction = 1 if number % 2 else -1
        size_direction = 1 if number <= 19 else -1
        zone_index = 0 if number <= 13 else 1 if number <= 26 else 2
        zone_deficit = _539_clamp((range_averages[zone_index] - recent_shape["zones"][zone_index]) / 2.0)
        # The category balance is added again while building the 15-number pool.
        odd_balance = _539_clamp((odd_average - recent_odd_average) * odd_direction / 2.5)
        size_balance = _539_clamp((small_average - recent_small_average) * size_direction / 2.5)
        expected_number = safe_divide(shape_average["sum"], 5)
        sum_fit = max(0.0, 1.0 - abs(number - expected_number) / 20.0)
        span_fit = _539_clamp((abs(number - (max_number + 1) / 2) - shape_average["span"] / 2) / 20.0)
        repeat_rate = safe_divide(repeat_count[number], max(1, repeat_total[number]))
        features[number] = {
            "recent30": recent30[number],
            "recent100": recent100[number],
            "recent300": recent300[number],
            "omission": round(omission, 6),
            "returnRate": round(return_rates[number], 6),
            "repeatRate": round(repeat_rate, 6),
            "tailBalance": round(tail_balance, 6),
            "oddBalance": round(odd_balance, 6),
            "sizeBalance": round(size_balance, 6),
            "rangeBalance": round(zone_deficit, 6),
            "sumFit": round(sum_fit, 6),
            "spanFit": round(max(0.0, span_fit), 6),
            "acFit": round(closeness(shape_average["ac"], 4.0, 4.0), 6),
            "sameTailFit": round(tail_balance, 6),
            "consecutiveFit": round(run_penalty, 6),
            "previousRepeatFit": round(_539_clamp(repeat_rate + run_penalty), 6),
        }

    metrics = {
        "drawCount": total,
        "windowWeights": {"近30期": 0.50, "近100期": 0.30, "近300期": 0.20},
        "frequency": {str(window): frequencies[window] for window in windows},
        "omission": {str(number): gaps[number] for number in gaps},
        "returnRate": {str(number): round(return_rates[number] * 100, 1) for number in return_rates},
        "consecutiveRun": {str(number): runs[number] for number in runs},
        "repeatRate": {str(number): round(safe_divide(repeat_count[number], max(1, repeat_total[number])) * 100, 1) for number in repeat_count},
        "tailDistribution": tail_counts,
        "oddAverage": round(odd_average, 3),
        "smallAverage": round(small_average, 3),
        "rangeAverage": range_averages,
        "shapeAverage": shape_average,
        "evenAverage": round(5 - odd_average, 3),
        "largeAverage": round(5 - small_average, 3),
        "sumAverage": shape_average["sum"],
        "spanAverage": shape_average["span"],
        "acAverage": shape_average["ac"],
        "sameTailAverage": shape_average["sameTail"],
        "consecutiveAverage": shape_average["consecutive"],
        "previousRepeatRate": round(safe_divide(sum(previous_overlap), max(1, len(previous_overlap) * 5)) * 100, 1),
        "weightedFeatureKeys": list(MODEL_539_FEATURE_KEYS),
        "weightedFeatureAverages": {
            key: round(safe_divide(sum(features[number][key] for number in features), max(1, len(features))), 4)
            for key in MODEL_539_FEATURE_KEYS
        },
    }
    return features, metrics


def _539_rank(features: dict[int, dict[str, float]], weights: dict[str, float]) -> list[int]:
    return sorted(
        features,
        key=lambda number: (
            -sum(features[number].get(key, 0.0) * weights.get(key, 0.0) for key in MODEL_539_FEATURE_KEYS),
            number,
        ),
    )


def _539_learn_weights(draws: list[dict[str, Any]], max_updates: int = 48) -> tuple[dict[str, float], dict[str, Any]]:
    """Online outcome update: reward features carried by actual hits.

    This is intentionally small and explainable.  It learns from prior draws
    only, then the learned weights are used for the next candidate pool.
    """
    weights = dict(MODEL_539_DEFAULT_WEIGHTS)
    ordered = _539_ordered(draws, 500)
    if len(ordered) < 40:
        return weights, {"sampleCount": 0, "updates": 0, "status": "資料累積中"}
    start = max(30, len(ordered) - max_updates - 1)
    updates = 0
    for target_index in range(start, len(ordered) - 1):
        context = ordered[:target_index][-MODEL_539_WINDOW:]
        if len(context) < 30:
            continue
        features, _ = _539_rate_features(context, 39)
        ranking = _539_rank(features, weights)
        actual = set(ordered[target_index]["numbers"])
        negatives = [number for number in ranking[:15] if number not in actual]
        if not negatives:
            continue
        for key in MODEL_539_FEATURE_KEYS:
            positive = safe_divide(sum(features[number][key] for number in actual), len(actual))
            negative = safe_divide(sum(features[number][key] for number in negatives), len(negatives))
            weights[key] = _539_clamp(weights[key] + 0.18 * (positive - negative), 0.005, 0.75)
        updates += 1
    total_weight = sum(weights.values()) or 1.0
    weights = {key: round(value / total_weight, 6) for key, value in weights.items()}
    # Keep the requested time hierarchy visible even after online learning:
    # recent 30 remains above recent 100, which remains above recent 300.
    weights["recent100"] = max(weights["recent100"], weights["recent300"] + 0.004)
    weights["recent30"] = max(
        weights["recent30"],
        weights["recent100"] + 0.004,
        weights["recent300"] + 0.008,
    )
    total_weight = sum(weights.values()) or 1.0
    weights = {key: round(value / total_weight, 6) for key, value in weights.items()}
    return weights, {"sampleCount": min(max_updates, max(0, len(ordered) - start - 1)), "updates": updates, "status": "已依命中結果自動調權"}


def _539_pool_balance(selected: list[int], number: int, metrics: dict[str, Any], pool_size: int = 15) -> float:
    values = selected + [number]
    tail_count = sum(1 for value in values if value % 10 == number % 10)
    tail_target = max(1.0, pool_size / 10)
    odd_target = metrics.get("oddAverage", 2.5) * pool_size / 5
    small_target = metrics.get("smallAverage", 2.5) * pool_size / 5
    odd_count = sum(value % 2 for value in values)
    small_count = sum(value <= 19 for value in values)
    zone_index = 0 if number <= 13 else 1 if number <= 26 else 2
    zone_target = metrics.get("rangeAverage", [pool_size / 3] * 3)[zone_index] * pool_size / 5
    zone_count = sum(1 for value in values if (0 if value <= 13 else 1 if value <= 26 else 2) == zone_index)
    return (
        _539_clamp((tail_target - tail_count) / 2.5) * 0.12
        + _539_clamp((odd_target - odd_count) / 5) * 0.08
        + _539_clamp((small_target - small_count) / 5) * 0.08
        + _539_clamp((zone_target - zone_count) / 4) * 0.12
    )


def _539_candidate_model(draws: list[dict[str, Any]], weights: dict[str, float] | None = None) -> dict[str, Any]:
    context = _539_ordered(draws, MODEL_539_WINDOW)
    features, metrics = _539_rate_features(context, 39)
    weights = weights or MODEL_539_DEFAULT_WEIGHTS
    base_scores = {
        number: sum(features[number].get(key, 0.0) * weights.get(key, 0.0) for key in MODEL_539_FEATURE_KEYS)
        for number in features
    }
    selected: list[int] = []
    remaining = set(features)
    while remaining and len(selected) < 15:
        choice = max(
            remaining,
            key=lambda number: (
                base_scores[number] + _539_pool_balance(selected, number, metrics),
                base_scores[number],
                -number,
            ),
        )
        selected.append(choice)
        remaining.remove(choice)
    details = []
    for index, number in enumerate(selected):
        signal = features[number]
        reasons = []
        if signal["recent30"] >= 0.72:
            reasons.append("近30期熱度高")
        elif signal["recent100"] >= 0.65:
            reasons.append("近100期有穩定頻率")
        if 8 <= metrics["omission"].get(str(number), 0) <= 15:
            reasons.append("遺漏8～15期，回補條件適中")
        elif metrics["omission"].get(str(number), 0) > 15:
            reasons.append("長遺漏僅保留低權重候選")
        if signal["tailBalance"] > 0.25:
            reasons.append(f"{number % 10}尾可平衡尾數")
        if metrics["consecutiveRun"].get(str(number), 0) >= 2:
            reasons.append("連續出現2期以上，已降權")
        if signal["returnRate"] >= 0.25:
            reasons.append("歷史回補率較佳")
        if not reasons:
            reasons.append("綜合權重與區間平衡入選")
        details.append(
            {
                "number": number,
                "rank": index + 1,
                "tier": 1 if index < 5 else 2 if index < 10 else 3,
                "score": round((base_scores[number] + _539_pool_balance(selected[:index], number, metrics)) * 100, 2),
                "reasons": reasons[:4],
                "reason": "、".join(reasons[:4]),
                "signals": {key: round(signal.get(key, 0.0), 4) for key in MODEL_539_FEATURE_KEYS},
            }
        )
    return {
        "full15": selected,
        "top10": selected[:10],
        "top5": selected[:5],
        "details": details,
        "features": features,
        "metrics": metrics,
        "weights": weights,
    }


def _539_metric_summary(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    if not rows:
        return {"hitRate": 0, "averageHit": 0, "twoPlusRate": 0}
    hits = [int(row[f"hits{key}"]) for row in rows]
    return {
        "hitRate": round(sum(hit >= 1 for hit in hits) / len(hits) * 100, 1),
        "averageHit": round(sum(hits) / len(hits), 2),
        "twoPlusRate": round(sum(hit >= 2 for hit in hits) / len(hits) * 100, 1),
    }


def _539_rolling_backtest(draws: list[dict[str, Any]], max_number: int = 39) -> dict[str, Any]:
    ordered = _539_ordered(draws, BACKTEST_539_WINDOW)
    snapshots = []
    rows = []
    start = max(BACKTEST_539_MIN_TRAIN, len(ordered) - BACKTEST_539_WINDOW)
    for target_index in range(start, len(ordered)):
        training = ordered[:target_index]
        context = training[-MODEL_539_WINDOW:]
        if len(context) < 30:
            continue
        # The live model learns from a longer recent sample.  Rolling
        # backtesting repeats this step hundreds of times, so use a compact
        # recent update at each node while keeping the full 300-draw context
        # and every target draw strictly out of the features.
        weights, _ = _539_learn_weights(training[-BACKTEST_539_WINDOW:], max_updates=8)
        model = _539_candidate_model(context, weights)
        actual = set(ordered[target_index]["numbers"])
        row = {
            "period": ordered[target_index].get("period", ""),
            "date": ordered[target_index].get("date", ""),
            "pick": model["top5"],
            "candidate10": model["top10"],
            "candidate15": model["full15"],
            "actual": sorted(actual),
            "hits5": len(set(model["top5"]) & actual),
            "hits10": len(set(model["top10"]) & actual),
            "hits15": len(set(model["full15"]) & actual),
        }
        row["hits"] = row["hits5"]
        rows.append(row)
        snapshots.append((model["features"], weights, actual))

    tier5 = _539_metric_summary(rows, "5")
    tier10 = _539_metric_summary(rows, "10")
    tier15 = _539_metric_summary(rows, "15")
    distribution = {str(number): 0 for number in range(6)}
    for row in rows:
        distribution[str(row["hits5"])] += 1
    weight_impact = {}
    for key in MODEL_539_FEATURE_KEYS:
        ablated_rows = []
        for features, weights, actual in snapshots:
            reduced = dict(weights)
            reduced[key] = 0.0
            ranking = _539_rank(features, reduced)
            ablated_rows.append({"hits15": len(set(ranking[:15]) & actual)})
        ablated = _539_metric_summary(ablated_rows, "15")
        weight_impact[key] = {
            "learnedWeight": round((snapshots[-1][1].get(key, 0.0) if snapshots else 0.0) * 100, 2),
            "averageHit15Impact": round(tier15["averageHit"] - ablated["averageHit"], 3),
            "hitRate15Impact": round(tier15["hitRate"] - ablated["hitRate"], 2),
        }
    latest_rows = list(reversed(rows[-12:]))
    return {
        "testedCount": len(rows),
        "trainingWindow": 300,
        "sourceWindow": min(len(ordered), BACKTEST_539_WINDOW),
        "averageHit": tier5["averageHit"],
        "onePlusCount": sum(row["hits5"] >= 1 for row in rows),
        "onePlusRate": tier5["hitRate"],
        "twoPlusCount": sum(row["hits5"] >= 2 for row in rows),
        "twoPlusRate": tier5["twoPlusRate"],
        "threePlusCount": sum(row["hits5"] >= 3 for row in rows),
        "threePlusRate": round(sum(row["hits5"] >= 3 for row in rows) / len(rows) * 100, 1) if rows else 0,
        "bestHit": max((row["hits5"] for row in rows), default=0),
        "distribution": distribution,
        "recentRows": latest_rows,
        "tierMetrics": {"5": tier5, "10": tier10, "15": tier15},
        "hitRate5": tier5["hitRate"],
        "hitRate10": tier10["hitRate"],
        "hitRate15": tier15["hitRate"],
        "averageHit5": tier5["averageHit"],
        "averageHit10": tier10["averageHit"],
        "averageHit15": tier15["averageHit"],
        "weightImpact": weight_impact,
        "learningUpdatesPerStep": 8,
        "method": "539 滾動回測：近500期資料，每次以前300期建立候選池，再逐期向前測到最新；15碼、10碼、5碼分開統計，未使用目標期之後資料。",
    }


def _analyze_539_candidate_model(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    source_rows = _539_ordered(draws, BACKTEST_539_WINDOW)
    context = source_rows[-MODEL_539_WINDOW:]
    weights, learning = _539_learn_weights(source_rows, max_updates=48)
    model = _539_candidate_model(context, weights)
    backtest = _539_rolling_backtest(source_rows, max_number=max_number)
    stats = number_stats(list(reversed(context)), max_number)
    frequency = stats["frequency"]
    gaps = stats["gaps"]
    hot = sorted(frequency, key=lambda number: (-frequency[number], number))[:10]
    cold = sorted(frequency, key=lambda number: (frequency[number], number))[:10]
    overdue = sorted(gaps, key=lambda number: (-gaps[number], number))[:10]
    patterns = pattern_summary(list(reversed(context)), max_number, "539-adaptive-300")
    details_by_number = {item["number"]: item for item in model["details"]}
    recommendation_roles = [
        {"number": number, "rank": rank, "score": details_by_number.get(number, {}).get("score", 0)}
        for rank, number in enumerate(model["top5"], start=1)
    ]
    quality = round(backtest["averageHit15"] * 100 + backtest["hitRate15"] * 0.35 + backtest["hitRate10"] * 0.25 + backtest["hitRate5"] * 0.2, 2)
    return {
        "drawCount": len(source_rows),
        "selectedDrawCount": len(context),
        "hot": [{"number": number, "count": frequency[number]} for number in hot],
        "cold": [{"number": number, "count": frequency[number]} for number in cold],
        "overdue": [{"number": number, "gap": gaps[number]} for number in overdue],
        "frequency": [{"number": number, "count": frequency[number], "gap": gaps[number]} for number in frequency],
        "recommendation": model["top5"],
        "recommendationRoles": recommendation_roles,
        "candidateTiers": {"top5": model["top5"], "top10": model["top10"], "full15": model["full15"]},
        "candidateDetails": model["details"],
        "modelWeights": model["weights"],
        "weightLearning": learning,
        "analysisMetrics": model["metrics"],
        "backtest": backtest,
        "modelProfiles": [{
            "id": "539-adaptive-300",
            "label": "539 300期自適應候選池",
            "quality": quality,
            "averageHit": backtest["averageHit5"],
            "onePlusRate": backtest["hitRate5"],
            "twoPlusRate": backtest["twoPlusRate"],
            "threePlusRate": backtest["threePlusRate"],
            "bestHit": backtest["bestHit"],
            "testedCount": backtest["testedCount"],
            "candidate15HitRate": backtest["hitRate15"],
        }],
        "patterns": patterns,
        "strategy": {
            "id": "539-adaptive-300",
            "label": "539 自適應 15 碼候選池",
            "summary": "以候選池覆蓋率為目標：近30期最重、近100期次重、近300期作背景，再以遺漏、回補、尾數與結構平衡修正。",
            "steps": [
                "近30期權重最高，近100期次高，近300期最低",
                "熱號、冷號、遺漏與回補率一起計分，不單追熱",
                "連莊2期以上自動降權，尾數過度集中時重新平衡",
                "校正奇偶、大小、三區、和值、跨度、AC、同尾、連號與上期重複",
                "以近500期滾動回測結果自動調整各項權重",
            ],
            "candidatePool": model["full15"],
            "variant": "adaptive-300",
        },
        "note": "本模型目標是提高15碼候選池的歷史覆蓋率，不是宣稱預測確定開出的號碼。每期更新後會依回測命中結果重新學習權重；彩券仍是隨機事件，不能保證中獎。",
    }


def analyze_california(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    stats = number_stats(draws, max_number)
    frequency = stats["frequency"]
    gaps = stats["gaps"]
    hot = sorted(frequency, key=lambda number: (-frequency[number], number))[:10]
    cold = sorted(frequency, key=lambda number: (frequency[number], number))[:10]
    overdue = sorted(gaps, key=lambda number: (-gaps[number], number))[:10]
    logic = california_logic_scores(draws, max_number=max_number)
    recommendation = california_recommendation(draws, max_number=max_number, pick_count=pick_count)
    recommendation_roles = [
        {"number": number, "rank": rank, "score": logic["scores"].get(number, 0)}
        for rank, number in enumerate(
            sorted(recommendation, key=lambda number: (-logic["scores"].get(number, 0), number)),
            start=1,
        )
    ]
    backtest = california_rolling_backtest(draws, max_number=max_number, pick_count=pick_count)
    quality = round(
        backtest["averageHit"] * 100
        + backtest["twoPlusRate"] * 1.35
        + backtest["threePlusRate"] * 2.6
        + backtest["bestHit"] * 14,
        2,
    )
    patterns = pattern_summary(draws, max_number, "california-special")
    patterns["selectedProfile"] = "california-special"
    patterns["selectedLabel"] = "天天樂專屬整合邏輯"
    return {
        "drawCount": len(draws),
        "hot": [{"number": number, "count": frequency[number]} for number in hot],
        "cold": [{"number": number, "count": frequency[number]} for number in cold],
        "overdue": [{"number": number, "gap": gaps[number]} for number in overdue],
        "frequency": [{"number": number, "count": frequency[number], "gap": gaps[number]} for number in frequency],
        "recommendation": recommendation,
        "recommendationRoles": recommendation_roles,
        "backtest": backtest,
        "modelProfiles": [
            {
                "id": "california-special",
                "label": "天天樂專屬整合邏輯",
                "quality": quality,
                "averageHit": backtest["averageHit"],
                "onePlusRate": backtest["onePlusRate"],
                "twoPlusRate": backtest["twoPlusRate"],
                "threePlusRate": backtest["threePlusRate"],
                "bestHit": backtest["bestHit"],
                "testedCount": backtest["testedCount"],
            }
        ],
        "patterns": patterns,
        "strategy": {
            "id": "ca-fantasy5-short-cycle",
            "label": "天天樂專屬整合邏輯",
            "summary": "近3期邊號與熱度為主，交叉近5/10期頻率、近20期背景與尾數新鮮度。",
            "steps": [
                "近3期：抓開獎號碼周圍的邊號動能",
                "近5/10期：抓短期熱號與連續出現訊號",
                "近20期：確認短期熱度不是單一期噪音",
                "20期以上未開與冷尾只降權，不硬排除",
                "依天天樂專屬分數排序取最高5碼",
            ],
            "downweightedNumbers": logic["blockedNumbers"],
            "downweightedTails": logic["blockedTails"],
            "candidatePool": sorted(logic["scores"], key=lambda number: (-logic["scores"][number], number))[:15],
        },
        "note": "加州天天樂採用獨立短週期邏輯：近3期邊號、近5/10期熱度、近20期背景與尾數新鮮度交叉；長遺漏只降權，不代表必開或必不開。彩券每期仍是隨機事件，不能保證中獎。",
    }


def analyze_california_with_stable_backtest(
    draws: list[dict[str, Any]],
    backtest_draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
) -> dict[str, Any]:
    analysis = analyze_california(draws, max_number=max_number, pick_count=pick_count)
    if analysis["backtest"].get("testedCount") or len(backtest_draws) < BACKTEST_MIN_HISTORY:
        return analysis
    fallback = california_rolling_backtest(
        backtest_draws[:BACKTEST_FALLBACK_LIMIT],
        max_number=max_number,
        pick_count=pick_count,
    )
    analysis["backtest"] = fallback
    analysis["modelProfiles"][0].update(
        {
            "quality": round(
                fallback["averageHit"] * 100
                + fallback["twoPlusRate"] * 1.35
                + fallback["threePlusRate"] * 2.6
                + fallback["bestHit"] * 14,
                2,
            ),
            "averageHit": fallback["averageHit"],
            "onePlusRate": fallback["onePlusRate"],
            "twoPlusRate": fallback["twoPlusRate"],
            "threePlusRate": fallback["threePlusRate"],
            "bestHit": fallback["bestHit"],
            "testedCount": fallback["testedCount"],
        }
    )
    analysis["backtest"]["method"] = (
        f"目前選擇近 {len(draws)} 期，短期樣本不足以單獨回測；"
        f"天天樂專屬回測改用近 {min(len(backtest_draws), BACKTEST_FALLBACK_LIMIT)} 期穩定樣本。"
        f"{fallback.get('method', '')}"
    )
    return analysis


def rolling_backtest(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5, profile_name: str = "balanced") -> dict[str, Any]:
    ordered = list(draws)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    distribution = {str(n): 0 for n in range(pick_count + 1)}
    rows = []
    sample_size = min(BACKTEST_SAMPLE_LIMIT, max(0, len(ordered) - 25))
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
    return {
        "testedCount": tested,
        "averageHit": round(hit_sum / tested, 2) if tested else 0,
        "onePlusCount": one_plus,
        "onePlusRate": round((one_plus / tested) * 100, 1) if tested else 0,
        "twoPlusCount": two_plus,
        "twoPlusRate": round((two_plus / tested) * 100, 1) if tested else 0,
        "threePlusCount": three_plus,
        "threePlusRate": round((three_plus / tested) * 100, 1) if tested else 0,
        "bestHit": best_hit,
        "distribution": distribution,
        "recentRows": rows[:10],
        "method": f"每一期只用該期以前的歷史資料產生推薦，再與實際開獎比對；目前採用「{MODEL_PROFILES.get(profile_name, MODEL_PROFILES['balanced'])['label']}」。",
    }


def choose_model_profile(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    results = []
    for profile_name, config in MODEL_PROFILES.items():
        backtest = rolling_backtest(draws, max_number=max_number, pick_count=pick_count, profile_name=profile_name)
        quality = (
            backtest["averageHit"] * 100
            + backtest["onePlusRate"] * 0.55
            + backtest["twoPlusRate"] * 1.35
            + backtest["threePlusRate"] * 2.6
            + backtest["bestHit"] * 14
            + backtest["distribution"].get("2", 0) * 2.1
        )
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
            }
        )
    results.sort(key=lambda item: (-item["quality"], -item["averageHit"], -item["threePlusRate"], item["id"]))
    selected = results[0]["id"] if results else "balanced"
    return selected, rolling_backtest(draws, max_number=max_number, pick_count=pick_count, profile_name=selected), results


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
    }


def analyze(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    stats = number_stats(draws, max_number)
    frequency = stats["frequency"]
    gaps = stats["gaps"]
    hot = sorted(frequency, key=lambda n: (-frequency[n], n))[:10]
    cold = sorted(frequency, key=lambda n: (frequency[n], n))[:10]
    overdue = sorted(gaps, key=lambda n: (-gaps[n], n))[:10]
    selected_variant, backtest, model_results = choose_tw539_strategy(
        draws,
        max_number=max_number,
        pick_count=pick_count,
    )
    logic = tw539_logic_scores(draws, max_number=max_number, variant=selected_variant)
    recommendation = tw539_recommendation(
        draws,
        max_number=max_number,
        pick_count=pick_count,
        variant=selected_variant,
    )
    recommendation_scores = logic["scores"]
    recommendation_roles = [
        {"number": number, "rank": rank, "score": round(recommendation_scores.get(number, 0), 4)}
        for rank, number in enumerate(
            sorted(recommendation, key=lambda number: (-recommendation_scores.get(number, 0), number)),
            start=1,
        )
    ]
    patterns = pattern_summary(draws, max_number, f"tw539-{selected_variant}")
    patterns["selectedProfile"] = f"tw539-{selected_variant}"
    patterns["selectedLabel"] = TW539_VARIANTS.get(selected_variant, TW539_VARIANTS["cycle"])["label"]

    return {
        "drawCount": len(draws),
        "hot": [{"number": n, "count": frequency[n]} for n in hot],
        "cold": [{"number": n, "count": frequency[n]} for n in cold],
        "overdue": [{"number": n, "gap": gaps[n]} for n in overdue],
        "frequency": [{"number": n, "count": frequency[n], "gap": gaps[n]} for n in frequency],
        "recommendation": recommendation,
        "recommendationRoles": recommendation_roles,
        "backtest": backtest,
        "modelProfiles": model_results,
        "patterns": patterns,
        "strategy": {
            "id": f"tw539-{selected_variant}",
            "label": TW539_VARIANTS.get(selected_variant, TW539_VARIANTS["cycle"])["label"],
            "summary": TW539_VARIANTS.get(selected_variant, TW539_VARIANTS["cycle"])["summary"],
            "steps": [
                "近 14 期：抓短期熱度與近期動能",
                "近 36 期：確認熱度是否只是短暫波動",
                "遺漏週期：用軟性回補分數，不硬指定必開",
                "上期鄰近、拖牌與尾數：只作輔助加分",
                "用滾動回測比較路線，再產生本期 5 碼",
            ],
            "candidatePool": logic["candidatePool"],
            "variant": selected_variant,
        },
        "note": "539 採用獨立的短期熱度、遺漏週期、鄰近號、尾數與版路輕量整合；回測只使用當時以前的資料。彩券每期仍是隨機事件，任何模型都不能保證命中。",
    }


def choose_best_analysis_window(
    history: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
    game: str = "tw539",
) -> dict[str, Any] | None:
    ordered = list(history)
    ordered.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    candidates = [window for window in AUTO_WINDOW_CANDIDATES if window <= len(ordered)]
    rows = []
    for window in candidates:
        window_draws = ordered[:window]
        if game == "ca-fantasy5":
            pick = california_recommendation(window_draws, max_number=max_number, pick_count=pick_count)
            backtest = california_rolling_backtest(window_draws, max_number=max_number, pick_count=pick_count)
            logic_scores = california_logic_scores(window_draws, max_number=max_number)["scores"]
            model_label = "天天樂專屬整合邏輯"
        else:
            variant, backtest, _ = choose_tw539_strategy(
                window_draws,
                max_number=max_number,
                pick_count=pick_count,
            )
            pick = tw539_recommendation(
                window_draws,
                max_number=max_number,
                pick_count=pick_count,
                variant=variant,
            )
            logic_scores = tw539_logic_scores(window_draws, max_number=max_number, variant=variant)["scores"]
            model_label = TW539_VARIANTS.get(variant, TW539_VARIANTS["cycle"])["label"]
        if backtest.get("testedCount", 0) < 5:
            continue
        roles = [
            {"number": number, "rank": rank, "score": round(logic_scores.get(number, 0), 4)}
            for rank, number in enumerate(sorted(pick, key=lambda number: (-logic_scores.get(number, 0), number)), start=1)
        ]
        quality = round(
            backtest.get("averageHit", 0) * 100
            + backtest.get("twoPlusRate", 0) * 1.25
            + backtest.get("threePlusRate", 0) * 2.5
            + backtest.get("bestHit", 0) * 12
            + backtest.get("distribution", {}).get("2", 0) * 0.5,
            2,
        )
        rows.append(
            {
                "limit": window,
                "quality": quality,
                "recommendation": pick,
                "recommendationRoles": roles,
                "backtest": backtest,
                "modelLabel": model_label,
            }
        )
    if not rows:
        return None
    best = max(
        rows,
        key=lambda row: (
            row["quality"],
            row["backtest"].get("averageHit", 0),
            row["backtest"].get("twoPlusRate", 0),
            row["backtest"].get("bestHit", 0),
            -row["limit"],
        ),
    )
    return {
        **best,
        "comparedWindows": [row["limit"] for row in rows],
        "method": (
            "天天樂專屬邏輯比較多個分析窗口，依平均命中、2 中以上比例與最高命中綜合排序。"
            if game == "ca-fantasy5"
            else "539 專屬邏輯比較多個分析窗口，依至少 1 中、平均命中、2 中以上比例與最高命中綜合排序。"
        ),
    }


def analyze(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    """Public 539 entry point: optimize the candidate pool, not one exact pick."""
    return _analyze_539_candidate_model(draws, max_number=max_number, pick_count=pick_count)


def analyze_with_stable_backtest(
    draws: list[dict[str, Any]],
    backtest_draws: list[dict[str, Any]],
    max_number: int = 39,
    pick_count: int = 5,
) -> dict[str, Any]:
    # The selected display window remains a UI preference.  The 539 engine
    # always trains from the latest 300 and validates on the latest 500.
    training_rows = backtest_draws[:BACKTEST_539_WINDOW] or draws
    analysis = _analyze_539_candidate_model(training_rows, max_number=max_number, pick_count=pick_count)
    analysis["selectedDrawCount"] = len(draws)
    return analysis


# ---------------------------------------------------------------------------
# Independent multi-model candidate-pool engine
# ---------------------------------------------------------------------------
# The production path below deliberately avoids random sampling.  Lottery
# draws are sparse observations, so Random Forest/XGBoost/Monte Carlo/GA are
# not added just to make the model list look impressive.  The active models
# are deterministic and can be audited against the same rolling backtest.
MODEL_ENGINE_VERSION = "2026.07-multimodel-candidate-pool-v1"
MODEL_WINDOWS = (30, 100, 300, 1000, 5000)
MODEL_ANALYSIS_DATA_WINDOW = 5000
MODEL_TRAIN_WINDOW = 300
# Keep the rolling source at 1,000 draws (300 train + 700 targets).  This
# gives a genuine 1,000-draw qualification sample while keeping first-load
# latency reasonable on the small Render instance.
MODEL_EVAL_WINDOW = 700
MODEL_MIN_QUALIFY_HISTORY = 1000
MODEL_RETRAIN_EVERY = 100
MODEL_STATE_FILE = Path(os.environ.get("LOTTO_MODEL_STATE_FILE", ROOT / "data" / "model_state.json"))
MODEL_PREDICTIONS_FILE = Path(os.environ.get("LOTTO_PREDICTIONS_FILE", ROOT / "data" / "prediction_history.json"))
MODEL_BACKTEST_CACHE_FILE = Path(os.environ.get("LOTTO_BACKTEST_CACHE_FILE", ROOT / "data" / "multimodel_backtest_cache.json"))
MODEL_STATE_LOCK = threading.Lock()
MODEL_BACKTEST_JOBS: set[str] = set()
MODEL_NAMES = ("bayesian", "logistic", "boosted", "markov")
MODEL_LABELS = {
    "bayesian": "Bayesian 機率平滑",
    "logistic": "校準 Logistic",
    "boosted": "Boosted 特徵集成",
    "markov": "Markov 轉移",
}
EXCLUDED_MODELS = {
    "random_forest": "目前不引入外部樹模型依賴；先以滾動回測證明特徵有效，再考慮加入。",
    "xgboost": "彩票樣本特徵稀疏，且目前環境沒有 XGBoost；避免未驗證依賴。",
    "lightgbm": "彩票樣本特徵稀疏，且目前環境沒有 LightGBM；避免未驗證依賴。",
    "catboost": "彩票樣本特徵稀疏，且目前環境沒有 CatBoost；避免未驗證依賴。",
    "monte_carlo": "依使用者要求不使用隨機亂數或隨機模擬。",
    "genetic_algorithm": "依使用者要求不使用隨機亂數；改用可重現的 Bayesian model averaging。",
}


def _mm_safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _mm_clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, _mm_safe_float(value)))


def _mm_rows(rows: list[dict[str, Any]], max_number: int = 39, limit: int = 5000) -> list[dict[str, Any]]:
    clean = []
    for row in rows or []:
        try:
            normalized = validate_draw(row)
        except (TypeError, ValueError):
            continue
        if all(1 <= number <= max_number for number in normalized["numbers"]):
            clean.append(normalized)
    clean.sort(key=lambda item: (item["date"], item["period"]))
    return clean[-limit:]


def _mm_window_counts(rows: list[dict[str, Any]], max_number: int, window: int) -> dict[int, int]:
    counts = {number: 0 for number in range(1, max_number + 1)}
    for row in rows[-window:]:
        for number in row["numbers"]:
            if number in counts:
                counts[number] += 1
    return counts


def _mm_prime(number: int) -> bool:
    if number < 2:
        return False
    divisor = 2
    while divisor * divisor <= number:
        if number % divisor == 0:
            return False
        divisor += 1
    return True


def _mm_ac(numbers: list[int]) -> int:
    values = sorted(numbers)
    differences = {right - left for index, left in enumerate(values) for right in values[index + 1 :]}
    return max(0, len(differences) - max(0, len(values) - 1))


def _mm_shape(numbers: list[int], max_number: int = 39) -> dict[str, Any]:
    values = sorted(int(number) for number in numbers)
    tails = [number % 10 for number in values]
    return {
        "odd": sum(number % 2 for number in values),
        "small": sum(number <= max_number // 2 for number in values),
        "zones": [
            sum(1 for number in values if 1 <= number <= 13),
            sum(1 for number in values if 14 <= number <= 26),
            sum(1 for number in values if 27 <= number <= max_number),
        ],
        "routes": [sum(1 for number in values if number % 3 == route) for route in range(3)],
        "prime": sum(_mm_prime(number) for number in values),
        "sum": sum(values),
        "span": values[-1] - values[0] if values else 0,
        "ac": _mm_ac(values),
        "sameTail": sum(max(0, tails.count(tail) - 1) for tail in set(tails)),
        "consecutive": sum(right - left == 1 for left, right in zip(values, values[1:])),
    }


def _mm_average_shape(rows: list[dict[str, Any]], max_number: int = 39) -> dict[str, Any]:
    shapes = [_mm_shape(row["numbers"], max_number) for row in rows]
    if not shapes:
        return {"odd": 2.5, "small": 2.5, "zones": [5 / 3] * 3, "routes": [5 / 3] * 3, "prime": 2, "sum": 100, "span": 30, "ac": 7, "sameTail": 1, "consecutive": 0.5}
    keys = ("odd", "small", "prime", "sum", "span", "ac", "sameTail", "consecutive")
    result = {key: round(sum(shape[key] for shape in shapes) / len(shapes), 4) for key in keys}
    for key in ("zones", "routes"):
        result[key] = [round(sum(shape[key][index] for shape in shapes) / len(shapes), 4) for index in range(3)]
    return result


def _mm_stats(rows: list[dict[str, Any]], max_number: int = 39) -> dict[str, Any]:
    ordered = _mm_rows(rows, max_number=max_number, limit=5000)
    recent = ordered[-300:]
    latest = set(ordered[-1]["numbers"]) if ordered else set()
    window_counts = {str(window): _mm_window_counts(ordered, max_number, window) for window in MODEL_WINDOWS}
    occurrence_indexes = {number: [] for number in range(1, max_number + 1)}
    for index, row in enumerate(ordered):
        for number in row["numbers"]:
            if number in occurrence_indexes:
                occurrence_indexes[number].append(index)
    omission = {}
    average_omission = {}
    maximum_omission = {}
    return_rate = {}
    repeat_rate = {}
    max_run = {}
    for number, indexes in occurrence_indexes.items():
        omission[number] = len(ordered) - 1 - indexes[-1] if indexes else len(ordered)
        gaps = [right - left - 1 for left, right in zip(indexes, indexes[1:])]
        average_omission[number] = round(sum(gaps) / len(gaps), 4) if gaps else float(len(ordered))
        maximum_omission[number] = max([*gaps, omission[number]] or [len(ordered)])
        eligible = [gap for gap in gaps if 8 <= gap <= 15]
        return_rate[number] = round(sum(8 <= gap <= 15 for gap in gaps) / len(gaps), 4) if gaps else 0.0
        consecutive_hits = sum(1 for left, right in zip(indexes, indexes[1:]) if right == left + 1)
        repeat_rate[number] = round(consecutive_hits / max(1, len(indexes) - 1), 4)
        run = 0
        for row in reversed(ordered):
            if number in row["numbers"]:
                run += 1
            else:
                break
        max_run[number] = run
    pair_counts: dict[tuple[int, int], int] = {}
    for row in recent:
        values = sorted(row["numbers"])
        for left, right in itertools.combinations(values, 2):
            pair_counts[(left, right)] = pair_counts.get((left, right), 0) + 1
    cooccurrence = {number: 0 for number in range(1, max_number + 1)}
    for (left, right), count in pair_counts.items():
        cooccurrence[left] += count
        cooccurrence[right] += count
    tail_counts = {tail: 0 for tail in range(10)}
    for row in recent:
        for number in row["numbers"]:
            tail_counts[number % 10] += 1
    shapes = [_mm_shape(row["numbers"], max_number) for row in recent]
    tail_presence = {tail: sum(1 for row in recent if any(number % 10 == tail for number in row["numbers"])) for tail in range(10)}
    same_tail_rate = sum(shape["sameTail"] > 0 for shape in shapes) / max(1, len(shapes))
    consecutive_rate = sum(shape["consecutive"] > 0 for shape in shapes) / max(1, len(shapes))
    previous_repeat_rate = sum(bool(set(left["numbers"]) & set(right["numbers"])) for left, right in zip(ordered, ordered[1:])) / max(1, len(ordered) - 1)
    weekday_counts = {str(day): 0 for day in range(7)}
    month_counts = {str(month): 0 for month in range(1, 13)}
    year_counts = {}
    for row in ordered:
        try:
            weekday_counts[str(datetime.strptime(row["date"], "%Y-%m-%d").weekday())] += 1
        except ValueError:
            pass
        month_counts[str(int(row["date"][5:7]))] += 1
        year_counts[row["date"][:4]] = year_counts.get(row["date"][:4], 0) + 1
    all_shapes = [_mm_shape(row["numbers"], max_number) for row in ordered]
    sums = [shape["sum"] for shape in all_shapes]
    spans = [shape["span"] for shape in all_shapes]
    ac_values = [shape["ac"] for shape in all_shapes]
    return {
        "count": len(ordered),
        "windows": {window: {number: window_counts[str(window)][number] for number in range(1, max_number + 1)} for window in MODEL_WINDOWS},
        "occurrences": occurrence_indexes,
        "omission": omission,
        "averageOmission": average_omission,
        "maximumOmission": maximum_omission,
        "returnRate": return_rate,
        "repeatRate": repeat_rate,
        "currentRun": max_run,
        "cooccurrence": cooccurrence,
        "pairCounts": {f"{left}-{right}": count for (left, right), count in sorted(pair_counts.items(), key=lambda item: (-item[1], item[0]))[:50]},
        "tailCounts": tail_counts,
        "tailPresence": tail_presence,
        "weekdayCounts": weekday_counts,
        "monthCounts": month_counts,
        "yearCounts": year_counts,
        "shapeAverage": _mm_average_shape(recent, max_number),
        "historicalShapeAverage": _mm_average_shape(ordered, max_number),
        "shapeRates": {"sameTail": round(same_tail_rate, 4), "consecutive": round(consecutive_rate, 4), "previousRepeat": round(previous_repeat_rate, 4)},
        "sumAverage": round(sum(sums) / len(sums), 4) if sums else 0,
        "spanAverage": round(sum(spans) / len(spans), 4) if spans else 0,
        "acAverage": round(sum(ac_values) / len(ac_values), 4) if ac_values else 0,
        "sumRange": [min(sums), max(sums)] if sums else [0, 0],
        "spanRange": [min(spans), max(spans)] if spans else [0, 0],
        "acRange": [min(ac_values), max(ac_values)] if ac_values else [0, 0],
        "latest": sorted(latest),
    }


def _mm_norm(value: float, values: list[float]) -> float:
    if not values:
        return 0.5
    low, high = min(values), max(values)
    if high == low:
        return 0.5
    return _mm_clamp((value - low) / (high - low))


def _mm_feature_rows(stats: dict[str, Any], max_number: int = 39) -> tuple[list[str], dict[int, dict[str, float]]]:
    keys = ("recent30", "recent100", "recent300", "recent1000", "recent5000", "omissionFit", "averageOmissionFit", "maximumOmissionFit", "returnRate", "repeatRate", "cooccurrence", "tailBalance", "oddBalance", "sizeBalance", "routeBalance", "primeBalance", "zoneBalance", "previousRepeat", "neighborSignal")
    omission = stats["omission"]
    avg_gap = stats["averageOmission"]
    max_gap = stats["maximumOmission"]
    windows = stats["windows"]
    recent = windows.get(30, {})
    latest = set(stats["latest"])
    expected = max(1, stats["count"] * 5 / max_number)
    tail_counts = stats["tailCounts"]
    tail_target = max(1, stats["count"] * 5 / 10)
    shape = stats["shapeAverage"]
    historical = stats["historicalShapeAverage"]
    zone_targets = shape["zones"]
    route_targets = shape["routes"]
    window_values = {window: [windows.get(window, {}).get(n, 0) for n in range(1, max_number + 1)] for window in MODEL_WINDOWS}
    max_cooccurrence = max(stats["cooccurrence"].values() or [1])
    rows = {}
    for number in range(1, max_number + 1):
        tail = number % 10
        zone = 0 if number <= 13 else 1 if number <= 26 else 2
        count_values = [windows.get(window, {}).get(number, 0) for window in MODEL_WINDOWS]
        omission_value = omission.get(number, stats["count"])
        rows[number] = {
            "recent30": _mm_norm(count_values[0], window_values[30]),
            "recent100": _mm_norm(count_values[1], window_values[100]),
            "recent300": _mm_norm(count_values[2], window_values[300]),
            "recent1000": _mm_norm(count_values[3], window_values[1000]),
            "recent5000": _mm_norm(count_values[4], window_values[5000]),
            "omissionFit": 0.88 if 8 <= omission_value <= 15 else 0.18 if omission_value >= 25 else 0.55 if omission_value >= 16 else 0.45,
            "averageOmissionFit": _mm_clamp(1 - abs(omission_value - avg_gap.get(number, omission_value)) / max(10, stats["count"])),
            "maximumOmissionFit": _mm_clamp(1 - abs(omission_value - max_gap.get(number, omission_value)) / max(10, stats["count"])),
            "returnRate": _mm_clamp(stats["returnRate"].get(number, 0)),
            "repeatRate": _mm_clamp(stats["repeatRate"].get(number, 0)),
            "cooccurrence": _mm_norm(stats["cooccurrence"].get(number, 0), list(stats["cooccurrence"].values())),
            "tailBalance": _mm_clamp(1 - abs(tail_counts.get(tail, 0) - tail_target) / max(1, tail_target * 1.5)),
            "oddBalance": _mm_clamp(1 - abs((number % 2) - (shape["odd"] / 5)) * 1.4),
            "sizeBalance": _mm_clamp(1 - abs((number <= 19) - (shape["small"] / 5)) * 1.4),
            "routeBalance": _mm_clamp(1 - abs((number % 3) - min(range(3), key=lambda route: abs(route_targets[route] - historical["routes"][route]))) / 3),
            "primeBalance": _mm_clamp(1 - abs(int(_mm_prime(number)) - historical["prime"] / 5) * 1.2),
            "zoneBalance": _mm_clamp(1 - abs((zone_targets[zone] / 5) - (historical["zones"][zone] / 5)) * 1.5) if zone_targets else 0.5,
            "previousRepeat": 0.2 if number in latest and stats["currentRun"].get(number, 0) >= 2 else 0.75 if number in latest else 0.48,
            "neighborSignal": _mm_clamp(stats["cooccurrence"].get(number, 0) / max_cooccurrence),
        }
        # The same feature rows are used by all models; every window still
        # keeps its own evidence and is never collapsed into one frequency.
    return list(keys), rows


def _mm_fit_logistic(features: dict[int, dict[str, float]], keys: list[str], stats: dict[str, Any]) -> dict[str, float]:
    labels = {number: _mm_clamp(stats["windows"][300].get(number, 0) / max(1, min(300, stats["count"]))) for number in features}
    coeffs = {key: 0.0 for key in keys}
    intercept = 0.0
    for _ in range(28):
        gradient = {key: 0.0 for key in keys}
        intercept_gradient = 0.0
        for number in features:
            linear = intercept + sum(coeffs[key] * features[number][key] for key in keys)
            probability = 1 / (1 + math.exp(-max(-12, min(12, linear))))
            error = labels[number] - probability
            intercept_gradient += error
            for key in keys:
                gradient[key] += error * features[number][key]
        intercept += intercept_gradient / max(1, len(features)) * 0.22
        for key in keys:
            coeffs[key] += gradient[key] / max(1, len(features)) * 0.22
    return {"intercept": round(intercept, 6), **{key: round(value, 6) for key, value in coeffs.items()}}


def _mm_model_scores(stats: dict[str, Any], max_number: int = 39) -> tuple[dict[str, dict[int, float]], dict[int, dict[str, float]], dict[str, Any]]:
    keys, features = _mm_feature_rows(stats, max_number)
    logistic = _mm_fit_logistic(features, keys, stats)
    models = {name: {} for name in MODEL_NAMES}
    target_values = {number: _mm_clamp(stats["windows"][300].get(number, 0) / max(1, min(300, stats["count"]))) for number in features}
    for number, row in features.items():
        windows_score = 0.34 * row["recent30"] + 0.26 * row["recent100"] + 0.18 * row["recent300"] + 0.13 * row["recent1000"] + 0.09 * row["recent5000"]
        models["bayesian"][number] = _mm_clamp(windows_score * 0.65 + row["returnRate"] * 0.12 + row["omissionFit"] * 0.12 + row["tailBalance"] * 0.11)
        logistic_value = logistic["intercept"] + sum(logistic[key] * row[key] for key in keys)
        models["logistic"][number] = _mm_clamp(1 / (1 + math.exp(-max(-12, min(12, logistic_value)))))
        models["boosted"][number] = _mm_clamp(0.42 * windows_score + 0.18 * row["cooccurrence"] + 0.15 * row["zoneBalance"] + 0.13 * row["tailBalance"] + 0.12 * row["omissionFit"])
        transition = 0.35 * row["neighborSignal"] + 0.25 * row["returnRate"] + 0.2 * row["repeatRate"] + 0.2 * row["previousRepeat"]
        models["markov"][number] = _mm_clamp(transition * 0.68 + windows_score * 0.32)
    return models, features, {"logisticCoefficients": logistic, "featureKeys": keys, "targetRate": target_values}


def _mm_select_pool(scores: dict[int, float], stats: dict[str, Any], max_number: int = 39, size: int = 15) -> list[int]:
    ranked = sorted(scores, key=lambda number: (-scores[number], number))
    selected: list[int] = []
    target = stats["shapeAverage"]
    for _ in range(min(size, len(ranked))):
        best = None
        best_value = -float("inf")
        for number in ranked:
            if number in selected:
                continue
            draft = selected + [number]
            shape = _mm_shape(draft, max_number)
            penalty = 0.0
            penalty += abs(shape["odd"] / len(draft) - target["odd"] / 5) * 0.08
            penalty += abs(shape["small"] / len(draft) - target["small"] / 5) * 0.08
            penalty += sum(abs(shape["zones"][index] / len(draft) - target["zones"][index] / 5) for index in range(3)) * 0.035
            penalty += max(0, shape["sameTail"] - target["sameTail"]) * 0.012
            value = scores[number] - penalty
            if value > best_value:
                best_value = value
                best = number
        if best is None:
            break
        selected.append(best)
    return selected


def _mm_reasons(number: int, features: dict[int, dict[str, float]], stats: dict[str, Any]) -> list[str]:
    row = features[number]
    reasons = []
    if row["recent30"] >= 0.68:
        reasons.append("近30期證據較強")
    elif row["recent100"] >= 0.65:
        reasons.append("近100期中期支持")
    if 8 <= stats["omission"].get(number, 0) <= 15:
        reasons.append(f"遺漏{stats['omission'][number]}期，落在回補觀察區")
    if stats["currentRun"].get(number, 0) >= 2:
        reasons.append("連續出現2期以上，已自動降權")
    if row["returnRate"] >= 0.55:
        reasons.append("歷史回補率較高")
    if row["tailBalance"] >= 0.72:
        reasons.append(f"{number % 10}尾與近期分布較平衡")
    if row["cooccurrence"] >= 0.65:
        reasons.append("與近期共現結構相容")
    if not reasons:
        reasons.append("多視窗統計與結構條件交叉保留")
    return reasons[:3]


def _mm_model_weights(game: str, profiles: list[dict[str, Any]]) -> dict[str, float]:
    default = {"tw539": {"bayesian": 0.32, "logistic": 0.28, "boosted": 0.24, "markov": 0.16}, "ca-fantasy5": {"bayesian": 0.28, "logistic": 0.25, "boosted": 0.22, "markov": 0.25}}[game]
    if not profiles:
        return default
    raw = {}
    for profile in profiles:
        name = profile.get("id")
        recent = _mm_safe_float(profile.get("recent100AverageHit", profile.get("averageHit", 0)))
        overall = _mm_safe_float(profile.get("allHistoryAverageHit", profile.get("averageHit", 0)))
        raw[name] = max(0.03, 0.62 * recent + 0.38 * overall)
        if profile.get("status") == "downweighted":
            raw[name] *= 0.55
        if profile.get("status") == "retired":
            raw[name] *= 0.15
    total = sum(raw.values()) or 1.0
    return {name: round(raw.get(name, default[name]) / total, 6) for name in MODEL_NAMES}


def _mm_roll_backtest_full(rows: list[dict[str, Any]], game: str, max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    ordered = _mm_rows(rows, max_number=max_number, limit=MODEL_EVAL_WINDOW + MODEL_TRAIN_WINDOW)
    if len(ordered) < 2:
        return {"testedCount": 0, "method": "資料不足，尚未開始滾動回測。", "distribution": {}, "tierMetrics": {}}
    start = min(MODEL_TRAIN_WINDOW, max(1, len(ordered) - 1))
    model_hits = {name: [] for name in MODEL_NAMES}
    ensemble_hits = {5: [], 10: [], 15: []}
    recent_rows = []
    for target_index in range(start, len(ordered)):
        train = ordered[max(0, target_index - 5000):target_index]
        stats = _mm_stats(train, max_number)
        model_scores, _features, _meta = _mm_model_scores(stats, max_number)
        profiles_so_far = [{"id": name, "averageHit": sum(model_hits[name]) / len(model_hits[name]) if model_hits[name] else 0} for name in MODEL_NAMES]
        weights = _mm_model_weights(game, profiles_so_far)
        ensemble_scores = {number: sum(weights[name] * model_scores[name][number] for name in MODEL_NAMES) for number in range(1, max_number + 1)}
        pool = _mm_select_pool(ensemble_scores, stats, max_number, 15)
        actual = set(ordered[target_index]["numbers"])
        for name in MODEL_NAMES:
            picked = sorted(model_scores[name], key=lambda number: (-model_scores[name][number], number))[:15]
            model_hits[name].append(len(set(picked[:pick_count]) & actual))
        for tier in (5, 10, 15):
            ensemble_hits[tier].append(len(set(pool[:tier]) & actual))
        if len(recent_rows) < 10:
            recent_rows.append({"date": ordered[target_index]["date"], "period": ordered[target_index]["period"], "pick": pool[:5], "candidate15": pool, "actual": ordered[target_index]["numbers"], "hits": len(set(pool[:5]) & actual)})
    tested = len(ordered) - start
    def profile_for(name: str) -> dict[str, Any]:
        values = model_hits[name]
        return {"id": name, "label": MODEL_LABELS[name], "testedCount": len(values), "averageHit": round(sum(values) / max(1, len(values)), 3), "hitRate": round(sum(value >= 1 for value in values) / max(1, len(values)) * 100, 2), "hit1Rate": round(sum(value == 1 for value in values) / max(1, len(values)) * 100, 2), "hit2Rate": round(sum(value == 2 for value in values) / max(1, len(values)) * 100, 2), "hit3Rate": round(sum(value == 3 for value in values) / max(1, len(values)) * 100, 2), "hit4Rate": round(sum(value == 4 for value in values) / max(1, len(values)) * 100, 2), "hit5Rate": round(sum(value == 5 for value in values) / max(1, len(values)) * 100, 2), "recent100AverageHit": round(sum(values[-100:]) / max(1, len(values[-100:])), 3), "recent300AverageHit": round(sum(values[-300:]) / max(1, len(values[-300:])), 3), "allHistoryAverageHit": round(sum(values) / max(1, len(values)), 3), "bestHit": max(values or [0]), "status": "active"}
    profiles = [profile_for(name) for name in MODEL_NAMES]
    for profile in profiles:
        if len(ordered) >= MODEL_MIN_QUALIFY_HISTORY and profile["recent100AverageHit"] < profile["allHistoryAverageHit"] * 0.8:
            profile["status"] = "downweighted"
        if tested >= 200 and len(model_hits[profile["id"]][-200:]) == 200 and sum(model_hits[profile["id"]][-200:]) / 200 < profile["allHistoryAverageHit"] * 0.8:
            profile["status"] = "retired"
        profile["qualified"] = len(ordered) >= MODEL_MIN_QUALIFY_HISTORY
    distribution = {str(hit): 0 for hit in range(6)}
    for hit in ensemble_hits[5]:
        distribution[str(min(5, hit))] += 1
    def tier_metrics(tier: int) -> dict[str, Any]:
        values = ensemble_hits[tier]
        return {"testedCount": len(values), "hitRate": round(sum(value >= 1 for value in values) / max(1, len(values)) * 100, 2), "averageHit": round(sum(values) / max(1, len(values)), 3), "twoPlusRate": round(sum(value >= 2 for value in values) / max(1, len(values)) * 100, 2), "threePlusRate": round(sum(value >= 3 for value in values) / max(1, len(values)) * 100, 2), "bestHit": max(values or [0])}
    ensemble15 = ensemble_hits[15]
    recent20 = ensemble15[-20:]
    historical_average = sum(ensemble15) / max(1, len(ensemble15))
    return {"testedCount": tested, "trainWindow": MODEL_TRAIN_WINDOW, "sourceWindow": len(ordered), "qualificationHistory": len(ordered), "qualifiedForPromotion": len(ordered) >= MODEL_MIN_QUALIFY_HISTORY, "averageHit": round(sum(ensemble_hits[5]) / max(1, tested), 3), "onePlusRate": round(sum(value >= 1 for value in ensemble_hits[5]) / max(1, tested) * 100, 2), "twoPlusRate": round(sum(value >= 2 for value in ensemble_hits[5]) / max(1, tested) * 100, 2), "threePlusRate": round(sum(value >= 3 for value in ensemble_hits[5]) / max(1, tested) * 100, 2), "bestHit": max(ensemble_hits[5] or [0]), "distribution": distribution, "tierMetrics": {str(tier): tier_metrics(tier) for tier in (5, 10, 15)}, "modelProfiles": profiles, "recentRows": recent_rows, "monitoring": {"recent20AverageHit": round(sum(recent20) / max(1, len(recent20)), 3), "historicalAverageHit": round(historical_average, 3), "warning": bool(len(recent20) >= 20 and sum(recent20) / 20 < historical_average * 0.8)}, "weightImpact": {name: {"averageHit15Impact": round(sum(ensemble_hits[15]) / max(1, tested) - sum(model_hits[name]) / max(1, tested), 3)} for name in MODEL_NAMES}, "method": f"{game} 獨立滾動回測：每個目標期只使用之前最多5,000期，至少以前300期訓練，從第{start + 1}筆一路測到最新資料；候選池目標為15碼覆蓋率。"}


def _mm_backtest_signature(rows: list[dict[str, Any]], game: str, max_number: int = 39) -> str:
    ordered = _mm_rows(rows, max_number=max_number, limit=MODEL_TRAIN_WINDOW + MODEL_EVAL_WINDOW)
    if not ordered:
        return f"{game}:{MODEL_ENGINE_VERSION}:empty"
    return f"{game}:{MODEL_ENGINE_VERSION}:{len(ordered)}:{ordered[0]['period']}:{ordered[-1]['period']}"


def _mm_load_backtest_cache(signature: str) -> dict[str, Any] | None:
    cache = _mm_json_load(MODEL_BACKTEST_CACHE_FILE, {})
    if not isinstance(cache, dict):
        return None
    item = cache.get(signature)
    return item if isinstance(item, dict) and item.get("testedCount", 0) else None


def _mm_save_backtest_cache(signature: str, result: dict[str, Any]) -> None:
    cache = _mm_json_load(MODEL_BACKTEST_CACHE_FILE, {})
    if not isinstance(cache, dict):
        cache = {}
    cache[signature] = result
    # A signature is tied to the latest source period. Keep a small history so
    # a restart can reuse recent results without growing the runtime database.
    keys = list(cache)[-8:]
    _mm_json_save(MODEL_BACKTEST_CACHE_FILE, {key: cache[key] for key in keys})


def _mm_start_backtest_job(rows: list[dict[str, Any]], game: str, signature: str, max_number: int, pick_count: int) -> None:
    with MODEL_STATE_LOCK:
        if signature in MODEL_BACKTEST_JOBS:
            return
        MODEL_BACKTEST_JOBS.add(signature)

    def run() -> None:
        try:
            result = _mm_roll_backtest_full(rows, game, max_number, pick_count)
            _mm_save_backtest_cache(signature, result)
        except Exception as exc:
            print(f"multimodel backtest error ({game}): {exc}")
        finally:
            with MODEL_STATE_LOCK:
                MODEL_BACKTEST_JOBS.discard(signature)

    threading.Thread(target=run, name=f"multimodel-backtest-{game}", daemon=True).start()


def _mm_roll_backtest(rows: list[dict[str, Any]], game: str, max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    """Return cached full results immediately and warm a full result in background.

    The public API must remain responsive while the 700-target qualification
    run is being computed. A short rolling preview is used only until the
    complete result is persisted; it is never presented as fully qualified.
    """
    ordered = _mm_rows(rows, max_number=max_number, limit=MODEL_TRAIN_WINDOW + MODEL_EVAL_WINDOW)
    signature = _mm_backtest_signature(ordered, game, max_number)
    cached_result = _mm_load_backtest_cache(signature)
    if cached_result:
        return {**cached_result, "cacheStatus": "complete", "cacheSignature": signature}
    _mm_start_backtest_job(ordered, game, signature, max_number, pick_count)
    preview_size = min(len(ordered), MODEL_TRAIN_WINDOW + 40)
    preview = _mm_roll_backtest_full(ordered[-preview_size:], game, max_number, pick_count)
    preview["sourceWindow"] = len(ordered)
    preview["qualificationHistory"] = len(ordered)
    preview["qualifiedForPromotion"] = False
    preview["cacheStatus"] = "warming"
    preview["cacheSignature"] = signature
    preview["method"] = "完整滾動回測正在背景建立；目前先顯示短期預覽，完成後自動換成完整結果。"
    return preview


def _mm_json_load(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                return json.load(file)
    except (OSError, ValueError, TypeError):
        pass
    return fallback


def _mm_json_save(path: Path, value: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(path)
    except OSError:
        pass


def _mm_save_prediction(game: str, analysis: dict[str, Any], latest: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    with MODEL_STATE_LOCK:
        state = _mm_json_load(MODEL_PREDICTIONS_FILE, {"tw539": [], "ca-fantasy5": []})
        records = list(state.get(game, [])) if isinstance(state, dict) else []
        ordered = _mm_rows(history, 39, 5000)
        by_period = {str(row["period"]): row for row in ordered}
        for record in records:
            source_period = str(record.get("sourcePeriod", ""))
            if record.get("actual") or source_period not in by_period:
                continue
            source_index = next((index for index, row in enumerate(ordered) if str(row["period"]) == source_period), None)
            if source_index is not None and source_index + 1 < len(ordered):
                actual = ordered[source_index + 1]
                record["actual"] = actual["numbers"]
                record["actualPeriod"] = actual["period"]
                record["hits5"] = len(set(record.get("recommended", [])) & set(actual["numbers"]))
                record["hits10"] = len(set(record.get("backup", [])) & set(actual["numbers"]))
                record["hits15"] = len(set(record.get("candidate15", [])) & set(actual["numbers"]))
        source_period = str(latest.get("period", ""))
        if source_period and not any(str(record.get("sourcePeriod")) == source_period for record in records):
            records.insert(0, {"game": game, "sourcePeriod": source_period, "sourceDate": latest.get("date", ""), "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "modelVersion": analysis.get("modelVersion", MODEL_ENGINE_VERSION), "weights": analysis.get("modelWeights", {}), "recommended": analysis.get("recommendation", [])[:5], "backup": analysis.get("backupRecommendation", [])[:5], "candidate15": analysis.get("candidateTiers", {}).get("full15", [])[:15], "allModelScores": analysis.get("modelScores", {}), "actual": None})
        records = records[:5000]
        _mm_json_save(MODEL_PREDICTIONS_FILE, {**(state if isinstance(state, dict) else {}), game: records})
        latest_record = records[0] if records else None
        return {"count": len(records), "latest": latest_record}


def _mm_save_model_state(game: str, analysis: dict[str, Any], cycle: int) -> dict[str, Any]:
    """Persist independent learned state without making it part of the draw data."""
    with MODEL_STATE_LOCK:
        state = _mm_json_load(MODEL_STATE_FILE, {})
        if not isinstance(state, dict):
            state = {}
        state[game] = {
            "modelVersion": analysis.get("modelVersion", MODEL_ENGINE_VERSION),
            "lastTunedCycle": cycle,
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "weights": analysis.get("modelWeights", {}),
            "leaderboard": analysis.get("modelLeaderboard", []),
            "monitoring": analysis.get("monitoring", {}),
        }
        _mm_json_save(MODEL_STATE_FILE, state)
        return state[game]


def _mm_analysis(game: str, rows: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    source_rows = _mm_rows(rows, max_number=max_number, limit=5000)
    if not source_rows:
        return {"drawCount": 0, "selectedDrawCount": 0, "recommendation": [], "candidateTiers": {}, "backtest": {"testedCount": 0}, "note": "資料不足，暫時無法計算。"}
    stats = _mm_stats(source_rows, max_number)
    model_scores, features, model_meta = _mm_model_scores(stats, max_number)
    provisional_profiles = []
    backtest = _mm_roll_backtest(source_rows, game, max_number, pick_count)
    profiles = backtest.get("modelProfiles", [])
    weights = _mm_model_weights(game, profiles)
    ensemble_scores = {number: sum(weights[name] * model_scores[name][number] for name in MODEL_NAMES) for number in range(1, max_number + 1)}
    pool = _mm_select_pool(ensemble_scores, stats, max_number, 15)
    top5, backup5 = pool[:5], pool[5:10]
    ranked = sorted(ensemble_scores, key=lambda number: (-ensemble_scores[number], number))
    detail = []
    for rank, number in enumerate(pool, start=1):
        agreement = sum(number in sorted(model_scores[name], key=lambda candidate: (-model_scores[name][candidate], candidate))[:15] for name in MODEL_NAMES) / len(MODEL_NAMES)
        detail.append({"number": number, "rank": rank, "tier": 1 if rank <= 5 else 2 if rank <= 10 else 3, "score": round(ensemble_scores[number] * 100, 2), "confidence": round(_mm_clamp(0.45 * ensemble_scores[number] + 0.55 * agreement) * 100, 2), "reason": "、".join(_mm_reasons(number, features, stats)), "modelAgreement": round(agreement * 100, 2)})
    least = [{"number": number, "score": round(ensemble_scores[number] * 100, 2), "reason": "近期連續或結構失衡，且未取得多模型支持" if stats["currentRun"].get(number, 0) >= 2 else "多視窗分數與近期結構支持較弱"} for number in sorted(ensemble_scores, key=lambda number: (ensemble_scores[number], number))[:10]]
    frequency = stats["windows"][300]
    hot = sorted(frequency, key=lambda number: (-frequency[number], number))[:10]
    cold = sorted(frequency, key=lambda number: (frequency[number], number))[:10]
    overdue = sorted(stats["omission"], key=lambda number: (-stats["omission"][number], number))[:10]
    shapes = stats["shapeAverage"]
    consistency = sum(number in sorted(model_scores[name], key=lambda candidate: (-model_scores[name][candidate], candidate))[:15] for name in MODEL_NAMES for number in pool) / max(1, len(MODEL_NAMES) * len(pool))
    cycle = len(source_rows) // MODEL_RETRAIN_EVERY
    result = {"drawCount": len(source_rows), "selectedDrawCount": len(source_rows), "modelVersion": f"{MODEL_ENGINE_VERSION}-{game}", "windowsUsed": [window for window in MODEL_WINDOWS if len(source_rows) >= window or window == 30], "statistics": {"hot": hot, "cold": cold, "omission": stats["omission"], "averageOmission": stats["averageOmission"], "maximumOmission": stats["maximumOmission"], "returnRate": stats["returnRate"], "repeatRate": stats["repeatRate"], "tailCounts": stats["tailCounts"], "tailPresence": stats["tailPresence"], "oddEven": shapes["odd"], "smallLarge": shapes["small"], "zoneRatio": shapes["zones"], "routeRatio": shapes["routes"], "primeCount": shapes["prime"], "sumAverage": stats["sumAverage"], "spanAverage": stats["spanAverage"], "acAverage": stats["acAverage"], "sameTailRate": stats["shapeRates"]["sameTail"], "consecutiveRate": stats["shapeRates"]["consecutive"], "previousRepeatRate": stats["shapeRates"]["previousRepeat"], "weekdayCounts": stats["weekdayCounts"], "monthCounts": stats["monthCounts"], "crossYear": stats["yearCounts"], "pairCounts": stats["pairCounts"]}, "hot": [{"number": number, "count": frequency[number]} for number in hot], "cold": [{"number": number, "count": frequency[number]} for number in cold], "overdue": [{"number": number, "gap": stats["omission"][number]} for number in overdue], "frequency": [{"number": number, "count": frequency[number], "gap": stats["omission"][number]} for number in range(1, max_number + 1)], "recommendation": top5, "backupRecommendation": backup5, "recommendationRoles": [{"number": number, "rank": index, "score": next(item["score"] for item in detail if item["number"] == number)} for index, number in enumerate(top5, start=1)], "candidateTiers": {"top5": top5, "backup5": backup5, "top10": pool[:10], "full15": pool}, "candidateDetails": detail, "modelScores": {name: {str(number): round(score, 6) for number, score in scores.items()} for name, scores in model_scores.items()}, "modelWeights": weights, "modelLeaderboard": profiles, "modelProfiles": [{**profile, "quality": round(profile.get("allHistoryAverageHit", 0) * 100 + profile.get("hitRate", 0), 2)} for profile in profiles], "modelCatalog": {**{name: {"status": "active", "label": MODEL_LABELS[name]} for name in MODEL_NAMES}, **{name: {"status": "excluded", "reason": reason} for name, reason in EXCLUDED_MODELS.items()}}, "ensemble": {"overallConfidence": round(_mm_clamp(sum(ensemble_scores[number] for number in top5) / max(1, len(top5))) * 100, 2), "modelConsistency": round(consistency * 100, 2), "voteWeights": weights, "estimatedSum": round(sum(top5) * 1.0, 2), "estimatedSpan": max(top5) - min(top5) if top5 else 0, "estimatedAC": _mm_ac(top5), "estimatedOddEven": _mm_shape(top5, max_number)["odd"], "estimatedSmallLarge": _mm_shape(top5, max_number)["small"], "estimatedConsecutiveRate": round(stats["shapeRates"]["consecutive"] * 100, 2), "estimatedSameTailRate": round(stats["shapeRates"]["sameTail"] * 100, 2)}, "leastRecommended": least, "backtest": backtest, "automl": {"cycle": cycle, "retrainEvery": MODEL_RETRAIN_EVERY, "qualified": len(source_rows) >= MODEL_MIN_QUALIFY_HISTORY, "method": "每100期檢查一次，以滾動回測績效做 Bayesian model averaging；未通過1000期驗證的模型不升格為正式模型。"}, "monitoring": backtest.get("monitoring", {}), "featureLearning": {"featureKeys": model_meta["featureKeys"], "logisticCoefficients": model_meta["logisticCoefficients"]}, "patterns": pattern_summary(source_rows[-300:], max_number, f"{game}-multi-model"), "strategy": {"id": f"{game}-multi-model", "label": "候選池多模型集成", "summary": "目標是提高完整15碼候選池覆蓋率，再由5碼核心、5碼備選分層呈現。", "steps": ["近30期最高權重、近100期次高、近300期校正，資料足夠時納入近1000與近5000期", "熱冷、遺漏、回補、連莊、共現、尾數與奇偶大小共同計分，不單追熱", "連續出現2期以上自動降權；遺漏8~15期只適度加分，不硬性回補", "區間、012路、質數、和值、跨度、AC、同尾、連號與上期重複用來平衡候選池", "每期開獎後留存快照，滾動回測結果決定模型投票權重"], "candidatePool": pool, "variant": "independent-ensemble"}, "note": "本模型以候選15碼長期覆蓋率與5碼平均命中為目標，不宣稱預測必開號碼。各彩種獨立計算；沒有使用隨機亂數、幸運數字或單次結果改寫規則。彩券仍是隨機事件，請理性投注。"}
    result["modelState"] = _mm_save_model_state(game, result, cycle)
    return result


def analyze(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    return _mm_analysis("tw539", draws, max_number, pick_count)


def analyze_california(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    return _mm_analysis("ca-fantasy5", draws, max_number, pick_count)


def analyze_with_stable_backtest(draws: list[dict[str, Any]], backtest_draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    analysis = _mm_analysis("tw539", backtest_draws or draws, max_number, pick_count)
    analysis["selectedDrawCount"] = len(draws)
    return analysis


def analyze_california_with_stable_backtest(draws: list[dict[str, Any]], backtest_draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
    analysis = _mm_analysis("ca-fantasy5", backtest_draws or draws, max_number, pick_count)
    analysis["selectedDrawCount"] = len(draws)
    return analysis


def data_health(game: str, latest: dict[str, Any] | None, history: list[dict[str, Any]]) -> dict[str, Any]:
    valid_latest = False
    if latest:
        try:
            validate_draw(latest)
            valid_latest = True
        except (TypeError, ValueError):
            valid_latest = False
    return {
        "game": game,
        "validated": valid_latest and bool(history),
        "latestPeriod": latest.get("period", "") if latest else "",
        "latestDate": latest.get("date", "") if latest else "",
        "historyCount": len(history),
        "message": "資料已驗證" if valid_latest and history else "資料不足，暫不更新分析",
    }


def analysis_metadata(limit: int, data_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "engineVersion": ANALYSIS_ENGINE_VERSION,
        "analysisLimit": limit,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataValidated": bool(data_status.get("validated")),
    }


def attach_deep_sniper_analysis(
    game: str,
    analysis: dict[str, Any],
    latest: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    slot_start, slot_end = deep_analysis_slot()
    key = f"{game}:{latest.get('date', '')}:{latest.get('period', '')}:deep-8h-{slot_start}"
    # The public engine is now deterministic and backtest-driven. Keep the
    # legacy deep-sniper slot for UI compatibility, but never let its older
    # random/heuristic implementation overwrite the formal recommendation.
    top5 = analysis.get("candidateTiers", {}).get("top5", analysis.get("recommendation", []))[:5]
    deep = {
        "numbers": top5,
        "windowsUsed": analysis.get("windowsUsed", []),
        "windowPicks": [{"window": window, "numbers": top5} for window in analysis.get("windowsUsed", [])],
        "method": "候選池多模型集成（滾動回測加權；不使用隨機抽樣）",
    }
    return {
        **analysis,
        "deepSniperRecommendation": deep.get("numbers", []),
        "deepSniperSnapshot": {
            "key": key,
            "status": "published" if len(deep.get("numbers", [])) == 5 else "unavailable",
            "profile": "deep-8h",
            "source": "deterministic-shared-slot",
        },
        "deepSniperWindowHours": round(DEEP_ANALYSIS_WINDOW_SECONDS / 3600, 2),
        "deepSniperSlotStartedAt": datetime.fromtimestamp(slot_start, timezone.utc).isoformat(timespec="seconds"),
        "deepSniperNextAt": datetime.fromtimestamp(slot_end, timezone.utc).isoformat(timespec="seconds"),
        "deepSniperAnalysisLimit": min(len(history), max(DEEP_ANALYSIS_WINDOWS)),
        "deepSniperWindows": deep.get("windowsUsed", []),
        "deepSniperWindowPicks": deep.get("windowPicks", []),
        "deepSniperMethod": deep.get("method", "多視窗交叉分析"),
        "deepSniperStatus": "8 小時深度分析已完成；新一期開出時立即重算。",
    }


def build_payload(game: str, limit: int, optimize: bool = False) -> dict[str, Any]:
    if game == "tw539":
        latest = taiwan_latest()
        fetch_limit = max(limit, MODEL_ANALYSIS_DATA_WINDOW, MODEL_EVAL_WINDOW + MODEL_TRAIN_WINDOW)
        history = taiwan_history(fetch_limit)
        if history and not same_draw(history[0], latest):
            history = [latest] + [item for item in history if item.get("period") != latest.get("period") and not same_draw(item, latest)]
        draws = history[:limit]
        analysis_key = f"{cache_key_for_draws('analysis', game, fetch_limit, history)}-selected-{limit}"
        analysis = cached(analysis_key, lambda: analyze_with_stable_backtest(draws, history))
        status = data_health(game, latest, draws)
        analysis = {**analysis, "metadata": analysis_metadata(limit, status)}
        analysis = attach_deep_sniper_analysis(game, analysis, latest, history)
        analysis["predictionHistory"] = _mm_save_prediction(game, analysis, latest, history)
        payload = {"latest": public_draw(latest), "history": public_draws(draws), "analysis": analysis, "dataStatus": status}
        if optimize:
            payload["bestWindow"] = choose_best_analysis_window(history, game="tw539")
        return payload
    if game == "ca-fantasy5":
        fetch_limit = max(limit, MODEL_ANALYSIS_DATA_WINDOW, MODEL_EVAL_WINDOW + MODEL_TRAIN_WINDOW)
        history = california_history(fetch_limit)
        if not history:
            raise RuntimeError("加州天天樂資料頁目前沒有可解析的開獎資料")
        draws = history[:limit]
        analysis_key = f"{cache_key_for_draws('analysis', game, fetch_limit, history)}-selected-{limit}"
        analysis = cached(analysis_key, lambda: analyze_california_with_stable_backtest(draws, history))
        latest = history[0]
        status = data_health(game, latest, draws)
        analysis = {**analysis, "metadata": analysis_metadata(limit, status)}
        analysis = attach_deep_sniper_analysis(game, analysis, latest, history)
        analysis["predictionHistory"] = _mm_save_prediction(game, analysis, latest, history)
        payload = {"latest": public_draw(latest), "history": public_draws(draws), "analysis": analysis, "dataStatus": status}
        if optimize:
            payload["bestWindow"] = choose_best_analysis_window(history, game="ca-fantasy5")
        return payload
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
                        "enabled": bool(STRIPE_PAYMENT_LINK),
                        "paymentLink": STRIPE_PAYMENT_LINK,
                        "plans": [
                            {
                                "id": "pro",
                                "name": "Pro 訂閱",
                                "price": "$9 / 月起",
                                "features": ["120-365 期進階分析", "跨年歷史查詢", "模型回測與版路模式", "高分組合排序"],
                            },
                        ],
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
                }
            )
            return
        if parsed.path == "/api/latest":
            params = parse_qs(parsed.query)
            try:
                game = clean_game(params.get("game", ["tw539"])[0])
                latest = taiwan_latest() if game == "tw539" else california_latest()
                self.send_json(
                    {
                        "ok": True,
                        "game": game,
                        "latest": public_draw(latest),
                        "dataStatus": data_health(game, latest, [latest]),
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
                limit = clamp_int(params.get("limit", ["180"])[0], 180, 1, 365)
                optimize = params.get("optimize", ["0"])[0].strip().lower() in {"1", "true", "yes"}
                payload = build_payload(game, limit, optimize=optimize)
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
                if action == "subscribe":
                    count = upsert_push_subscription(subscription, payload.get("game", "all"))
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
    server = ThreadingHTTPServer((host, port), Handler)
    if AUTO_NOTIFY_ENABLED:
        threading.Thread(target=auto_notify_loop, name="lotto-auto-notify", daemon=True).start()
        print(f"auto notify enabled every {max(30, AUTO_NOTIFY_INTERVAL_SECONDS)}s for {', '.join(AUTO_NOTIFY_GAMES) or 'no games'}")
    print(f"摘星狙擊手 running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
