Warning: truncated output (original token count: 67574)
Total output lines: 5308

# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import hashlib
import hmac
import html
import io
import itertools
import json
import math
import os
import re
import socket
import ssl
import threading
import time
import traceback
import uuid
import posixpath
import queue
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from lottery_registry import catalog_rows, validate_main_numbers
from line_social import LineSocialStore
from marksix_official import latest as marksix_latest
from taiwan_official_history import recent as taiwan_official_history_recent
from line_notifications import LineNotificationStore
from urllib.parse import parse_qs, unquote, urlparse

try:
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover - optional production dependency
    WebPushException = None
    webpush = None

try:
    import analysis_v2
except Exception:  # pragma: no cover - server can still serve static pages if optional ML deps are absent
    analysis_v2 = None

try:
    import prediction_journal_v3
except Exception:  # pragma: no cover - journal is optional during static-only startup
    prediction_journal_v3 = None

try:
    import feature_importance
except Exception:  # pragma: no cover - explainability is additive and optional at startup
    feature_importance = None

try:
    import operations_v11
except Exception:  # pragma: no cover - operations reporting must never block Production startup
    operations_v11 = None

try:
    import tw539_evidence_trigger
except Exception:  # pragma: no cover - Evidence trigger must never block Current startup
    tw539_evidence_trigger = None

_ORIGINAL_GETADDRINFO = socket.getaddrinfo


def ipv4_getaddrinfo(*args, **kwargs):
    results = _ORIGINAL_GETADDRINFO(*args, **kwargs)
    ipv4_results = [info for info in results if info[0] == socket.AF_INET]
    return ipv4_results or results


socket.getaddrinfo = ipv4_getaddrinfo

ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"
PERSISTENT_DATA = Path(os.environ.get("LOTTO_PERSISTENT_DATA_DIR", ROOT / "data"))
BUNDLED_TAIWAN_HISTORY = PUBLIC / "taiwan_539_history.json"
BUNDLED_CA_FANTASY5_HISTORY = ROOT / "data" / "ca_fantasy5_database.json"
BUNDLED_CA_FANTASY5_HISTORY_V2 = ROOT / "data" / "ca_fantasy5_database_v2.json"

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
    "/api/analyze": (90, 60),
    "/api/analyze/status": (240, 60),
    "/api/history-search": (45, 60),
    "/api/prediction-journal": (30, 60),
    "/api/ai-vs-app": (30, 60),
    "/api/config": (120, 60),
    "/api/push-subscription": (20, 60),
    "/api/notify-latest": (5, 600),
    "/api/internal/tw539-evidence-cycle": (4, 3600),
    "/prediction": (60, 60),
}
ALLOWED_GAMES = {"tw539", "ca-fantasy5"}
STRIPE_PAYMENT_LINK = os.environ.get("LOTTO_STRIPE_PAYMENT_LINK", "").strip()
PUSH_PUBLIC_KEY = os.environ.get("LOTTO_VAPID_PUBLIC_KEY", "").strip()
PUSH_PRIVATE_KEY = os.environ.get("LOTTO_VAPID_PRIVATE_KEY", "").strip().replace("\\n", "\n")
PUSH_CONTACT_EMAIL = os.environ.get("LOTTO_PUSH_CONTACT_EMAIL", "admin@example.com").strip()
NOTIFY_SECRET = os.environ.get("LOTTO_NOTIFY_SECRET", "").strip()
SUBSCRIPTIONS_FILE = Path(os.environ.get("LOTTO_SUBSCRIPTIONS_FILE", PERSISTENT_DATA / "push_subscriptions.json"))
NOTIFY_STATE_FILE = Path(os.environ.get("LOTTO_NOTIFY_STATE_FILE", PERSISTENT_DATA / "notify_state.json"))
AUTO_NOTIFY_ENABLED = os.environ.get("LOTTO_AUTO_NOTIFY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
AUTO_NOTIFY_INTERVAL_SECONDS = int(os.environ.get("LOTTO_AUTO_NOTIFY_INTERVAL_SECONDS", "30"))
AUTO_NOTIFY_GAMES = [
    game.strip()
    for game in os.environ.get("LOTTO_AUTO_NOTIFY_GAMES", "tw539,ca-fantasy5").split(",")
    if game.strip() in ALLOWED_GAMES
]
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LINE_ADMIN_USER_IDS = {
    value.strip()
    for value in os.environ.get("LINE_ADMIN_USER_IDS", "").split(",")
    if value.strip()
}
LINE_WEBHOOK_MAX_EVENTS = 50
LINE_GAME_CATALOG = catalog_rows()
LINE_SOCIAL_ENABLED = os.environ.get("LINE_SOCIAL_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
LINE_SOCIAL_STORE = LineSocialStore(
    Path(os.environ.get("LINE_SOCIAL_FILE", PERSISTENT_DATA / "line_social.json")),
    enabled=LINE_SOCIAL_ENABLED,
)
LINE_NOTIFICATION_RUNTIME = os.environ.get("LINE_NOTIFICATION_RUNTIME", "").strip().lower()
LINE_NOTIFICATION_TESTER_IDS = {
    value.strip() for value in os.environ.get("LINE_NOTIFICATION_TESTER_USER_IDS", "").split(",") if value.strip()
}
LINE_NOTIFICATIONS_ENABLED = (
    os.environ.get("LINE_NOTIFICATIONS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    and LINE_NOTIFICATION_RUNTIME == "staging"
)
LINE_NOTIFICATION_STORE = LineNotificationStore(
    Path(os.environ.get("LINE_NOTIFICATION_FILE", PERSISTENT_DATA / "line_notifications_staging.sqlite3")),
    enabled=LINE_NOTIFICATIONS_ENABLED,
    tester_ids=LINE_NOTIFICATION_TESTER_IDS,
)


@dataclass
class CacheItem:
    value: Any
    created_at: float


cache: dict[str, CacheItem] = {}
rate_limit_hits: dict[tuple[str, str], list[float]] = {}
notify_lock = threading.Lock()
analysis_job_lock = threading.Lock()
analysis_jobs: dict[str, dict[str, Any]] = {}
analysis_job_keys: dict[str, str] = {}
ANALYSIS_JOB_RETRY_SECONDS = 2
ANALYSIS_JOB_RESULT_TTL_SECONDS = CACHE_TTL_SECONDS
WARM_CACHE_SCHEMA_VERSION = 2
WARM_CACHE_FILE = Path(os.environ.get("LOTTO_WARM_CACHE_FILE", PERSISTENT_DATA / "analysis_warm_cache.json"))
WARM_CACHE_LIMITS = tuple(
    dict.fromkeys(
        limit for value in os.environ.get("LOTTO_WARM_CACHE_LIMITS", "90,10").split(",") if (limit := int(value.strip() or 0)) > 0
    )
)
WARM_CACHE_POLL_SECONDS = max(30, int(os.environ.get("LOTTO_WARM_CACHE_POLL_SECONDS", "60")))
warm_cache_lock = threading.Lock()
warm_cache_jobs: set[str] = set()
analysis_work_queue: queue.Queue[tuple[Any, tuple[Any, ...]]] = queue.Queue()
analysis_worker_lock = threading.Lock()
analysis_worker_started = False
analysis_worker_context = threading.local()
ANALYSIS_EXECUTION_LOCK_FILE = Path(
    os.environ.get("LOTTO_ANALYSIS_LOCK_FILE", PERSISTENT_DATA / "analysis_execution.lock")
)


class AnalysisExecutionFileLock:
    """One non-blocking Production analysis lock shared by every process."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or ANALYSIS_EXECUTION_LOCK_FILE)
        self.stream = None
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":  # pragma: no cover - Render uses Linux; exercised on Windows CI
                import msvcrt

                self.stream.seek(0, os.SEEK_END)
                if self.stream.tell() == 0:
                    self.stream.write("0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
            return True
        except (BlockingIOError, OSError):
            self.stream.close()
            self.stream = None
            return False

    def release(self) -> None:
        if not self.stream:
            return
        try:
            if self.acquired:
                if os.name == "nt":  # pragma: no cover - Render uses Linux
                    import msvcrt

                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.acquired = False
            self.stream.close()
            self.stream = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc):
        self.release()


def _analysis_task_busy(task, args: tuple[Any, ...]) -> None:
    """Resolve a skipped cross-process task without blocking or loading models."""
    if task is _run_analysis_job:
        job_id = args[0]
        with analysis_job_lock:
            job = analysis_jobs.get(job_id)
            if job:
                job.update(
                    status="failed",
                    completed_at=time.time(),
                    error={
                        "message": "another Production process owns the analysis lock",
                        "type": "AnalysisBusy",
                        "retryable": True,
                    },
                )
    elif task is _run_warm_cache:
        game, signature = args[:2]
        with warm_cache_lock:
            warm_cache_jobs.discard(f"{game}:{signature}")
        print(f"warm cache skipped; analysis lock held by another process ({game})")


def _analysis_queue_worker() -> None:
    while True:
        task, args = analysis_work_queue.get()
        try:
            with AnalysisExecutionFileLock() as execution_lock:
                if not execution_lock.acquired:
                    _analysis_task_busy(task, args)
                    continue
                analysis_worker_context.active = True
                try:
                    task(*args)
                finally:
                    analysis_worker_context.active = False
        except Exception:
            traceback.print_exc()
        finally:
            analysis_work_queue.task_done()


def enqueue_analysis_work(task, *args) -> None:
    global analysis_worker_started
    with analysis_worker_lock:
        if not analysis_worker_started:
            threading.Thread(
                target=_analysis_queue_worker,
                name="lotto-analysis-queue",
                daemon=True,
            ).start()
            analysis_worker_started = True
    analysis_work_queue.put((task, args))


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
        "title": f"{lottery.get('name', '摘星引擎')} 已開獎",
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


def _warm_json_load() -> dict[str, Any]:
    try:
        value = json.loads(WARM_CACHE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _warm_json_save(value: dict[str, Any]) -> None:
    WARM_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = WARM_CACHE_FILE.with_suffix(f"{WARM_CACHE_FILE.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(WARM_CACHE_FILE)


def _repository_signature(game: str, history: list[dict[str, Any]]) -> str:
    latest = history[0] if history else {}
    return f"{game}:{len(history)}:{latest.get('date', '')}:{latest.get('period', '')}"


def _warm_cache_key(game: str, limit: int, optimize: bool = False) -> str:
    return f"{game}:{limit}:{int(optimize)}"


def get_warm_analysis(game: str, limit: int, optimize: bool = False) -> dict[str, Any] | None:
    with warm_cache_lock:
        entry = _warm_json_load().get("entries", {}).get(_warm_cache_key(game, limit, optimize))
    if (
        not isinstance(entry, dict)
        or entry.get("schemaVersion") != WARM_CACHE_SCHEMA_VERSION
        or not isinstance(entry.get("result"), dict)
    ):
        return None
    return entry


def build_warm_cache(game: str, limit: int, signature: str, loader=None) -> bool:
    """Build one cache entry and publish it only after the full payload succeeds."""
    loader = loader or build_payload
    key = _warm_cache_key(game, limit, False)
    payload = loader(game, limit, optimize=False)
    result = {
        "ok": True,
        "game": game,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }
    with warm_cache_lock:
        document = _warm_json_load()
        entries = document.setdefault("entries", {})
        entries[key] = {
            "schemaVersion": WARM_CACHE_SCHEMA_VERSION,
            "game": game,
            "limit": limit,
            "repositorySignature": signature,
            "completedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "result": result,
        }
        document["schemaVersion"] = WARM_CACHE_SCHEMA_VERSION
        _warm_json_save(document)
    return True


def store_warm_result(
    game: str,
    limit: int,
    signature: str,
    result: dict[str, Any],
    optimize: bool = False,
) -> None:
    key = _warm_cache_key(game, limit, optimize)
    with warm_cache_lock:
        document = _warm_json_load()
        document.setdefault("entries", {})[key] = {
            "schemaVersion": WARM_CACHE_SCHEMA_VERSION,
            "game": game,
            "limit": limit,
            "repositorySignature": signature,
            "completedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "result": result,
        }
        document["schemaVersion"] = WARM_CACHE_SCHEMA_VERSION
        _warm_json_save(document)


def _run_warm_cache(game: str, signature: str, limits: tuple[int, ...], loader=None) -> None:
    job_key = f"{game}:{signature}"
    try:
        missing = [
            limit
            for limit in limits
            if not ((entry := get_warm_analysis(game, limit)) and entry.get("repositorySignature") == signature)
        ]
        if missing:
            canonical_limit = max(missing)
            build_warm_cache(game, canonical_limit, signature, loader=loader)
            canonical = get_warm_analysis(game, canonical_limit)
            for limit in missing:
                if limit == canonical_limit:
                    continue
                result = json.loads(json.dumps(canonical["result"], ensure_ascii=False))
                result["history"] = result.get("history", [])[:limit]
                result.get("analysis", {}).get("metadata", {})["analysisLimit"] = limit
                store_warm_result(game, limit, signature, result)
        print(f"warm cache completed ({game}) {signature}")
    except Exception as exc:
        # Entries are replaced only after success, so the previous good cache remains.
        print(f"warm cache failed ({game}): {exc}")
    finally:
        with warm_cache_lock:
            warm_cache_jobs.discard(job_key)


def start_warm_cache(game: str, signature: str, limits: tuple[int, ...] | None = None, loader=None) -> bool:
    limits = limits or WARM_CACHE_LIMITS
    job_key = f"{game}:{signature}"
    with warm_cache_lock:
        if job_key in warm_cache_jobs:
            return False
        if all(
            (entry := _warm_json_load().get("entries", {}).get(_warm_cache_key(game, limit, False)))
            and entry.get("schemaVersion") == WARM_CACHE_SCHEMA_VERSION
            and entry.get("repositorySignature") == signature
            for limit in limits
        ):
            return False
        warm_cache_jobs.add(job_key)
    enqueue_analysis_work(_run_warm_cache, game, signature, limits, loader)
    return True


def warm_cache_monitor_loop() -> None:
    while True:
        for game in ("tw539", "ca-fantasy5"):
            try:
                history = taiwan_history(5000) if game == "tw539" else california_history(5000)
                if history:
                    start_warm_cache(game, _repository_signature(game, history))
            except Exception as exc:
                print(f"warm cache repository check failed ({game}): {exc}")
        time.sleep(WARM_CACHE_POLL_SECONDS)


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


TAIWAN_LINE_LATEST_GAMES = {
    "power-lottery": {"game_code": 5134, "name": "威力彩", "draw_size": 6, "bonus_label": "第二區"},
    "lotto-649": {"game_code": 5118, "name": "大樂透", "draw_size": 6, "bonus_label": "特別號"},
    "daily-3": {"game_code": 2108, "name": "三星彩", "draw_size": 3, "bonus_label": ""},
    "daily-4": {"game_code": 2109, "name": "四星彩", "draw_size": 4, "bonus_label": ""},
}


def taiwan_line_latest(game: str) -> dict[str, Any]:
    """Read a Taiwan Lottery latest draw for LINE after game-specific validation."""
    spec = TAIWAN_LINE_LATEST_GAMES[game]

    def load():
        payload = json.loads(fetch_text(cache_busted_url(TAIWAN_LAST_URL), timeout=10))
        entries = payload.get("content", {}).get("lastNumberList", [])
        item = next((entry for entry in entries if entry.get("gameCode") == spec["game_code"]), None)
        if not item:
            raise RuntimeError(f"台灣彩券 API 目前沒有回傳{spec['name']}最新資料")
        raw_numbers = [int(number) for number in item.get("lotNumber", [])]
        main_numbers = raw_numbers[: spec["draw_size"]]
        if not validate_main_numbers(game, main_numbers):
            raise RuntimeError(f"{spec['name']}最新資料未通過號碼規格驗證")
        return {
            "game": game,
            "name": spec["name"],
            "period": item.get("period", ""),
            "date": parse_date(item.get("drawDate", "")),
            "numbers": main_numbers,
            "bonus": raw_numbers[spec["draw_size"] :],
            "bonusLabel": spec["bonus_label"],
            "source": "台灣彩券 LastNumber API",
            "sourceUrl": TAIWAN_LAST_URL,
        }

    return cached(f"taiwan-line-latest-{game}", load, ttl_seconds=LATEST_CACHE_TTL_SECONDS)


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
 …47574 tokens truncated…ver let its older
    # random/heuristic implementation overwrite the formal recommendation.
    top5 = analysis.get("candidateTiers", {}).get("top5", analysis.get("recommendation", []))[:5]
    deep = {
        "numbers": top5,
        "windowsUsed": analysis.get("windowsUsed", []),
        "windowPicks": [{"window": window, "numbers": top5} for window in analysis.get("windowsUsed", [])],
        "method": "候選池多模型集成（滾動回測加權；不使用隨機抽樣）",
    }
    deep_status = "8 小時深度分析已完成；新一期開出時立即重算。" if len(deep.get("numbers", [])) == 5 else "資料不足，深度分析暫不產生推薦。"
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
        "deepSniperStatus": deep_status,
    }


def build_payload(game: str, limit: int, optimize: bool = False) -> dict[str, Any]:
    if not getattr(analysis_worker_context, "active", False):
        raise RuntimeError("full analysis must run inside the Production analysis queue worker")
    if analysis_v2 is None:
        raise RuntimeError("v2 分析引擎尚未載入；請確認 scikit-learn 與 joblib 已安裝")
    if game == "tw539":
        latest = taiwan_latest()
        fetch_limit = max(limit, MODEL_ANALYSIS_DATA_WINDOW, MODEL_EVAL_WINDOW + MODEL_TRAIN_WINDOW)
        history = taiwan_history(fetch_limit)
        if history and not same_draw(history[0], latest):
            history = [latest] + [item for item in history if item.get("period") != latest.get("period") and not same_draw(item, latest)]
        draws = history[:limit]
        analysis_key = f"{cache_key_for_draws('analysis', game, fetch_limit, history)}-selected-{limit}"
        analysis = cached(analysis_key, lambda: analysis_v2.analyze_tw539(history))
        status = data_health(game, latest, draws)
        analysis = {**analysis, "metadata": analysis_metadata(limit, status)}
        analysis = _attach_homepage_statistics(analysis, history)
        analysis = attach_deep_sniper_analysis(game, analysis, latest, history)
        if feature_importance is not None and not analysis.get("dataInsufficient"):
            analysis["featureImportance"] = feature_importance.capture_prediction(game, analysis, latest, history)
        analysis["predictionHistory"] = _mm_save_prediction(game, analysis, latest, history)
        if prediction_journal_v3 is not None:
            analysis["predictionJournal"] = prediction_journal_v3.record_live_prediction(game, analysis, latest, history)
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
        analysis = cached(analysis_key, lambda: analysis_v2.analyze_ca_fantasy5(history))
        latest = history[0]
        status = data_health(game, latest, draws)
        if analysis.get("dataInsufficient"):
            status = {
                **status,
                "validated": False,
                "message": "最新開獎可解析，但正式模型資料不足；暫不產生推薦。",
        }
        analysis = {**analysis, "metadata": analysis_metadata(limit, status)}
        analysis = _attach_homepage_statistics(analysis, history)
        analysis = attach_deep_sniper_analysis(game, analysis, latest, history)
        if feature_importance is not None and not analysis.get("dataInsufficient"):
            analysis["featureImportance"] = feature_importance.capture_prediction(game, analysis, latest, history)
        analysis["predictionHistory"] = _mm_save_prediction(game, analysis, latest, history)
        if prediction_journal_v3 is not None:
            analysis["predictionJournal"] = prediction_journal_v3.record_live_prediction(game, analysis, latest, history)
        payload = {"latest": public_draw(latest), "history": public_draws(draws), "analysis": analysis, "dataStatus": status}
        if optimize:
            payload["bestWindow"] = choose_best_analysis_window(history, game="ca-fantasy5")
        return payload
    raise ValueError("unknown game")


def _analysis_job_response(job: dict[str, Any], include_result: bool = True) -> dict[str, Any]:
    response = {
        "status": job["status"],
        "completed": job["status"] == "completed",
        "job_id": job["job_id"],
        "game": job["game"],
        "retry_after_seconds": ANALYSIS_JOB_RETRY_SECONDS,
        "cached": bool(job.get("cached", False)),
        "stale": False,
        "error": job.get("error"),
    }
    if include_result and job.get("result") is not None:
        response["result"] = job["result"]
    return response


def _run_analysis_job(job_id: str, loader, persist_warm: bool = True) -> None:
    try:
        with analysis_job_lock:
            job = analysis_jobs[job_id]
            game = job["game"]
            limit = job["limit"]
            optimize = job["optimize"]
        payload = loader(game, limit, optimize=optimize)
        result = {
            "ok": True,
            "game": game,
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
            **payload,
        }
        if persist_warm:
            latest = result.get("latest", {})
            draw_count = result.get("analysis", {}).get("drawCount", len(result.get("history", [])))
            signature = f"{game}:{draw_count}:{latest.get('date', '')}:{latest.get('period', '')}"
            store_warm_result(game, limit, signature, result, optimize=optimize)
        with analysis_job_lock:
            job = analysis_jobs[job_id]
            job.update(status="completed", result=result, completed_at=time.time(), error=None)
    except Exception as exc:
        error_traceback = traceback.format_exc()
        with analysis_job_lock:
            job = analysis_jobs[job_id]
            job.update(
                status="failed",
                completed_at=time.time(),
                error={"message": str(exc), "type": type(exc).__name__, "traceback": error_traceback},
            )


def start_analysis_job(game: str, limit: int, optimize: bool = False, loader=None) -> tuple[dict[str, Any], int]:
    persist_warm = loader is None
    loader = loader or build_payload
    if persist_warm:
        history = taiwan_history(5000) if game == "tw539" else california_history(5000)
        repository_signature = _repository_signature(game, history)
    else:
        repository_signature = f"test:{game}"
    request_key = f"{game}:{limit}:{int(optimize)}:{repository_signature}"
    now = time.time()
    if persist_warm:
        warm = get_warm_analysis(game, limit, optimize)
        if warm and warm.get("repositorySignature") == repository_signature:
            return {
                "status": "completed",
                "completed": True,
                "job_id": f"warm-{hashlib.sha256(request_key.encode()).hexdigest()[:24]}",
                "game": game,
                "retry_after_seconds": ANALYSIS_JOB_RETRY_SECONDS,
                "cached": True,
                "stale": False,
                "error": None,
                "result": warm["result"],
            }, 200
    with analysis_job_lock:
        existing_id = analysis_job_keys.get(request_key)
        existing = analysis_jobs.get(existing_id) if existing_id else None
        if existing and existing["status"] == "processing":
            return _analysis_job_response(existing), 202
        if existing and now - existing.get("completed_at", 0) < ANALYSIS_JOB_RESULT_TTL_SECONDS:
            existing["cached"] = existing["status"] == "completed"
            return _analysis_job_response(existing), 200

        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "request_key": request_key,
            "game": game,
            "limit": limit,
            "optimize": optimize,
            "repository_signature": repository_signature,
            "status": "processing",
            "created_at": now,
            "completed_at": 0.0,
            "cached": False,
            "error": None,
            "result": None,
        }
        analysis_jobs[job_id] = job
        analysis_job_keys[request_key] = job_id
    enqueue_analysis_work(_run_analysis_job, job_id, loader, persist_warm)
    return _analysis_job_response(job), 202


def analysis_get_response(job: dict[str, Any]) -> dict[str, Any]:
    """Keep legacy completed payload fields while exposing queue lifecycle metadata."""
    if job.get("status") == "completed" and isinstance(job.get("result"), dict):
        return {
            **job["result"],
            "status": "completed",
            "completed": True,
            "cached": bool(job.get("cached", False)),
            "stale": bool(job.get("stale", False)),
            "job_id": job.get("job_id"),
            "retry_after_seconds": job.get("retry_after_seconds", ANALYSIS_JOB_RETRY_SECONDS),
            "error": None,
        }
    return {**job, "completed": False}


def get_analysis_job(job_id: str) -> dict[str, Any] | None:
    with analysis_job_lock:
        job = analysis_jobs.get(job_id)
        return _analysis_job_response(job) if job else None


def public_draw(draw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in draw.items() if key not in {"source", "sourceUrl"}}


def public_draws(draws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [public_draw(draw) for draw in draws]


def line_signature_is_valid(body: bytes, signature: str) -> bool:
    """Validate LINE's HMAC-SHA256 signature without logging secrets or payloads."""
    if not LINE_CHANNEL_SECRET or not signature:
        return False
    expected = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256
    ).digest()
    try:
        supplied = __import__("base64").b64decode(signature, validate=True)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, supplied)


def line_reply(reply_token: str, text: str) -> None:
    """Reply once to a LINE message. A missing token is a configuration error, not a fallback."""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN 尚未設定")
    payload = json.dumps(
        {"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.line.me/v2/bot/message/reply",
        data=payload,
        headers={
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def line_message_reply(event: dict[str, Any]) -> str | None:
    """Return an allowlisted, non-predictive reply for a LINE text-message event."""
    if event.get("type") != "message" or event.get("message", {}).get("type") != "text":
        return None
    text = str(event["message"].get("text", "")).strip().lower()
    user_id = str(event.get("source", {}).get("userId", "")).strip()
    event_id = str(event.get("webhookEventId", "")).strip()
    if text in {"通知開啟", "開啟通知"}:
        return LINE_NOTIFICATION_STORE.subscribe(user_id)
    if text in {"通知關閉", "關閉通知"}:
        return LINE_NOTIFICATION_STORE.unsubscribe(user_id)
    if text in {"通知設定", "通知狀態"}:
        return LINE_NOTIFICATION_STORE.status(user_id)
    parts = text.split()
    if parts and parts[0] == "分享":
        try:
            return LINE_SOCIAL_STORE.share(user_id, parts[1], [int(number) for number in parts[2:]], event_id)
        except (IndexError, ValueError):
            return "格式：分享 彩種 號碼，例如：分享 539 01 02 03 04 05"
    if parts and parts[0] == "熱門":
        return LINE_SOCIAL_STORE.hot_numbers(parts[1] if len(parts) > 1 else "")
    if parts and parts[0] in {"排行", "排名"}:
        return LINE_SOCIAL_STORE.active_ranking(parts[1] if len(parts) > 1 else "")
    if text == "討論區":
        return LINE_SOCIAL_STORE.recent_posts()
    if text.startswith("討論"):
        return LINE_SOCIAL_STORE.post(user_id, text.removeprefix("討論"), event_id)
    if text in {"彩種", "彩券", "遊戲"}:
        lines = ["彩券系統建置中："]
        lines.extend(f"• {name}：{status}" for name, _code, status in LINE_GAME_CATALOG)
        return "\n".join(lines)
    if text in {"我的id", "我的 id", "myid"}:
        return f"你的 LINE 管理識別碼：{user_id or '尚未取得'}\n請只在管理員設定時使用，不要公開貼出。"
    if text in {"管理", "admin"}:
        if user_id and user_id in LINE_ADMIN_USER_IDS:
            return "管理員功能正在建立中。"
        return "此指令僅限管理員。"
    if text in {"最新", "最新開獎", "539", "tw539"}:
        try:
            latest = taiwan_latest()
            numbers = "、".join(f"{int(number):02d}" for number in latest.get("numbers", []))
            period = latest.get("period", "—")
            date = latest.get("date", "—")
            return f"今彩539 最新開獎\n期別：{period}\n日期：{date}\n號碼：{numbers or '資料驗證中'}"
        except Exception:
            return "開獎資料驗證中，請稍後再試。"
    if text in {"六合彩", "mark six", "marksix"}:
        try:
            latest = marksix_latest()
            numbers = "、".join(f"{int(number):02d}" for number in latest["numbers"])
            bonus = "、".join(f"{int(number):02d}" for number in latest["bonus"])
            return f"六合彩 最新開獎\n期別：{latest['period']}\n日期：{latest['date']}\n號碼：{numbers}\n特別號：{bonus}"
        except Exception:
            return "六合彩開獎資料驗證中，請稍後再試。"
    history_game_commands = {
        "威力彩": "power-lottery",
        "大樂透": "lotto-649",
        "三星彩": "daily-3",
        "四星彩": "daily-4",
    }
    if parts and parts[0] in {"歷史", "紀錄"}:
        game = history_game_commands.get(parts[1] if len(parts) > 1 else "")
        if not game:
            return "格式：歷史 彩種，例如：歷史 威力彩"
        try:
            rows = taiwan_official_history_recent(game, limit=5)
            lines = [f"{TAIWAN_LINE_LATEST_GAMES[game]['name']} 官方近 5 期紀錄"]
            for row in rows:
                numbers = "、".join(f"{int(number):02d}" for number in row["numbers"])
                if row["bonus"]:
                    numbers += " + " + "、".join(f"{int(number):02d}" for number in row["bonus"])
                lines.append(f"{row['date']}｜{numbers}")
            return "\n".join(lines)
        except Exception:
            return "官方歷史資料驗證中，請稍後再試。"
    line_game_commands = {
        "威力彩": "power-lottery",
        "大樂透": "lotto-649",
        "三星彩": "daily-3",
        "四星彩": "daily-4",
    }
    if text in line_game_commands:
        try:
            latest = taiwan_line_latest(line_game_commands[text])
            numbers = "、".join(f"{int(number):02d}" for number in latest["numbers"])
            reply = f"{latest['name']} 最新開獎\n期別：{latest['period'] or '—'}\n日期：{latest['date'] or '—'}\n號碼：{numbers}"
            if latest["bonus"] and latest["bonusLabel"]:
                bonus = "、".join(f"{int(number):02d}" for number in latest["bonus"])
                reply += f"\n{latest['bonusLabel']}：{bonus}"
            return reply
        except Exception:
            return "開獎資料驗證中，請稍後再試。"
    if text in {"系統", "系統狀態", "status"}:
        return "摘星引擎目前已連線。\n開獎資料會先完成驗證，再提供可用資訊。"
    if text in {"幫助", "help", "開始", "start"}:
        return "摘星引擎已連線。\n「彩種」查看支援進度\n「最新」查今彩539開獎\n直接傳「六合彩／威力彩／大樂透／三星彩／四星彩」查最新開獎\n「歷史 威力彩」查官方近 5 期\n「系統」查看連線狀態\n「我的ID」取得管理識別碼"
    return "輸入「幫助」查看可用指令。"


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
        rate_path = path
        if path.startswith("/api/analyze/status/"):
            rate_path = "/api/analyze/status"
        elif path.startswith("/api/analyze/"):
            rate_path = "/api/analyze"
        limit = API_RATE_LIMITS.get(rate_path)
        if not limit:
            return False, 0
        max_hits, window_seconds = limit
        now = time.time()
        key = (self.client_key(), rate_path)
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
        if parsed.path.startswith("/prediction/") and self.reject_if_rate_limited("/prediction"):
            return
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "service": "lotto-lab", "time": datetime.now().isoformat(timespec="seconds")})
            return
        if parsed.path.startswith("/api/analyze/status/"):
            job_id = unquote(parsed.path.rsplit("/", 1)[-1]).strip()
            result = get_analysis_job(job_id)
            if result is None:
                self.send_json({"ok": False, "error": "analysis job not found"}, status=404)
            else:
                self.send_json(result)
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
        if parsed.path == "/api/analyze/tw539" or parsed.path == "/api/analyze/ca-fantasy5":
            params = parse_qs(parsed.query)
            route_game = "tw539" if parsed.path.endswith("tw539") else "ca-fantasy5"
            try:
                requested = clean_game(params.get("game", [route_game])[0])
                if requested != route_game:
                    raise ValueError("分析 API 彩種與路徑不一致")
                limit = clamp_int(params.get("limit", ["365"])[0], 365, 1, 365)
                job, status = start_analysis_job(route_game, limit, optimize=False)
                headers = {"Retry-After": str(job["retry_after_seconds"])} if status == 202 else None
                self.send_json(analysis_get_response(job), status=status, extra_headers=headers)
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
                job, status = start_analysis_job(game, limit, optimize=optimize)
                headers = {"Retry-After": str(job["retry_after_seconds"])} if status == 202 else None
                self.send_json(analysis_get_response(job), status=status, extra_headers=headers)
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
        if parsed.path == "/api/prediction-journal":
            params = parse_qs(parsed.query)
            try:
                if prediction_journal_v3 is None:
                    raise RuntimeError("Prediction Journal 模組尚未載入")
                game = clean_game(params.get("game", ["tw539"])[0])
                limit = clamp_int(params.get("limit", ["100"])[0], 100, 1, 500)
                history = taiwan_history(5000) if game == "tw539" else california_history(5000)
                result = prediction_journal_v3.get_journal(game, history, limit=limit)
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **result})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/ai-vs-app":
            params = parse_qs(parsed.query)
            try:
                if prediction_journal_v3 is None:
                    raise RuntimeError("AI vs App Battle 模組尚未載入")
                limit = clamp_int(params.get("limit", ["100"])[0], 100, 1, 500)
                history = california_history(5000)
                result = prediction_journal_v3.get_battle(history, limit=limit)
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **result})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path.startswith("/prediction/") and parsed.path.endswith("/feature_importance"):
            try:
                if feature_importance is None:
                    raise RuntimeError("Feature Importance 模組尚未載入")
                prefix = "/prediction/"
                suffix = "/feature_importance"
                draw_id = unquote(parsed.path[len(prefix):-len(suffix)]).strip("/")
                params = parse_qs(parsed.query)
                game = params.get("game", [None])[0]
                result = feature_importance.get_prediction(draw_id, game=clean_game(game) if game else None)
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **result})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        # LINE does not send browser Origin headers; signature verification is the
        # authority check for this single public endpoint.
        if parsed.path == "/api/line/webhook":
            if not LINE_CHANNEL_SECRET:
                self.send_json({"ok": False, "error": "LINE Webhook 尚未設定"}, status=503)
                return
            try:
                body = self.read_raw_body()
                if not line_signature_is_valid(body, self.headers.get("X-Line-Signature", "")):
                    self.send_json({"ok": False, "error": "LINE 簽章驗證失敗"}, status=401)
                    return
                payload = json.loads(body.decode("utf-8")) if body else {}
                events = payload.get("events", []) if isinstance(payload, dict) else []
                if not isinstance(events, list) or len(events) > LINE_WEBHOOK_MAX_EVENTS:
                    raise ValueError("LINE 事件格式不正確")
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("webhookEventId", "")).strip()
                    if not LINE_NOTIFICATION_STORE.record_webhook_event(event_id):
                        continue
                    source = event.get("source", {}) if isinstance(event.get("source"), dict) else {}
                    event_user_id = str(source.get("userId", "")).strip()
                    if event.get("type") == "follow":
                        LINE_NOTIFICATION_STORE.follow(event_user_id)
                    elif event.get("type") == "unfollow":
                        LINE_NOTIFICATION_STORE.unfollow(event_user_id)
                    reply_token = str(event.get("replyToken", "")).strip()
                    reply_text = line_message_reply(event)
                    if reply_token and reply_text and LINE_CHANNEL_ACCESS_TOKEN:
                        try:
                            line_reply(reply_token, reply_text)
                        except Exception as exc:
                            # LINE retries only the webhook delivery; do not turn a
                            # reply failure into a replayed command.
                            print(f"LINE reply failed: {type(exc).__name__}")
                self.send_json({"ok": True})
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                self.send_json({"ok": False, "error": "LINE 事件格式不正確"}, status=400)
            return
        if parsed.path.startswith("/api/") and self.reject_if_rate_limited(parsed.path):
            return
        if not self.verify_origin():
            self.send_json({"ok": False, "error": "不允許的請求來源"}, status=403)
            return
        if parsed.path == "/api/internal/tw539-evidence-cycle":
            if tw539_evidence_trigger is None:
                self.send_json({"status": "PERMANENT_FAILURE", "error_category": "runtime_unavailable"}, status=503)
                return
            try:
                payload = self.read_json_body()
                supplied = self.headers.get(tw539_evidence_trigger.TRIGGER_HEADER, "")
                status, response = tw539_evidence_trigger.invoke_current_evidence_cycle(
                    supplied_secret=supplied,
                    payload=payload,
                )
                self.send_json(response, status=status)
            except Exception:
                self.send_json({"status": "PERMANENT_FAILURE", "error_category": "request_error"}, status=500)
            return
        if parsed.path in {"/api/analyze/tw539", "/api/analyze/ca-fantasy5"}:
            try:
                game = parsed.path.rsplit("/", 1)[-1]
                payload = self.read_json_body()
                limit = clamp_int(payload.get("limit", 180), 180, 1, 365)
                optimize = str(payload.get("optimize", "0")).strip().lower() in {"1", "true", "yes"}
                result, status = start_analysis_job(game, limit, optimize=optimize)
                headers = {"Retry-After": str(result["retry_after_seconds"])} if status == 202 else None
                self.send_json(result, status=status, extra_headers=headers)
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
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
        if parsed.path == "/api/ai-vs-app":
            try:
                if prediction_journal_v3 is None:
                    raise RuntimeError("AI vs App Battle 模組尚未載入")
                payload = self.read_json_body()
                snapshot = payload.get("snapshot", payload)
                result = prediction_journal_v3.submit_app_snapshot(snapshot)
                self.send_json({"ok": True, **result})
                return
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
                return
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=500)
                return
        self.send_json({"ok": False, "error": "not found"}, status=404)

    def read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError("資料量過大")
        return self.rfile.read(length)

    def read_json_body(self) -> dict[str, Any]:
        body = self.read_raw_body()
        if not body:
            return {}
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
        if parsed.path in ("/", "/index.html", "/app.js", "/styles.css", "/sw.js", "/manifest.webmanifest"):
            self.send_header("Cache-Control", "no-cache")
        elif parsed.path.startswith("/icon"):
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
    threading.Thread(target=warm_cache_monitor_loop, name="lotto-warm-cache-monitor", daemon=True).start()
    print(f"warm cache monitor enabled every {WARM_CACHE_POLL_SECONDS}s for limits {WARM_CACHE_LIMITS}")
    if operations_v11 is not None and os.environ.get("LOTTO_OPERATIONS_V11_ENABLED", "1").lower() not in {"0", "false", "off"}:
        threading.Thread(
            target=operations_v11.daemon_loop,
            kwargs={
                "interval": int(os.environ.get("LOTTO_OPERATIONS_V11_INTERVAL", "86400")),
                "initial_delay": int(os.environ.get("LOTTO_OPERATIONS_V11_INITIAL_DELAY", "60")),
            },
            name="lotto-operations-v11",
            daemon=True,
        ).start()
        print("operations v1.1 daily audit enabled")
    if AUTO_NOTIFY_ENABLED:
        threading.Thread(target=auto_notify_loop, name="lotto-auto-notify", daemon=True).start()
        print(f"auto notify enabled every {max(30, AUTO_NOTIFY_INTERVAL_SECONDS)}s for {', '.join(AUTO_NOTIFY_GAMES) or 'no games'}")
    print(f"摘星引擎 running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
