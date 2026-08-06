"""Side-effect-free TW539 Shadow runners.

Imported lazily only when SHADOW_CANDIDATE_A_ENABLED is true.  The runners
reuse the Current response's exact history snapshot and never touch queues,
production journals, warm cache, or API payloads.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


BASELINE_VERSION = "tw539-uniform-fixed-tiebreak-v1"
BASELINE_DEFINITION = {
    "name": "uniform",
    "score": "1/39",
    "ranking": "descending score, ascending lottery number tie-break",
    "features": [],
    "weights": {},
    "supervised_model": False,
}
BASELINE_CONFIG_SHA256 = hashlib.sha256(
    json.dumps(BASELINE_DEFINITION, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ShadowAnalysisContext:
    lottery: str
    draw_id: str
    source_data_snapshot: tuple[Mapping[str, Any], ...]
    dataset_sha256: str
    dataset_version: str
    analysis_timestamp: str
    runtime_version: str


@dataclass(frozen=True)
class ShadowPredictionResult:
    lottery: str
    draw_id: str
    version: str
    config_sha256: str
    prediction: tuple[int, ...]
    top5: tuple[int, ...]
    top10: tuple[int, ...]
    top15: tuple[int, ...]
    scores: Mapping[int, float]
    started_at: str
    completed_at: str
    latency_ms: float
    status: str = "completed"
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "lottery": self.lottery, "draw_id": self.draw_id, "version": self.version,
            "config_sha256": self.config_sha256, "prediction": list(self.prediction),
            "top5": list(self.top5), "top10": list(self.top10), "top15": list(self.top15),
            "scores": {str(key): score for key, score in self.scores.items()},
            "started_at": self.started_at, "completed_at": self.completed_at,
            "latency_ms": self.latency_ms, "status": self.status,
            "error_type": self.error_type, "error_message": self.error_message,
        }


def build_analysis_context(game: str, current_result: dict[str, Any]) -> ShadowAnalysisContext:
    latest = current_result.get("latest") or {}
    draw_id = str(latest.get("period") or latest.get("drawId") or "")
    if not draw_id:
        raise ValueError("Shadow draw_id is missing")
    rows = current_result.get("history") or []
    stable = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    snapshot = tuple(_freeze(dict(row)) for row in rows)
    analysis = current_result.get("analysis") or {}
    return ShadowAnalysisContext(
        lottery=game,
        draw_id=draw_id,
        source_data_snapshot=snapshot,
        dataset_sha256=hashlib.sha256(stable).hexdigest(),
        dataset_version=str(analysis.get("modelVersion") or "current-snapshot"),
        analysis_timestamp=_now(),
        runtime_version="shadow-runtime-v1",
    )


def _mutable_rows(context: ShadowAnalysisContext) -> list[dict[str, Any]]:
    return [{key: list(value) if isinstance(value, tuple) else value for key, value in row.items()} for row in context.source_data_snapshot]


def run_candidate_a(context: ShadowAnalysisContext, current_result: dict[str, Any], candidate_config: Mapping[str, Any]) -> ShadowPredictionResult:
    import server  # delayed: default-off never imports this runtime module

    started_at, started = _now(), time.perf_counter()
    if context.lottery != "tw539":
        raise ValueError("Frozen Candidate A supports TW539 only")
    rows = server._mm_rows(_mutable_rows(context), max_number=39, limit=5000)
    if not rows:
        raise ValueError("Candidate A source snapshot is empty")
    stats = server._mm_stats(rows, 39)
    names, original = server._mm_feature_rows(stats, 39)
    logistic = server._mm_fit_logistic(original, names, stats)
    removed = tuple(candidate_config["removed_features"])
    features = {number: dict(values) for number, values in original.items()}
    for values in features.values():
        for name in removed:
            if name in values:
                values[name] = 0.5
    models = {name: {} for name in server.FORMAL_MODEL_NAMES["tw539"]}
    for number, row in features.items():
        window = .34*row["recent30"]+.26*row["recent100"]+.18*row["recent300"]+.13*row["recent1000"]+.09*row["recent5000"]
        models["tw-bayesian"][number] = server._mm_clamp(window*.65+row["returnRate"]*.12+row["omissionFit"]*.12+row["tailBalance"]*.11)
        linear = logistic["intercept"] + sum(logistic[name]*row[name] for name in names)
        models["tw-logistic"][number] = server._mm_clamp(1/(1+math.exp(-max(-12, min(12, linear)))))
        models["tw-boosted"][number] = server._mm_clamp(.42*window+.18*row["cooccurrence"]+.15*row["zoneBalance"]+.13*row["tailBalance"]+.12*row["omissionFit"])
        transition = .35*row["neighborSignal"]+.25*row["returnRate"]+.2*row["repeatRate"]+.2*row["previousRepeat"]
        models["tw-markov"][number] = server._mm_clamp(transition*.68+window*.32)
    current_weights = (current_result.get("analysis") or {}).get("modelWeights") or server._formal_default_weights("tw539")
    weights = {name: float(current_weights.get(name, server._formal_default_weights("tw539")[name])) for name in models}
    scores = {number: sum(weights[name]*models[name][number] for name in models) for number in range(1, 40)}
    ranking = server._mm_select_pool(scores, stats, 39, 15)
    completed_at = _now()
    return ShadowPredictionResult(context.lottery, context.draw_id, candidate_config["version"], candidate_config["definition_sha256"], tuple(ranking), tuple(ranking[:5]), tuple(ranking[:10]), tuple(ranking[:15]), MappingProxyType(scores), started_at, completed_at, round((time.perf_counter()-started)*1000, 3))


def run_baseline(context: ShadowAnalysisContext, current_result: dict[str, Any]) -> ShadowPredictionResult:
    del current_result
    started_at, started = _now(), time.perf_counter()
    if context.lottery != "tw539":
        raise ValueError("Frozen baseline supports TW539 only")
    scores = {number: 1/39 for number in range(1, 40)}
    ranking = sorted(scores, key=lambda number: (-scores[number], number))
    completed_at = _now()
    return ShadowPredictionResult(context.lottery, context.draw_id, BASELINE_VERSION, BASELINE_CONFIG_SHA256, tuple(ranking), tuple(ranking[:5]), tuple(ranking[:10]), tuple(ranking[:15]), MappingProxyType(scores), started_at, completed_at, round((time.perf_counter()-started)*1000, 3))


def get_shadow_runners(game: str, enabled: bool):
    if not enabled or game != "tw539":
        return ()
    return (run_candidate_a, run_baseline)
