"""Independent, auditable v2 engines for Taiwan 539 and California Fantasy 5.

The engines intentionally do not import the legacy server analysis functions.
Each game has its own config, history path, model directory, weight path, and
feature adapter. The walk-forward code is deterministic for audit purposes;
the random baseline is never included in production ensemble weights.
"""

from __future__ import annotations

import builtins
import csv
import hashlib
import json
import math
import random
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    import joblib
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    SKLEARN_AVAILABLE = True
except Exception:  # pragma: no cover - deployment installs requirements
    joblib = None
    np = None
    HistGradientBoostingClassifier = None
    RandomForestClassifier = None
    LogisticRegression = None
    SKLEARN_AVAILABLE = False


ROOT = Path(__file__).parent
DATA = ROOT / "data"
MODEL_ROOT = DATA / "models_v2"
MODEL_VERSION = "2026.08-independent-baselines-v2"
MAX_NUMBER = 39
PICK_COUNT = 5
SEED = 20260801
BASELINE_MODELS = (
    "uniform",
    "random",
    "long-term-frequency",
    "rolling-frequency",
    "gap",
    "repeat",
    "co-occurrence",
)

TW539_CONFIG = {
    "game": "tw539",
    "label": "今彩539",
    "history_path": DATA / "tw539_database.json",
    "weight_path": DATA / "weights_v2_tw539.json",
    "model_dir": MODEL_ROOT / "tw539",
    "timezone": "Asia/Taipei",
    "windows": (5, 10, 20, 30, 60, 100, 300),
    "features": ("freq_5", "freq_10", "freq_30", "freq_100", "freq_300", "gap", "repeat_prev", "repeat_2", "neighbor", "tail", "odd", "size", "zone", "sum", "span", "ac", "same_tail", "consecutive", "weekday", "month"),
}

CA_FANTASY5_CONFIG = {
    "game": "ca-fantasy5",
    "label": "California Fantasy 5",
    "history_path": DATA / "ca_fantasy5_database_v2.json",
    "weight_path": DATA / "weights_v2_ca_fantasy5.json",
    "model_dir": MODEL_ROOT / "ca-fantasy5",
    "timezone": "America/Los_Angeles",
    "windows": (5, 7, 10, 14, 30, 60, 100, 300),
    "features": ("freq_5", "freq_7", "freq_14", "freq_30", "freq_100", "freq_300", "gap", "repeat_prev", "repeat_2", "pair", "tail", "odd", "size", "zone", "sum", "span", "ac", "same_tail", "consecutive", "weekday_pacific", "dst_pacific", "month_pacific"),
}

CONFIGS = {"tw539": TW539_CONFIG, "ca-fantasy5": CA_FANTASY5_CONFIG}


def _config(game: str) -> dict[str, Any]:
    if game not in CONFIGS:
        raise ValueError(f"unsupported game: {game}")
    return CONFIGS[game]


def load_history(game: str, path: Path | None = None) -> list[dict[str, Any]]:
    config = _config(game)
    source = path or config["history_path"]
    rows = json.loads(source.read_text(encoding="utf-8"))
    values = [row for row in rows if row.get("game", game) == game]
    return sorted(values, key=lambda row: (row["date"], str(row["period"])))


def _numbers(row: dict[str, Any]) -> list[int]:
    return builtins.sorted(builtins.int(number) for number in row["numbers"])


def _draw_shape(numbers: list[int]) -> dict[str, float]:
    ordered = sorted(numbers)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    distinct_differences = len({abs(b - a) for i, a in enumerate(ordered) for b in ordered[i + 1:]})
    return {
        "sum": float(sum(ordered)),
        "span": float(max(ordered) - min(ordered)),
        "ac": float(distinct_differences - len(ordered) + 2),
        "same_tail": float(len(ordered) - len({number % 10 for number in ordered})),
        "consecutive": float(sum(1 for gap in gaps if gap == 1)),
        "odd": float(sum(number % 2 for number in ordered)),
        "size": float(sum(number >= 20 for number in ordered)),
    }


def _mean_shape(history: list[dict[str, Any]]) -> dict[str, float]:
    if not history:
        return {key: 0.0 for key in ("sum", "span", "ac", "same_tail", "consecutive", "odd", "size")}
    values = [_draw_shape(_numbers(row)) for row in history]
    return {key: statistics.fmean(value[key] for value in values) for key in values[0]}


def _frequency(history: list[dict[str, Any]], window: int) -> dict[int, int]:
    counts = {number: 0 for number in range(1, MAX_NUMBER + 1)}
    for row in history[-window:]:
        for number in _numbers(row):
            counts[number] += 1
    return counts


def _gap(history: list[dict[str, Any]], number: int) -> int:
    for distance, row in enumerate(reversed(history)):
        if number in _numbers(row):
            return distance
    return len(history)


def _weekday(row: dict[str, Any], game: str) -> int:
    value = row.get("weekday")
    if value not in (None, ""):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return datetime.strptime(row["date"], "%Y-%m-%d").weekday()


def _feature_row(history: list[dict[str, Any]], number: int, game: str) -> list[float]:
    config = _config(game)
    windows = config["windows"]
    counts = {window: _frequency(history, window) for window in windows}
    latest = set(_numbers(history[-1])) if history else set()
    latest_two = set(number for row in history[-2:] for number in _numbers(row))
    shape = _mean_shape(history[-30:])
    current = _draw_shape(_numbers(history[-1])) if history else shape
    row: dict[str, float] = {}
    for feature in config["features"]:
        if feature.startswith("freq_"):
            window = int(feature.split("_")[1])
            row[feature] = counts.get(window, counts.get(max(windows), {})).get(number, 0) / max(1, window)
        elif feature == "gap":
            row[feature] = min(30, _gap(history, number)) / 30
        elif feature == "repeat_prev":
            row[feature] = float(number in latest)
        elif feature == "repeat_2":
            row[feature] = float(number in latest_two)
        elif feature == "neighbor":
            row[feature] = float((number - 1 in latest) + (number + 1 in latest))
        elif feature == "pair":
            row[feature] = float(sum(number in _numbers(item) for item in history[-30:])) / max(1, len(history[-30:]))
        elif feature == "tail":
            row[feature] = float(sum(number % 10 == other % 10 for other in (n for item in history[-30:] for n in _numbers(item)))) / max(1, len(history[-30:]) * PICK_COUNT)
        elif feature == "odd":
            row[feature] = float(number % 2)
        elif feature == "size":
            row[feature] = float(number >= 20)
        elif feature == "zone":
            row[feature] = float(min(2, (number - 1) // 13))
        elif feature == "sum":
            row[feature] = (current["sum"] - shape["sum"]) / 100
        elif feature == "span":
            row[feature] = (current["span"] - shape["span"]) / 39
        elif feature == "ac":
            row[feature] = (current["ac"] - shape["ac"]) / 10
        elif feature == "same_tail":
            row[feature] = shape["same_tail"] / PICK_COUNT
        elif feature == "consecutive":
            row[feature] = shape["consecutive"] / PICK_COUNT
        elif feature in {"weekday", "weekday_pacific"}:
            row[feature] = _weekday(history[-1], game) / 6 if history else 0.0
        elif feature == "dst_pacific":
            if history:
                local_date = datetime.strptime(history[-1]["date"], "%Y-%m-%d").replace(tzinfo=ZoneInfo("America/Los_Angeles"))
                row[feature] = float(local_date.dst() != timedelta(0))
            else:
                row[feature] = 0.0
        elif feature in {"month", "month_pacific"}:
            row[feature] = float(datetime.strptime(history[-1]["date"], "%Y-%m-%d").month) / 12 if history else 0.0
        else:
            row[feature] = 0.0
    return [float(row[feature]) for feature in config["features"]]


def _prequential_features(history: list[dict[str, Any]], game: str) -> tuple[Any, Any]:
    if not SKLEARN_AVAILABLE:
        raise RuntimeError("scikit-learn is required for v2 model training")
    features: list[list[list[float]]] = []
    labels: list[list[int]] = []
    for index, row in enumerate(history):
        prefix = history[:index]
        features.append([_feature_row(prefix, number, game) for number in range(1, MAX_NUMBER + 1)])
        actual = set(_numbers(row))
        labels.append([int(number in actual) for number in range(1, MAX_NUMBER + 1)])
    return np.asarray(features, dtype=float), np.asarray(labels, dtype=int)


def _normalize_scores(values: dict[int, float]) -> dict[int, float]:
    low = min(values.values() or [0.0])
    high = max(values.values() or [1.0])
    if high <= low:
        return {number: 0.5 for number in values}
    return {number: max(0.0, min(1.0, (value - low) / (high - low))) for number, value in values.items()}


def _baseline_scores(history: list[dict[str, Any]], game: str, target_index: int = 0) -> dict[str, dict[int, float]]:
    config = _config(game)
    counts_long = _frequency(history, 1000)
    counts_rolling = {number: sum(_frequency(history, window)[number] / max(1, window) for window in config["windows"] if window <= 300) for number in range(1, MAX_NUMBER + 1)}
    gaps = {number: _gap(history, number) for number in range(1, MAX_NUMBER + 1)}
    previous = set(_numbers(history[-1])) if history else set()
    pair = {number: 0.0 for number in range(1, MAX_NUMBER + 1)}
    if previous:
        for row in history[-300:]:
            values = set(_numbers(row))
            for number in previous:
                for other in values:
                    if other != number:
                        pair[other] += 1
    rng = random.Random(SEED + target_index + (0 if game == "tw539" else 100000))
    random_values = {number: rng.random() for number in range(1, MAX_NUMBER + 1)}
    return {
        "uniform": {number: 0.5 for number in range(1, MAX_NUMBER + 1)},
        "random": random_values,
        "long-term-frequency": _normalize_scores({number: counts_long[number] for number in counts_long}),
        "rolling-frequency": _normalize_scores(counts_rolling),
        "gap": _normalize_scores({number: math.exp(-min(gaps[number], 40) / 20) for number in gaps}),
        "repeat": _normalize_scores({number: (1.0 if number in previous else 0.0) + 0.25 * pair[number] for number in pair}),
        "co-occurrence": _normalize_scores(pair),
    }


def _supervised_models() -> dict[str, Any]:
    if not SKLEARN_AVAILABLE:
        return {}
    return {
        "logistic-regression": LogisticRegression(max_iter=150, class_weight="balanced", random_state=SEED),
        "random-forest": RandomForestClassifier(n_estimators=16, max_depth=6, min_samples_leaf=3, random_state=SEED, n_jobs=1, class_weight="balanced_subsample"),
        "hist-gradient-boosting": HistGradientBoostingClassifier(max_iter=24, max_leaf_nodes=12, learning_rate=0.08, random_state=SEED),
    }


def _fit_supervised(history: list[dict[str, Any]], game: str, target_index: int, precomputed: tuple[Any, Any] | None = None, train_window: int = 300) -> dict[str, Any]:
    if not SKLEARN_AVAILABLE:
        return {}
    feature_matrix, labels = precomputed or _prequential_features(history, game)
    start = max(1, target_index - train_window)
    x = feature_matrix[start:target_index].reshape(-1, len(_config(game)["features"]))
    y = labels[start:target_index].reshape(-1)
    models: dict[str, Any] = {}
    for name, model in _supervised_models().items():
        model.fit(x, y)
        models[name] = model
    return models


def _score_supervised(models: dict[str, Any], current_features: list[list[float]]) -> dict[str, dict[int, float]]:
    if not models:
        return {}
    x = np.asarray(current_features, dtype=float)
    scores: dict[str, dict[int, float]] = {}
    for name, model in models.items():
        values = model.predict_proba(x)[:, 1] if hasattr(model, "predict_proba") else model.predict(x)
        scores[name] = {number: float(value) for number, value in zip(range(1, MAX_NUMBER + 1), values)}
    return scores


def _model_names() -> tuple[str, ...]:
    return BASELINE_MODELS + ("logistic-regression", "random-forest", "hist-gradient-boosting")


def _active_model_names(scores: dict[str, dict[int, float]]) -> tuple[str, ...]:
    """Keep unavailable optional models out of the ensemble without blocking analysis."""
    return tuple(name for name in _model_names() if name in scores)


def _normalized_weights_for(names: tuple[str, ...], stored: dict[str, Any]) -> dict[str, float]:
    if not names:
        return {}
    fallback = 1 / len(names)
    values = {name: max(0.0, float(stored.get(name, fallback))) for name in names}
    total = sum(values.values()) or 1.0
    return {name: value / total for name, value in values.items()}


def _weight_from_history(performance: dict[str, list[float]], game: str) -> dict[str, float]:
    names = _model_names()
    if not performance or not any(performance.get(name) for name in names):
        return {name: round(1 / len(names), 8) for name in names}
    raw: dict[str, float] = {}
    for name in names:
        values = performance.get(name, [])
        recent = values[-30:]
        medium = values[-100:]
        long = values[-300:]
        score = 0.50 * (statistics.fmean(recent) if recent else 0.0) + 0.30 * (statistics.fmean(medium) if medium else 0.0) + 0.20 * (statistics.fmean(long) if long else 0.0)
        raw[name] = max(0.05, score + 0.05)
    total = sum(raw.values())
    weights = {name: raw[name] / total for name in names}
    cap = 0.28
    for _ in range(3):
        excess = sum(max(0.0, value - cap) for value in weights.values())
        if excess <= 0:
            break
        under = [name for name, value in weights.items() if value < cap]
        for name in under:
            weights[name] += excess / max(1, len(under))
        for name in weights:
            weights[name] = min(cap, weights[name])
    total = sum(weights.values())
    return {name: round(value / total, 8) for name, value in weights.items()}


def _probabilities(scores: dict[int, float]) -> dict[int, float]:
    total = sum(max(0.0001, value) for value in scores.values())
    return {number: max(0.0001, min(0.9999, value / total * PICK_COUNT)) for number, value in scores.items()}


def _metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    if not events:
        return {"testedCount": 0, "top5AverageHit": 0.0, "top10AverageHit": 0.0, "top15AverageHit": 0.0, "distribution": {str(i): 0 for i in range(6)}, "brierScore": None, "logLoss": None}
    def avg(key: str) -> float:
        return statistics.fmean(event[key] for event in events)
    distribution = {str(i): sum(event["top15"] == i for event in events) / len(events) for i in range(6)}
    return {"testedCount": len(events), "top5AverageHit": round(avg("top5"), 6), "top10AverageHit": round(avg("top10"), 6), "top15AverageHit": round(avg("top15"), 6), "distribution": {key: round(value, 6) for key, value in distribution.items()}, "brierScore": round(statistics.fmean(event["brier"] for event in events), 8), "logLoss": round(statistics.fmean(event["logloss"] for event in events), 8)}


def _bootstrap_ci(values: list[float], seed: int = SEED, rounds: int = 800) -> list[float]:
    if not values:
        return [None, None]
    rng = random.Random(seed)
    means = [statistics.fmean(rng.choice(values) for _ in values) for _ in range(rounds)]
    means.sort()
    return [round(means[int(0.025 * rounds)], 6), round(means[int(0.975 * rounds)], 6)]


def _permutation_pvalue(values: list[float], baseline: list[float], seed: int = SEED, rounds: int = 1200) -> float | None:
    if not values or len(values) != len(baseline):
        return None
    observed = statistics.fmean(a - b for a, b in zip(values, baseline))
    rng = random.Random(seed)
    extreme = 0
    for _ in range(rounds):
        signed = [((a - b) if rng.random() < 0.5 else -(a - b)) for a, b in zip(values, baseline)]
        if statistics.fmean(signed) >= observed:
            extreme += 1
    return round((extreme + 1) / (rounds + 1), 6)


def _backtest_display_fields(events: list[dict[str, Any]], model_events: dict[str, list[dict[str, Any]]], ensemble_summary: dict[str, Any]) -> dict[str, Any]:
    """Adapt the audited walk-forward result to the existing UI contract."""
    count = len(events)
    if not count:
        return {"testedCount": 0, "recentRows": [], "tierMetrics": {}}

    def rate(key: str, threshold: int) -> float:
        return round(sum(event[key] >= threshold for event in events) / count * 100, 2)

    distribution = {str(hits): sum(event["top5"] == hits for event in events) for hits in range(6)}
    tier_metrics: dict[str, dict[str, Any]] = {}
    for label, key in (("5", "top5"), ("10", "top10"), ("15", "top15")):
        values = [event[key] for event in events]
        tier_metrics[label] = {
            "hitRate": round(sum(value >= 1 for value in values) / count * 100, 2),
            "averageHit": round(statistics.fmean(values), 6),
            "twoPlusRate": round(sum(value >= 2 for value in values) / count * 100, 2),
        }
    profiles = []
    for name, model_rows in model_events.items():
        if not model_rows:
            continue
        profiles.append({
            "label": name,
            "status": "active",
            "averageHit": round(statistics.fmean(row["top5"] for row in model_rows), 6),
            "hitRate15": round(sum(row["top15"] >= 1 for row in model_rows) / len(model_rows) * 100, 2),
            "bestHit": max(row["top5"] for row in model_rows),
        })
    return {
        "testedCount": count,
        "averageHit": ensemble_summary["top5AverageHit"],
        "bestHit": max(event["top5"] for event in events),
        "onePlusRate": rate("top5", 1),
        "twoPlusRate": rate("top5", 2),
        "threePlusRate": rate("top5", 3),
        "threePlusCount": sum(event["top5"] >= 3 for event in events),
        "distribution": distribution,
        "tierMetrics": tier_metrics,
        "recentRows": [
            {"date": event["date"], "period": event["period"], "pick": event["top5Numbers"], "actual": event["actual"], "hits": event["top5"]}
            for event in events[-24:]
        ],
        "modelProfiles": profiles,
        "method": "Walk-forward 回測：每一期只使用該期以前資料；顯示的是相對排序，不是真實中獎機率。",
        "cacheStatus": "complete",
    }


def walk_forward(game: str, rows: list[dict[str, Any]], eval_limit: int | None = None) -> dict[str, Any]:
    history = sorted(rows, key=lambda row: (row["date"], str(row["period"])))
    train_window = min(300, max(1, len(history) - 1))
    if len(history) <= train_window:
        return {"game": game, "testedCount": 0, "trainWindow": train_window, "events": [], "models": {}, "weights": []}
    features = _prequential_features(history, game) if SKLEARN_AVAILABLE else None
    start = train_window
    targets = list(range(start, len(history)))
    if eval_limit:
        targets = targets[-eval_limit:]
    names = _model_names()
    performance = {name: [] for name in names}
    model_events = {name: [] for name in names}
    weights_history: list[dict[str, Any]] = []
    ensemble_events: list[dict[str, Any]] = []
    for target in targets:
        prefix = history[:target]
        baseline = _baseline_scores(prefix, game, target)
        fitted = _fit_supervised(history, game, target, features, train_window=train_window) if SKLEARN_AVAILABLE else {}
        current_features = [_feature_row(prefix, number, game) for number in range(1, MAX_NUMBER + 1)]
        scores = {**baseline, **_score_supervised(fitted, current_features)}
        active_names = _active_model_names(scores)
        weights = _normalized_weights_for(active_names, _weight_from_history(performance, game))
        ensemble = {number: sum(weights[name] * scores[name][number] for name in active_names) for number in range(1, MAX_NUMBER + 1)}
        actual = set(_numbers(history[target]))
        event_by_model: dict[str, dict[str, Any]] = {}
        for name in active_names:
            ranked = sorted(scores[name], key=lambda number: (-scores[name][number], number))
            probabilities = _probabilities(scores[name])
            brier = statistics.fmean((probabilities[number] - int(number in actual)) ** 2 for number in range(1, MAX_NUMBER + 1))
            logloss = -statistics.fmean(math.log(max(1e-6, probabilities[number] if number in actual else 1 - probabilities[number])) for number in range(1, MAX_NUMBER + 1))
            event = {"date": history[target]["date"], "period": history[target]["period"], "top5": len(set(ranked[:5]) & actual), "top10": len(set(ranked[:10]) & actual), "top15": len(set(ranked[:15]) & actual), "brier": brier, "logloss": logloss}
            model_events[name].append(event)
            performance[name].append(event["top15"])
            event_by_model[name] = event
        ranked_ensemble = sorted(ensemble, key=lambda number: (-ensemble[number], number))
        probabilities = _probabilities(ensemble)
        event = {"date": history[target]["date"], "period": history[target]["period"], "top5": len(set(ranked_ensemble[:5]) & actual), "top10": len(set(ranked_ensemble[:10]) & actual), "top15": len(set(ranked_ensemble[:15]) & actual), "brier": statistics.fmean((probabilities[number] - int(number in actual)) ** 2 for number in range(1, MAX_NUMBER + 1)), "logloss": -statistics.fmean(math.log(max(1e-6, probabilities[number] if number in actual else 1 - probabilities[number])) for number in range(1, MAX_NUMBER + 1)), "actual": sorted(actual), "top5Numbers": ranked_ensemble[:5], "top15Numbers": ranked_ensemble[:15]}
        ensemble_events.append(event)
        weights_history.append({"game": game, "period": history[target]["period"], "date": history[target]["date"], **weights})
    model_summary = {name: _metrics(events) for name, events in model_events.items() if events}
    ensemble_summary = _metrics(ensemble_events)
    random_events = model_events["random"]
    comparisons = {}
    for name in ("random", "long-term-frequency", "rolling-frequency", "gap"):
        base = _metrics(model_events[name])
        comparisons[name] = {"top5Delta": round(ensemble_summary["top5AverageHit"] - base["top5AverageHit"], 6), "top15Delta": round(ensemble_summary["top15AverageHit"] - base["top15AverageHit"], 6), "baseline": base}
    result = {"game": game, "modelVersion": MODEL_VERSION, "testedCount": len(ensemble_events), "trainWindow": train_window, "evalLimit": eval_limit, "ensemble": ensemble_summary, "models": model_summary, "modelEvents": model_events, "weights": weights_history, "events": ensemble_events, "baselineComparison": comparisons, "bootstrap95": {"top5": _bootstrap_ci([event["top5"] for event in ensemble_events]), "top10": _bootstrap_ci([event["top10"] for event in ensemble_events]), "top15": _bootstrap_ci([event["top15"] for event in ensemble_events])}, "permutationPValueVsRandom": _permutation_pvalue([event["top15"] for event in ensemble_events], [event["top15"] for event in random_events]), "note": "Walk-forward only: each target uses rows strictly before the target; scores are relative rankings and not true win probabilities."}
    result.update(_backtest_display_fields(ensemble_events, model_events, ensemble_summary))
    return result


def save_walkforward_csv(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["game", "date", "period", "top5", "top10", "top15", "brier", "logloss", "actual", "top5Numbers", "top15Numbers"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for event in result.get("events", []):
            row = dict(event)
            row["game"] = result.get("game")
            for key in ("actual", "top5Numbers", "top15Numbers"):
                row[key] = json.dumps(row.get(key, []), ensure_ascii=False)
            writer.writerow({field: row.get(field) for field in fields})


def _artifact_manifest(game: str, models: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    config = _config(game)
    config["model_dir"].mkdir(parents=True, exist_ok=True)
    manifest = {"game": game, "modelVersion": MODEL_VERSION, "seed": SEED, "trainedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "historyCount": len(history), "featureNames": list(config["features"]), "baselineModels": [], "models": []}
    baseline_path = config["model_dir"] / "baseline_models.json"
    baseline_manifest = {
        "game": game,
        "modelVersion": MODEL_VERSION,
        "seed": SEED,
        "modelType": "deterministic_score_function",
        "models": [{"name": name, "loadable": True, "inputDimension": MAX_NUMBER, "outputDimension": MAX_NUMBER} for name in BASELINE_MODELS],
    }
    baseline_path.write_text(json.dumps(baseline_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["baselineModels"] = baseline_manifest["models"]
    for name, model in models.items():
        path = config["model_dir"] / f"{name}.joblib"
        joblib.dump(model, path)
        manifest["models"].append({"name": name, "path": str(path), "inputDimension": len(config["features"]), "outputDimension": 1, "loadable": True})
    (config["model_dir"] / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def train_and_save(game: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not SKLEARN_AVAILABLE:
        raise RuntimeError("scikit-learn is required for model artifacts")
    history = sorted(rows, key=lambda row: (row["date"], str(row["period"])))
    features, labels = _prequential_features(history, game)
    target = len(history)
    fitted = _fit_supervised(history, game, target, (features, labels), train_window=min(300, max(1, len(history) - 1)))
    manifest = _artifact_manifest(game, fitted, history)
    scores = _score_supervised(fitted, [_feature_row(history, number, game) for number in range(1, MAX_NUMBER + 1)])
    result = walk_forward(game, history, eval_limit=min(100, max(0, len(history) - min(300, len(history) - 1))))
    weights = result.get("weights", [])[-1] if result.get("weights") else {"game": game}
    config = _config(game)
    config["weight_path"].write_text(json.dumps({"game": game, "modelVersion": MODEL_VERSION, "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "weights": weights, "source": "walk-forward previous outcomes"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": manifest, "scores": scores, "weights": weights, "walkForward": result}


def load_model_artifacts(game: str) -> dict[str, Any]:
    config = _config(game)
    manifest_path = config["model_dir"] / "manifest.json"
    baseline_path = config["model_dir"] / "baseline_models.json"
    if not manifest_path.exists() or not SKLEARN_AVAILABLE:
        return {"loaded": {}, "baselines": [], "errors": ["manifest or scikit-learn unavailable"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    loaded: dict[str, Any] = {}
    errors: list[str] = []
    for item in manifest.get("models", []):
        try:
            artifact_path = Path(str(item.get("path", "")))
            if not artifact_path.exists():
                artifact_path = config["model_dir"] / artifact_path.name
            loaded[item["name"]] = joblib.load(artifact_path)
        except Exception as exc:
            errors.append(f"{item.get('name')}: {exc}")
    baselines: list[str] = []
    if baseline_path.exists():
        try:
            baseline_manifest = json.loads(baseline_path.read_text(encoding="utf-8"))
            baselines = [item["name"] for item in baseline_manifest.get("models", []) if item.get("loadable")]
        except Exception as exc:
            errors.append(f"baseline-manifest: {exc}")
    return {"loaded": loaded, "baselines": baselines, "errors": errors, "manifest": manifest}


def _ensure_latest_artifacts(game: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    artifacts = load_model_artifacts(game)
    if len(artifacts.get("loaded", {})) >= 3:
        return artifacts
    if not SKLEARN_AVAILABLE:
        return artifacts
    train_and_save(game, history)
    return load_model_artifacts(game)


def analyze(game: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    config = _config(game)
    history = sorted(rows, key=lambda row: (row["date"], str(row["period"])))
    if len(history) < 301:
        return {"game": game, "dataInsufficient": True, "drawCount": len(history), "recommendation": [], "candidateTiers": {"top5": [], "top10": [], "full15": []}, "modelVersion": MODEL_VERSION, "note": "v2 至少需要 301 期已驗證資料。"}
    artifacts = _ensure_latest_artifacts(game, history)
    latest_scores: dict[str, dict[int, float]] = _baseline_scores(history, game, len(history))
    loaded = artifacts.get("loaded", {})
    current_features = [_feature_row(history, number, game) for number in range(1, MAX_NUMBER + 1)]
    latest_scores.update(_score_supervised(loaded, current_features))
    active_names = _active_model_names(latest_scores)
    if not active_names:
        return {"game": game, "dataInsufficient": True, "drawCount": len(history), "recommendation": [], "candidateTiers": {"top5": [], "top10": [], "full15": []}, "modelVersion": MODEL_VERSION, "note": "沒有可用模型分數，暫停產生分析。"}
    stored_weight = json.loads(config["weight_path"].read_text(encoding="utf-8")).get("weights", {}) if config["weight_path"].exists() else {}
    weights = _normalized_weights_for(active_names, stored_weight)
    ensemble = {number: sum(weights[name] * latest_scores[name][number] for name in active_names) for number in range(1, MAX_NUMBER + 1)}
    backtest = walk_forward(game, history, eval_limit=min(24, len(history) - 301))
    ranked = sorted(ensemble, key=lambda number: (-ensemble[number], number))
    pool = ranked[:15]
    details = [{"number": number, "rank": index + 1, "score": round(ensemble[number] * 100, 4), "reason": "多模型相對排序；基準與監督模型共同支持" if index < 5 else "候選池排序", "relativeConfidence": "高" if index < 5 else "中"} for index, number in enumerate(pool)]
    unavailable = [name for name in _model_names() if name not in active_names]
    return {"game": game, "modelVersion": MODEL_VERSION, "drawCount": len(history), "selectedDrawCount": len(history), "dataInsufficient": False, "recommendation": pool[:5], "backupRecommendation": pool[5:10], "candidateTiers": {"top5": pool[:5], "backup5": pool[5:10], "top10": pool[:10], "full15": pool}, "candidateDetails": details, "ranking": [{"number": number, "rank": index + 1, "score": round(ensemble[number] * 100, 4)} for index, number in enumerate(ranked)], "modelScores": {name: {str(number): round(value, 8) for number, value in scores.items()} for name, scores in latest_scores.items() if name in active_names}, "modelWeights": weights, "modelCatalog": {name: {"status": "active", "label": name, "features": list(config["features"])} for name in active_names}, "modelProfiles": backtest.get("modelProfiles", []), "backtest": backtest, "availableModels": list(active_names), "unavailableModels": unavailable, "artifactManifest": artifacts.get("manifest", {}), "appIntegration": {"enabled": False, "reason": "App 分數只允許進入 Fantasy 5；目前沒有外部 App 快照。" if game == "ca-fantasy5" else "539 production path 不讀取 App 分數。"}, "note": "v2 目標是提高候選15碼覆蓋率；分數是相對排序，不是真實中獎機率。"}


def analyze_tw539(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return analyze("tw539", rows)


def analyze_ca_fantasy5(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return analyze("ca-fantasy5", rows)


def source_hash(game: str, rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(sorted(rows, key=lambda row: (row["date"], str(row["period"]))), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
