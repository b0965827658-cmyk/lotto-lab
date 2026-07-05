from __future__ import annotations

import csv
import io
import json
import os
import random
import re
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).parent
PUBLIC = ROOT / "public"

TAIWAN_LAST_URL = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/LastNumber"
TAIWAN_DATASET_URL = "https://gaze.nta.gov.tw/dntmb/OpenData/csvDw?ntaCode=D423F"
CALIFORNIA_FANTASY5_URL = "https://sc888.net/index.php?s=%2FLotteryFan%2Findex"

USER_AGENT = "Mozilla/5.0 LottoLab/0.1"
CACHE_TTL_SECONDS = 15 * 60
STRIPE_PAYMENT_LINK = os.environ.get("LOTTO_STRIPE_PAYMENT_LINK", "").strip()


@dataclass
class CacheItem:
    value: Any
    created_at: float


cache: dict[str, CacheItem] = {}


def cached(key: str, loader):
    hit = cache.get(key)
    if hit and time.time() - hit.created_at < CACHE_TTL_SECONDS:
        return hit.value
    value = loader()
    cache[key] = CacheItem(value=value, created_at=time.time())
    return value


def fetch_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def fetch_bytes(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def normalize_numbers(nums: list[int]) -> list[int]:
    return sorted(int(n) for n in nums)


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


def taiwan_latest() -> dict[str, Any]:
    payload = json.loads(fetch_text(TAIWAN_LAST_URL))
    entries = payload.get("content", {}).get("lastNumberList", [])
    daily_cash = next((item for item in entries if item.get("gameCode") == 5120), None)
    if not daily_cash:
        raise RuntimeError("台灣彩券 API 目前沒有回傳今彩 539 最新資料")
    return {
        "game": "tw539",
        "name": "今彩 539",
        "period": daily_cash.get("period", ""),
        "date": parse_date(daily_cash.get("drawDate", "")),
        "numbers": normalize_numbers(daily_cash.get("lotNumber", [])),
        "source": "台灣彩券 LastNumber API",
        "sourceUrl": TAIWAN_LAST_URL,
    }


def taiwan_dataset_rows() -> list[dict[str, str]]:
    def load():
        dataset = fetch_text(TAIWAN_DATASET_URL)
        return list(csv.DictReader(io.StringIO(dataset)))

    return cached("taiwan-dataset-rows", load)


def parse_taiwan_zip(zip_url: str) -> list[dict[str, Any]]:
    data = fetch_bytes(zip_url)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = next(name for name in zf.namelist() if "今彩539" in name)
        text = zf.read(name).decode("utf-8-sig")
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


def taiwan_history(limit: int = 180) -> list[dict[str, Any]]:
    rows = taiwan_dataset_rows()
    latest_row = max(rows, key=lambda row: int(row.get("資料所屬年度", "0") or "0"))
    latest_year = int(latest_row.get("資料所屬年度", "0") or "0") + 1911
    return taiwan_year_history(latest_year)[:limit]


def search_taiwan_history(from_year: int, to_year: int, keyword: str = "", number: int | None = None, limit: int = 2000) -> dict[str, Any]:
    rows = taiwan_dataset_rows()
    available_years = sorted(int(row.get("資料所屬年度", "0") or "0") + 1911 for row in rows)
    if not available_years:
        return {"history": [], "availableYears": [], "searchedYears": []}
    start = max(min(from_year, to_year), available_years[0])
    end = min(max(from_year, to_year), available_years[-1])
    searched_years = list(range(start, end + 1))
    draws = []
    for year in searched_years:
        draws.extend(taiwan_year_history(year))
    latest = taiwan_latest()
    latest_year = int(latest["date"][:4]) if latest.get("date") else None
    if latest_year in searched_years and all(draw.get("period") != latest.get("period") for draw in draws):
        draws.append(latest)
    query = keyword.strip().lower()
    if query or number:
        draws = [
            draw
            for draw in draws
            if (not query or query in f"{draw['date']} {draw['period']} {' '.join(map(lambda n: str(n).zfill(2), draw['numbers']))}".lower())
            and (not number or number in draw["numbers"])
        ]
    draws.sort(key=lambda item: (item["date"], item["period"]), reverse=True)
    return {
        "history": draws[:limit],
        "total": len(draws),
        "availableYears": available_years,
        "searchedYears": searched_years,
        "limited": len(draws) > limit,
    }


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
        return values

    return cached("california-history", load)[:limit]


def analyze(draws: list[dict[str, Any]], max_number: int = 39, pick_count: int = 5) -> dict[str, Any]:
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
    hot = sorted(frequency, key=lambda n: (-frequency[n], n))[:10]
    cold = sorted(frequency, key=lambda n: (frequency[n], n))[:10]
    overdue = sorted(gaps, key=lambda n: (-gaps[n], n))[:10]

    scored = []
    max_freq = max(frequency.values()) or 1
    max_gap = max(gaps.values()) or 1
    for n in frequency:
        score = (frequency[n] / max_freq) * 0.58 + (gaps[n] / max_gap) * 0.42
        scored.append((score, n))
    scored.sort(reverse=True)
    pool = [n for _, n in scored[:16]]
    random.seed(datetime.now(timezone.utc).strftime("%Y-%m-%d") + ",".join(map(str, pool)))
    recommendation = sorted(random.sample(pool, pick_count))

    return {
        "drawCount": len(draws),
        "hot": [{"number": n, "count": frequency[n]} for n in hot],
        "cold": [{"number": n, "count": frequency[n]} for n in cold],
        "overdue": [{"number": n, "gap": gaps[n]} for n in overdue],
        "frequency": [{"number": n, "count": frequency[n], "gap": gaps[n]} for n in frequency],
        "recommendation": recommendation,
        "note": "這是用頻率與遺漏值做的統計參考，不代表可預測或保證中獎。",
    }


def build_payload(game: str, limit: int) -> dict[str, Any]:
    if game == "tw539":
        latest = taiwan_latest()
        history = taiwan_history(limit)
        if history and history[0]["period"] != latest["period"]:
            history = [latest] + [item for item in history if item["period"] != latest["period"]]
        return {"latest": latest, "history": history[:limit], "analysis": analyze(history[:limit])}
    if game == "ca-fantasy5":
        history = california_history(limit)
        if not history:
            raise RuntimeError("加州天天樂資料頁目前沒有可解析的開獎資料")
        return {"latest": history[0], "history": history[:limit], "analysis": analyze(history[:limit])}
    raise ValueError("unknown game")


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = urlparse(path).path
        if clean.startswith("/api/"):
            return str(PUBLIC / "index.html")
        if clean == "/":
            return str(PUBLIC / "index.html")
        return str(PUBLIC / clean.lstrip("/"))

    def do_GET(self):
        parsed = urlparse(self.path)
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
                                "id": "free",
                                "name": "免費版",
                                "price": "$0",
                                "features": ["最新開獎號碼", "基本歷史紀錄", "統計免責提示"],
                            },
                            {
                                "id": "pro",
                                "name": "Pro 訂閱",
                                "price": "$9 / 月起",
                                "features": ["完整歷史分析", "熱號冷號與遺漏值", "每日統計參考選號", "後續可加開獎通知"],
                            },
                        ],
                    },
                }
            )
            return
        if parsed.path == "/api/lottery":
            params = parse_qs(parsed.query)
            game = params.get("game", ["tw539"])[0]
            limit = max(20, min(365, int(params.get("limit", ["180"])[0])))
            try:
                payload = build_payload(game, limit)
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **payload})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        if parsed.path == "/api/history-search":
            params = parse_qs(parsed.query)
            game = params.get("game", ["tw539"])[0]
            if game != "tw539":
                self.send_json(
                    {
                        "ok": False,
                        "error": "目前跨年查詢先支援今彩 539；加州天天樂需要更穩定的跨年資料源。",
                    },
                    status=400,
                )
                return
            current_year = datetime.now().year
            from_year = int(params.get("fromYear", [str(current_year - 2)])[0])
            to_year = int(params.get("toYear", [str(current_year)])[0])
            keyword = params.get("keyword", [""])[0]
            number_value = params.get("number", [""])[0]
            number = int(number_value) if number_value else None
            limit = max(50, min(5000, int(params.get("limit", ["2000"])[0])))
            try:
                payload = search_taiwan_history(from_year, to_year, keyword=keyword, number=number, limit=limit)
                self.send_json({"ok": True, "updatedAt": datetime.now().isoformat(timespec="seconds"), **payload})
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=502)
            return
        return super().do_GET()

    def send_json(self, payload: dict[str, Any], status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get("PORT", "8787"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Lotto Lab running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
