"""Stage 3: immutable live Prediction Journal and AI-vs-App comparison.

This module deliberately does not change recommendation logic.  It records
only predictions captured before a future draw is known, settles them after a
later draw is present, and compares the independent Fantasy 5 engine with an
external App snapshot when both exist for the same target.

Walk-forward reconstructions are intentionally not written here and never
count toward the Stage 4 gate.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import os

try:
    import analysis_v2
except Exception:  # pragma: no cover - direct unit tests can still import helpers
    analysis_v2 = None


ROOT = Path(__file__).parent
DATA = Path(os.environ.get("LOTTO_PERSISTENT_DATA_DIR", ROOT / "data"))
STAGE_VERSION = "2026.08-prediction-journal-v3"
MIN_REAL_PRE_DRAW = 100
MAX_RECORDS = 5000
GAMES = {"tw539", "ca-fantasy5"}
JOURNAL_FILES = {
    "tw539": DATA / "prediction_journal_v3_tw539.json",
    "ca-fantasy5": DATA / "prediction_journal_v3_ca_fantasy5.json",
}
APP_FILE = DATA / "app_predictions_v3_ca_fantasy5.json"
_LOCK = threading.RLock()


class JournalError(ValueError):
    """Raised when an external snapshot is malformed or violates stage scope."""


def _now(value: str | None = None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _verify_prediction_lock(record: dict[str, Any]) -> None:
    """Reject journal records whose immutable prediction payload was changed."""
    prediction = record.get("prediction")
    prediction_hash = record.get("predictionHash")
    if prediction is not None or prediction_hash is not None:
        if not isinstance(prediction, dict) or not prediction_hash or _hash(prediction) != prediction_hash:
            raise JournalError("Prediction lock verification failed")
        return
    snapshot = record.get("snapshot")
    snapshot_hash = record.get("snapshotHash")
    if snapshot is not None and snapshot_hash and _hash(snapshot) != snapshot_hash:
        raise JournalError("Legacy prediction lock verification failed")


def _path_for(game: str, path: Path | None = None) -> Path:
    if game not in GAMES:
        raise JournalError("不支援的遊戲種類")
    return path or JOURNAL_FILES[game]


def _read_list(path: Path) -> list[dict[str, Any]]:
    try:
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _write_list(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(records[-MAX_RECORDS:], ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _number_list(values: Any, *, exact: int | None = None, minimum: int | None = None) -> list[int]:
    if not isinstance(values, (list, tuple)):
        raise JournalError("號碼必須是陣列")
    numbers = []
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise JournalError("號碼格式不正確") from exc
        if not 1 <= number <= 39:
            raise JournalError("號碼必須介於 01 到 39")
        numbers.append(number)
    if len(set(numbers)) != len(numbers):
        raise JournalError("號碼不可重複")
    if exact is not None and len(numbers) != exact:
        raise JournalError(f"號碼數量必須是 {exact} 顆")
    if minimum is not None and len(numbers) < minimum:
        raise JournalError(f"號碼數量至少是 {minimum} 顆")
    return sorted(numbers)


def _valid_draw(row: dict[str, Any]) -> bool:
    try:
        date_value = str(row.get("date", ""))
        period = str(row.get("period", ""))
        numbers = _number_list(row.get("numbers", []), exact=5)
        datetime.strptime(date_value, "%Y-%m-%d")
        return bool(period) and len(numbers) == 5
    except (JournalError, TypeError, ValueError):
        return False


def _ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted((row for row in rows if isinstance(row, dict) and _valid_draw(row)), key=lambda row: (row["date"], str(row["period"])))


def _target_key(period: Any) -> str:
    value = str(period or "").strip()
    if not value:
        raise JournalError("缺少資料截止期別")
    return f"next-after:{value}"


def _latest_position(rows: list[dict[str, Any]], cutoff: dict[str, Any]) -> int:
    for index, row in enumerate(rows):
        if str(row.get("period")) == str(cutoff.get("period")) and row.get("date") == cutoff.get("date"):
            return index
    return -1


def _outcome_for(record: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    ordered = _ordered(rows)
    cutoff_position = _latest_position(ordered, {"period": record.get("dataCutoffPeriod"), "date": record.get("dataCutoffDate")})
    if cutoff_position < 0:
        return None
    if cutoff_position + 1 >= len(ordered):
        return None
    draw = ordered[cutoff_position + 1]
    actual = _number_list(draw["numbers"], exact=5)
    snapshot = record.get("prediction") or record.get("snapshot", {})
    top5 = set(_number_list(snapshot.get("top5", []), exact=5))
    top10 = set(_number_list(snapshot.get("top10", []), exact=10))
    full15 = set(_number_list(snapshot.get("top15") or snapshot.get("full15", []), exact=15))
    actual_set = set(actual)
    return {
        "period": str(draw["period"]),
        "date": draw["date"],
        "numbers": actual,
        "hits5": len(top5 & actual_set),
        "hits10": len(top10 & actual_set),
        "hits15": len(full15 & actual_set),
        "settledAt": _now(),
    }


def _finalize(records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> bool:
    changed = False
    for record in records:
        _verify_prediction_lock(record)
        if record.get("recordType") != "live-pre-draw" or record.get("status") != "open":
            continue
        outcome = _outcome_for(record, rows)
        if outcome is not None:
            settlement = {
                "drawId": outcome["period"],
                "drawDate": outcome["date"],
                "winningNumbers": outcome["numbers"],
                "top5Hits": outcome["hits5"],
                "top10Hits": outcome["hits10"],
                "top15Hits": outcome["hits15"],
                "settledAt": outcome["settledAt"],
            }
            record["settlement"] = settlement
            record["outcome"] = outcome
            record["status"] = "closed"
            record["closedAt"] = outcome["settledAt"]
            _verify_prediction_lock(record)
            changed = True
    return changed


def journal_status(records: list[dict[str, Any]]) -> dict[str, Any]:
    live = [record for record in records if record.get("recordType") == "live-pre-draw"]
    closed = [record for record in live if record.get("status") == "closed" and record.get("outcome")]
    live_targets = {str(record.get("targetKey")) for record in live if record.get("targetKey")}
    closed_targets = {str(record.get("targetKey")) for record in closed if record.get("targetKey")}
    return {
        "stage": 3,
        "minimumRealPreDraw": MIN_REAL_PRE_DRAW,
        "realPreDrawCount": len(live_targets),
        "closedRealPreDrawCount": len(closed_targets),
        "openCount": sum(record.get("status") == "open" for record in live),
        "stage4Eligible": len(live) >= MIN_REAL_PRE_DRAW and len(closed) >= MIN_REAL_PRE_DRAW,
        "gate": "open" if len(live) >= MIN_REAL_PRE_DRAW and len(closed) >= MIN_REAL_PRE_DRAW else "closed",
        "backtestRecordsCounted": 0,
        "source": "live-pre-draw-only",
        "note": "第三階段只累積真實開獎前快照；回測重建不計入 100 期門檻。",
    }


def require_stage4(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Hard gate for future stages; Stage 3 cannot bypass the 100-record rule."""
    status = journal_status(records)
    if not status["stage4Eligible"]:
        raise JournalError("尚未累積 100 期真實開獎前 Prediction Journal，第四階段維持鎖定")
    return status


def _next_draw_date(game: str, cutoff_date: str) -> str:
    value = datetime.strptime(cutoff_date, "%Y-%m-%d").date() + timedelta(days=1)
    if game == "tw539" and value.weekday() == 6:
        value += timedelta(days=1)
    return value.isoformat()


def _complete_ranking(analysis: dict[str, Any]) -> list[int]:
    ranking = analysis.get("ranking") or []
    if not isinstance(ranking, list):
        raise JournalError("Complete ranking is missing")
    ordered = sorted(
        (item for item in ranking if isinstance(item, dict)),
        key=lambda item: int(item.get("rank", 10_000)),
    )
    return _number_list([item.get("number") for item in ordered], exact=39)


def _snapshot_from_analysis(game: str, analysis: dict[str, Any], latest: dict[str, Any], history: list[dict[str, Any]], captured_at: str) -> dict[str, Any]:
    tiers = analysis.get("candidateTiers") or {}
    top5 = _number_list(tiers.get("top5") or analysis.get("recommendation", []), exact=5)
    top10 = _number_list(tiers.get("top10") or (top5 + list(tiers.get("backup5", []))), exact=10)
    full15 = _number_list(tiers.get("full15") or (top10 + list(tiers.get("backup5", []))), exact=15)
    try:
        source_data_hash = analysis_v2.source_hash(game, history) if analysis_v2 is not None else None
    except Exception:
        source_data_hash = None
    model_version = str(analysis.get("modelVersion") or "unknown")
    ranking39 = _complete_ranking(analysis)
    repository_version = f"sha256:{source_data_hash}" if source_data_hash else "unknown"
    snapshot = {
        "top5": top5,
        "top10": top10,
        "full15": full15,
        "scores": analysis.get("modelScores", {}),
        "modelWeights": analysis.get("modelWeights", {}),
        "modelVersion": model_version,
        "dataCount": int(analysis.get("drawCount") or len(history)),
        "reasons": analysis.get("candidateDetails", []),
        "feature_breakdown": analysis.get("featureImportance", {}),
    }
    cutoff_period = str(latest.get("period") or "")
    if not cutoff_period or not latest.get("date"):
        raise JournalError("缺少資料截止期別或日期")
    target_key = _target_key(cutoff_period)
    prediction = {
        "predictionTime": captured_at,
        "drawId": target_key,
        "drawDate": _next_draw_date(game, latest["date"]),
        "top5": top5,
        "top10": top10,
        "top15": full15,
        "ranking39": ranking39,
        "modelVersion": model_version,
        "repositoryVersion": repository_version,
        "datasetHash": source_data_hash,
    }
    return {
        "stageVersion": STAGE_VERSION,
        "recordType": "live-pre-draw",
        "game": game,
        "status": "open",
        "targetKey": target_key,
        "targetPeriod": None,
        "targetDate": prediction["drawDate"],
        "predictionCapturedAt": captured_at,
        "dataCutoffPeriod": cutoff_period,
        "dataCutoffDate": latest["date"],
        "sourceDataHash": source_data_hash,
        "modelVersion": model_version,
        "snapshotHash": _hash(snapshot),
        "snapshot": snapshot,
        "predictionHash": _hash(prediction),
        "prediction": prediction,
        "locked": True,
        "settlement": None,
        "outcome": None,
    }


def record_live_prediction(
    game: str,
    analysis: dict[str, Any],
    latest: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    path: Path | None = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Record one current live snapshot; never record a historical reconstruction."""
    journal_path = _path_for(game, path)
    with _LOCK:
        records = _read_list(journal_path)
        changed = _finalize(records, history)
        if analysis.get("dataInsufficient"):
            if changed:
                _write_list(journal_path, records)
            return {"status": "insufficient", "journal": journal_status(records)}
        try:
            candidate = _snapshot_from_analysis(game, analysis, latest, history, _now(captured_at))
        except JournalError:
            if changed:
                _write_list(journal_path, records)
            return {"status": "insufficient", "journal": journal_status(records)}
        existing = next((record for record in records if record.get("recordType") == "live-pre-draw" and record.get("targetKey") == candidate["targetKey"] and record.get("modelVersion") == candidate["modelVersion"]), None)
        if existing is None:
            candidate["journalId"] = _hash({"game": game, "targetKey": candidate["targetKey"], "modelVersion": candidate["modelVersion"], "sourceDataHash": candidate["sourceDataHash"]})[:24]
            records.append(candidate)
            changed = True
        if changed:
            _write_list(journal_path, records)
        return {"status": "recorded" if existing is None else "deduplicated", "journalId": (existing or candidate).get("journalId"), "journal": journal_status(records)}


def get_journal(game: str, history: list[dict[str, Any]], *, limit: int = 100, path: Path | None = None) -> dict[str, Any]:
    journal_path = _path_for(game, path)
    with _LOCK:
        records = _read_list(journal_path)
        changed = _finalize(records, history)
        if changed:
            _write_list(journal_path, records)
        visible = [record for record in records if record.get("recordType") == "live-pre-draw"][-max(1, min(int(limit), 500)) :]
        return {"game": game, "stageVersion": STAGE_VERSION, "records": visible, "status": journal_status(records), "source": "live-pre-draw-only"}


def _app_snapshot(snapshot: dict[str, Any], captured_at: str | None = None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise JournalError("App 快照格式不正確")
    if snapshot.get("game") != "ca-fantasy5":
        raise JournalError("AI vs App Battle 只接受 California Fantasy 5 App 快照")
    if snapshot.get("actual") is not None or snapshot.get("outcome") is not None:
        raise JournalError("App 開獎前快照不可包含開獎結果")
    target_key = str(snapshot.get("targetKey") or snapshot.get("targetPeriod") or "").strip()
    if not target_key:
        raise JournalError("App 快照缺少目標期別")
    top5 = _number_list(snapshot.get("top5"), exact=5)
    full15 = _number_list(snapshot.get("full15"), exact=15)
    clean = {
        "top5": top5,
        "full15": full15,
        "scores": snapshot.get("scores", {}),
        "sourceLabel": str(snapshot.get("sourceLabel") or "external-app"),
    }
    return {
        "stageVersion": STAGE_VERSION,
        "recordType": "external-app-pre-draw",
        "game": "ca-fantasy5",
        "targetKey": target_key,
        "predictionCapturedAt": _now(captured_at),
        "snapshot": clean,
        "snapshotHash": _hash(clean),
        "outcome": None,
    }


def submit_app_snapshot(snapshot: dict[str, Any], *, path: Path | None = None, captured_at: str | None = None) -> dict[str, Any]:
    app_path = path or APP_FILE
    candidate = _app_snapshot(snapshot, captured_at)
    with _LOCK:
        records = _read_list(app_path)
        existing = next((record for record in records if record.get("targetKey") == candidate["targetKey"]), None)
        if existing is None:
            candidate["appSnapshotId"] = _hash({"targetKey": candidate["targetKey"], "snapshotHash": candidate["snapshotHash"]})[:24]
            records.append(candidate)
            _write_list(app_path, records)
            return {"status": "recorded", "appSnapshotId": candidate["appSnapshotId"], "count": len(records)}
        return {"status": "deduplicated", "appSnapshotId": existing.get("appSnapshotId"), "count": len(records)}


def _battle_row(ai: dict[str, Any], app: dict[str, Any]) -> dict[str, Any] | None:
    outcome = ai.get("outcome") or {}
    if ai.get("status") != "closed" or not outcome:
        return None
    actual = _number_list(outcome.get("numbers"), exact=5)
    actual_set = set(actual)
    ai_snapshot = ai.get("snapshot", {})
    app_snapshot = app.get("snapshot", {})
    ai_top5 = _number_list(ai_snapshot.get("top5"), exact=5)
    ai_full15 = _number_list(ai_snapshot.get("full15"), exact=15)
    app_top5 = _number_list(app_snapshot.get("top5"), exact=5)
    app_full15 = _number_list(app_snapshot.get("full15"), exact=15)
    return {
        "targetKey": ai.get("targetKey"),
        "actualPeriod": outcome.get("period"),
        "actualDate": outcome.get("date"),
        "actual": actual,
        "aiTop5": ai_top5,
        "appTop5": app_top5,
        "aiHits5": len(set(ai_top5) & actual_set),
        "appHits5": len(set(app_top5) & actual_set),
        "aiHits15": len(set(ai_full15) & actual_set),
        "appHits15": len(set(app_full15) & actual_set),
    }


def _battle_metrics(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "top5AverageHit": None, "top15AverageHit": None, "hitDistribution5": {str(index): 0 for index in range(6)}, "hitDistribution15": {str(index): 0 for index in range(6)}}
    dist5 = {str(index): 0 for index in range(6)}
    dist15 = {str(index): 0 for index in range(6)}
    for row in rows:
        dist5[str(row[f"{prefix}Hits5"])] += 1
        dist15[str(row[f"{prefix}Hits15"])] += 1
    return {
        "count": len(rows),
        "top5AverageHit": round(sum(row[f"{prefix}Hits5"] for row in rows) / len(rows), 6),
        "top15AverageHit": round(sum(row[f"{prefix}Hits15"] for row in rows) / len(rows), 6),
        "hitDistribution5": dist5,
        "hitDistribution15": dist15,
    }


def get_battle(history: list[dict[str, Any]], *, limit: int = 100, journal_path: Path | None = None, app_path: Path | None = None) -> dict[str, Any]:
    with _LOCK:
        journal = get_journal("ca-fantasy5", history, limit=5000, path=journal_path)
        app_records = _read_list(app_path or APP_FILE)
        app_by_target = {record.get("targetKey"): record for record in app_records if record.get("recordType") == "external-app-pre-draw"}
        rows = []
        for ai in journal["records"]:
            app = app_by_target.get(ai.get("targetKey"))
            if app is not None:
                row = _battle_row(ai, app)
                if row is not None:
                    rows.append(row)
        rows = rows[-max(1, min(int(limit), 500)) :]
        ai_metrics = _battle_metrics(rows, "ai")
        app_metrics = _battle_metrics(rows, "app")
        if not app_records:
            status = "waiting-for-app-snapshots"
        elif len(rows) < MIN_REAL_PRE_DRAW:
            status = "insufficient-battle-history"
        else:
            status = "ready"
        winner = None
        if rows:
            if ai_metrics["top15AverageHit"] > app_metrics["top15AverageHit"]:
                winner = "ai"
            elif ai_metrics["top15AverageHit"] < app_metrics["top15AverageHit"]:
                winner = "app"
            else:
                winner = "tie"
        return {
            "game": "ca-fantasy5",
            "stageVersion": STAGE_VERSION,
            "status": status,
            "minimumBattleRecords": MIN_REAL_PRE_DRAW,
            "matchedCount": len(rows),
            "ai": ai_metrics,
            "app": app_metrics,
            "records": rows,
            "winner": winner,
            "note": "只比較 AI 與外部 App 既有快照；沒有 App 資料不自行產生。",
        }
