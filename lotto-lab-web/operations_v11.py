"""Lotto Lab V1.1 production operations toolkit.

This module is deliberately independent from prediction and analysis code.  It
only observes existing runtime files, creates backups, and writes operational
reports below ``$LOTTO_PERSISTENT_DATA_DIR/operations``.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import html
import json
import os
import shutil
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TAIPEI = timezone(timedelta(hours=8))
DATA_DIR = Path(os.environ.get("LOTTO_PERSISTENT_DATA_DIR", Path(__file__).parent / "data"))
OPS_DIR = DATA_DIR / "operations"
HEALTH_URL = os.environ.get("LOTTO_HEALTH_URL", "http://127.0.0.1:%s/api/health" % os.environ.get("PORT", "8787"))
RETENTION_DAYS = max(1, int(os.environ.get("LOTTO_BACKUP_RETENTION_DAYS", "30")))
WARM_CACHE = DATA_DIR / "analysis_warm_cache.json"
JOURNALS = {
    "tw539": DATA_DIR / "prediction_journal_v3_tw539.json",
    "ca-fantasy5": DATA_DIR / "prediction_journal_v3_ca_fantasy5.json",
}
BACKUP_PATTERNS = (
    "analysis_warm_cache.json",
    "prediction_journal*.json",
    "*prediction_history*.json",
    "*database*.json",
    "*model_store*.json",
)
LOCK_FILE_NAME = "scheduler.lock"


def render_mode() -> bool:
    return os.environ.get("RENDER", "").strip().lower() == "true"


def validate_operations_path(data_dir: Path | None = None, require_render_disk: bool | None = None) -> Path:
    """Return the operations directory, rejecting an ephemeral Render path."""
    selected = (data_dir or DATA_DIR).expanduser()
    require_disk = render_mode() if require_render_disk is None else require_render_disk
    if not selected.is_absolute():
        if require_disk:
            raise RuntimeError("LOTTO_PERSISTENT_DATA_DIR must be an absolute Render disk path")
        selected = selected.resolve()
    selected = selected.resolve()
    if require_disk:
        configured = os.environ.get("LOTTO_PERSISTENT_DATA_DIR")
        if not configured:
            raise RuntimeError("LOTTO_PERSISTENT_DATA_DIR is required on Render")
        mount = Path(os.environ.get("LOTTO_RENDER_DISK_MOUNT_PATH", "/api/health")).resolve()
        if selected != mount:
            raise RuntimeError(f"operations path is not on the Render disk: {selected} != {mount}")
    return selected / "operations"


class SchedulerLock:
    """Cross-process, non-blocking lifetime lock for the daily scheduler."""

    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                if self.stream.read(1) == b"":
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.stream.seek(0)
                self.stream.truncate()
                self.stream.write(str(os.getpid()).encode("ascii"))
                self.stream.flush()
            return True
        except (BlockingIOError, OSError):
            self.stream.close()
            self.stream = None
            return False

    def release(self) -> None:
        if self.stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


def acquire_scheduler_lock() -> SchedulerLock | None:
    operations = validate_operations_path()
    lock = SchedulerLock(operations / LOCK_FILE_NAME)
    if not lock.acquire():
        return None
    atexit.register(lock.release)
    return lock


def now() -> datetime:
    return datetime.now(TAIPEI)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def file_evidence(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "exists": True,
        "bytes": stat.st_size,
        "sha256": sha256(path),
        "modifiedAt": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def api_health() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10) as response:
            payload = json.loads(response.read())
            return {
                "ok": response.status == 200 and bool(payload.get("ok")),
                "http": response.status,
                "responseMs": round((time.perf_counter() - started) * 1000, 3),
                "error": None,
            }
    except Exception as exc:
        return {
            "ok": False,
            "http": None,
            "responseMs": round((time.perf_counter() - started) * 1000, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def warm_cache_status() -> dict[str, Any]:
    document = read_json(WARM_CACHE, {})
    entries = document.get("entries", {}) if isinstance(document, dict) else {}
    games = {}
    for game in ("tw539", "ca-fantasy5"):
        matching = [entry for key, entry in entries.items() if key.startswith(f"{game}:") and isinstance(entry, dict)]
        matching.sort(key=lambda entry: entry.get("completedAt", ""), reverse=True)
        latest = matching[0] if matching else {}
        games[game] = {
            "completed": bool(latest.get("result")),
            "entryCount": len(matching),
            "repositorySignature": latest.get("repositorySignature"),
            "lastAnalysisAt": latest.get("completedAt"),
        }
    return {"file": file_evidence(WARM_CACHE), "games": games}


def journal_status() -> dict[str, Any]:
    result = {}
    for game, path in JOURNALS.items():
        document = read_json(path, {})
        records = document.get("records", []) if isinstance(document, dict) else []
        identities = [(item.get("drawId"), item.get("predictionHash")) for item in records if isinstance(item, dict)]
        result[game] = {
            "file": file_evidence(path),
            "records": len(records),
            "duplicates": len(identities) - len(set(identities)),
            "ok": path.is_file() and len(identities) == len(set(identities)),
        }
    return result


def process_status() -> dict[str, Any]:
    memory = None
    try:
        fields = (Path("/proc/self/status").read_text(encoding="utf-8"))
        memory = next((line.split(":", 1)[1].strip() for line in fields.splitlines() if line.startswith("VmRSS:")), None)
    except OSError:
        pass
    load = None
    try:
        load = round(os.getloadavg()[0], 4)
    except (AttributeError, OSError):
        pass
    return {
        "memoryRss": memory,
        "cpuLoad1m": load,
        "cpuCount": os.cpu_count(),
        "queue": {"mode": "single-worker", "depth": "not exposed", "state": "observable via cache completion"},
        "instanceId": os.environ.get("RENDER_INSTANCE_ID"),
        "service": os.environ.get("RENDER_SERVICE_NAME"),
        "commit": os.environ.get("RENDER_GIT_COMMIT"),
    }


def collect_health() -> dict[str, Any]:
    validate_operations_path()
    timestamp = now()
    snapshot = {
        "schemaVersion": 1,
        "checkedAt": timestamp.isoformat(),
        "api": api_health(),
        "warmCache": warm_cache_status(),
        "predictionJournal": journal_status(),
        "runtime": process_status(),
    }
    problems = []
    if not snapshot["api"]["ok"]:
        problems.append("api_unhealthy")
    for game in ("tw539", "ca-fantasy5"):
        if not snapshot["warmCache"]["games"][game]["completed"]:
            problems.append(f"warm_cache_missing:{game}")
        if not snapshot["predictionJournal"][game]["ok"]:
            problems.append(f"journal_invalid:{game}")
        if snapshot["predictionJournal"][game]["duplicates"]:
            problems.append(f"journal_duplicate:{game}")
    snapshot["status"] = "normal" if not problems else "abnormal"
    snapshot["problems"] = problems
    checks = OPS_DIR / "health_checks"
    atomic_json(checks / "latest.json", snapshot)
    with (checks / "history.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    render_dashboard(snapshot)
    return snapshot


def backup() -> dict[str, Any]:
    validate_operations_path()
    timestamp = now()
    destination = OPS_DIR / "backups" / timestamp.strftime("%Y-%m-%d")
    destination.mkdir(parents=True, exist_ok=True)
    groups = (
        ("analysis_warm_cache.json",),
        ("prediction_journal*.json", "*prediction_history*.json"),
        ("*database*.json", "*model_store*.json"),
    )
    sources: dict[str, Path] = {}
    for patterns in groups:
        for pattern in patterns:
            for source in DATA_DIR.glob(pattern):
                if source.is_file() and not source.is_symlink():
                    sources[source.name] = source
    manifest = {"schemaVersion": 1, "createdAt": timestamp.isoformat(), "retentionDays": RETENTION_DAYS, "files": []}
    for source in sorted(sources.values()):
        target = destination / source.name
        if target.is_symlink():
            raise RuntimeError(f"refusing symlink backup destination: {target}")
        if not target.exists():
            shutil.copy2(source, target)
        manifest["files"].append(file_evidence(target))
    atomic_json(destination / "manifest.json", manifest)
    cutoff = timestamp.date() - timedelta(days=RETENTION_DAYS - 1)
    for directory in (OPS_DIR / "backups").iterdir():
        try:
            backup_date = datetime.strptime(directory.name, "%Y-%m-%d").date()
        except (ValueError, OSError):
            continue
        if directory.is_symlink():
            continue
        if directory.is_dir() and backup_date < cutoff:
            shutil.rmtree(directory)
    return manifest


def operations_report() -> dict[str, Any]:
    validate_operations_path()
    history = OPS_DIR / "health_checks" / "history.jsonl"
    samples = []
    if history.is_file():
        for line in history.read_text(encoding="utf-8").splitlines():
            try:
                sample = json.loads(line)
            except ValueError:
                continue
            if sample.get("checkedAt", "")[:10] == now().date().isoformat():
                samples.append(sample)
    response_times = [sample["api"]["responseMs"] for sample in samples if sample.get("api", {}).get("responseMs") is not None]
    instance_ids = [sample.get("runtime", {}).get("instanceId") for sample in samples]
    instance_ids = [value for value in instance_ids if value]
    hits = sum(
        all(sample.get("warmCache", {}).get("games", {}).get(game, {}).get("completed") for game in ("tw539", "ca-fantasy5"))
        for sample in samples
    )
    successes = sum(bool(sample.get("api", {}).get("ok")) for sample in samples)
    report = {
        "schemaVersion": 1,
        "date": now().date().isoformat(),
        "observedApiRequests": len(samples),
        "averageResponseMs": round(sum(response_times) / len(response_times), 3) if response_times else None,
        "cacheHitRate": round(hits / len(samples), 4) if samples else None,
        "cacheMissRate": round(1 - hits / len(samples), 4) if samples else None,
        "renderRestarts": sum(a != b for a, b in zip(instance_ids, instance_ids[1:])),
        "analysisSuccessRate": round(successes / len(samples), 4) if samples else None,
        "errorRate": round(1 - successes / len(samples), 4) if samples else None,
        "note": "Metrics cover V1.1 health-check observations; Production API payloads are not instrumented or changed.",
    }
    atomic_json(OPS_DIR / "reports" / f"{report['date']}.json", report)
    return report


def render_dashboard(snapshot: dict[str, Any]) -> None:
    validate_operations_path()
    warm = snapshot["warmCache"]["games"]
    journal = snapshot["predictionJournal"]
    runtime = snapshot["runtime"]
    rows = {
        "API": f"HTTP {snapshot['api']['http']} / {snapshot['api']['responseMs']} ms",
        "Warm Cache": f"TW539={warm['tw539']['completed']}, Fantasy5={warm['ca-fantasy5']['completed']}",
        "Memory": runtime["memoryRss"] or "unavailable",
        "CPU": f"load1m={runtime['cpuLoad1m']}, cores={runtime['cpuCount']}",
        "Queue": f"{runtime['queue']['mode']} / {runtime['queue']['state']}",
        "Prediction Journal": f"TW539={journal['tw539']['records']}, Fantasy5={journal['ca-fantasy5']['records']}",
        "Last Analysis": f"TW539={warm['tw539']['lastAnalysisAt']}, Fantasy5={warm['ca-fantasy5']['lastAnalysisAt']}",
        "Render Instance": f"{runtime['instanceId']} / {runtime['commit']}",
    }
    body = "".join(f"<tr><th>{html.escape(key)}</th><td>{html.escape(str(value))}</td></tr>" for key, value in rows.items())
    page = f"""<!doctype html><html lang=\"zh-Hant\"><meta charset=\"utf-8\"><title>Lotto Lab Health Dashboard</title>
<style>body{{font:16px system-ui;margin:2rem;background:#f5f7fb}}table{{background:white;border-collapse:collapse;width:min(900px,100%)}}th,td{{padding:12px;border:1px solid #ddd;text-align:left}}.normal{{color:#087830}}</style>
<h1>Lotto Lab V1.1 Health Dashboard</h1><p class=\"normal\">Status: {html.escape(snapshot['status'])}</p><p>{html.escape(snapshot['checkedAt'])}</p><table>{body}</table></html>"""
    target = OPS_DIR / "dashboard" / "health_dashboard.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")


def run_all() -> dict[str, Any]:
    health = collect_health()
    return {"health": health, "backup": backup(), "report": operations_report()}


def run_audit_safely() -> bool:
    try:
        result = run_all()
        print(f"operations v1.1 daily check: {result['health']['status']}")
        return True
    except Exception as exc:
        print(f"operations v1.1 daily check failed: {type(exc).__name__}: {exc}")
        return False


def daemon_loop(interval: int = 86400, initial_delay: int = 60) -> None:
    """Run the read-only audit once daily; failures are logged, never repaired."""
    lock = acquire_scheduler_lock()
    if lock is None:
        print("operations v1.1 scheduler skipped: lock held by another process")
        return
    try:
        time.sleep(max(0, initial_delay))
        while True:
            run_audit_safely()
            time.sleep(max(3600, interval))
    finally:
        lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Lotto Lab V1.1 read-only operations toolkit")
    parser.add_argument("command", choices=("health", "backup", "report", "all", "daemon"), nargs="?", default="all")
    parser.add_argument("--interval", type=int, default=86400, help="daemon interval in seconds")
    args = parser.parse_args()
    actions = {"health": collect_health, "backup": backup, "report": operations_report, "all": run_all}
    if args.command == "daemon":
        daemon_loop(interval=args.interval, initial_delay=0)
    else:
        print(json.dumps(actions[args.command](), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
