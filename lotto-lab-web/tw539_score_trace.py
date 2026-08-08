"""Observer-only TW539 score tracing. Never participates in scoring."""
from __future__ import annotations
import contextlib, contextvars, copy, os
from typing import Any

_ACTIVE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("tw539_score_trace", default=None)
_LATEST: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("tw539_score_trace_latest", default=None)

def enabled() -> bool:
    return os.environ.get("TW539_SCORE_TRACE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

def begin(game: str):
    if game != "tw539" or not enabled(): return None
    trace={"version":"tw539-score-trace-1.0.0","game":game,"numbers":{},"errors":[]}
    return _ACTIVE.set(trace)

def finish(token) -> None:
    if token is None: return
    trace=_ACTIVE.get()
    try: _LATEST.set(copy.deepcopy(trace))
    except Exception: pass
    finally: _ACTIVE.reset(token)

@contextlib.contextmanager
def capture(game: str):
    if game != "tw539" or not enabled():
        yield None; return
    trace: dict[str, Any] = {"version":"tw539-score-trace-1.0.0","game":game,"numbers":{},"errors":[]}
    token = _ACTIVE.set(trace)
    try:
        yield trace
    except Exception as exc:
        trace["errors"].append({"stage":"capture","type":type(exc).__name__})
    finally:
        _LATEST.set(copy.deepcopy(trace)); _ACTIVE.reset(token)

def _safe(stage: str, callback) -> None:
    trace = _ACTIVE.get()
    if trace is None: return
    try: callback(trace)
    except Exception as exc: trace["errors"].append({"stage":stage,"type":type(exc).__name__})

def record_features(number: int, normalized: dict[str,float], raw: dict[str,Any] | None=None) -> None:
    def write(trace):
        row=trace["numbers"].setdefault(str(number),{}); row["raw_features"]=copy.deepcopy(raw or {}); row["normalized_features"]=dict(normalized)
    _safe("features",write)

def record_model(number:int, model:str, *, feature_weights:dict[str,float], weighted_terms:dict[str,float], raw_score:float,
                 transformed_score:float, transform:dict[str,Any]|None=None, bias:float=0.0) -> None:
    def write(trace):
        row=trace["numbers"].setdefault(str(number),{}); row.setdefault("models",{})[model]={"feature_weights":dict(feature_weights),"weighted_terms":dict(weighted_terms),"bias":bias,"model_raw_score":raw_score,"model_transformed_score":transformed_score,"transform":copy.deepcopy(transform)}
    _safe("model",write)

def finalize(weights:dict[str,float], ensemble_scores:dict[int,float], ranked:list[int]) -> None:
    def write(trace):
        trace["model_weights"]=dict(weights); trace["active_feature_list"]=sorted({k for row in trace["numbers"].values() for k in row.get("normalized_features",{})})
        rank_by={number:rank for rank,number in enumerate(ranked,1)}; errors=[]
        for number,final in ensemble_scores.items():
            row=trace["numbers"].setdefault(str(number),{}); row["ensemble_score_raw"]=final; row["final_score"]=round(final*100,2); row["final_rank"]=rank_by[number]
            rebuilt=sum(weights.get(model,0.0)*detail["model_transformed_score"] for model,detail in row.get("models",{}).items())
            row["reconstructed_final_score"]=rebuilt; row["absolute_error"]=abs(rebuilt-final); errors.append(row["absolute_error"])
        trace["score_reconstruction"]={"max_error":max(errors,default=0.0),"mean_error":sum(errors)/max(1,len(errors))}
    _safe("finalize",write)

def latest_trace() -> dict[str,Any] | None:
    value=_LATEST.get(); return copy.deepcopy(value) if value is not None else None

def clear_latest() -> None: _LATEST.set(None)
