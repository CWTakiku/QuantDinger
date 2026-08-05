"""Async job registry for quant-model-driven backtests.

Job *state* is stored in Redis so Gunicorn multi-worker setups can poll a job
created by another worker. Execution still runs in a process-local thread pool
on the worker that accepted ``POST /run-with-model``.

Falls back to an in-memory dict when Redis is unavailable or
``QUANT_MODEL_JOBS_STORE=memory`` (used by unit tests).
"""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


_JOB_TTL_SECONDS = 24 * 3600
_REDIS_KEY_PREFIX = "quantdinger:quant-model-job:v1:"
_REDIS_USER_INDEX_PREFIX = "quantdinger:quant-model-jobs:user:v1:"

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_redis_client = None
_redis_lock = threading.Lock()
_redis_warned = False
_force_memory = False


def _max_workers() -> int:
    try:
        return max(1, int(os.getenv("QUANT_MODEL_JOBS_MAX_WORKERS", "2")))
    except Exception:
        return 2


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is not None:
        return _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=_max_workers(),
                thread_name_prefix="quant-model-job",
            )
    return _executor


def _new_job_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _store_mode() -> str:
    if _force_memory:
        return "memory"
    raw = (os.getenv("QUANT_MODEL_JOBS_STORE") or "").strip().lower()
    if raw in {"memory", "mem", "local"}:
        return "memory"
    if raw in {"redis"}:
        return "redis"
    return "auto"


def _get_redis():
    """Return a decode_responses Redis client, or None."""
    global _redis_client, _redis_warned
    mode = _store_mode()
    if mode == "memory":
        return None
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            import redis
            from app.config.redis_urls import cache_redis_url

            client = redis.Redis.from_url(
                cache_redis_url(),
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            _redis_client = client
            logger.info("quant_model_jobs: using Redis store")
            return _redis_client
        except Exception as exc:
            if mode == "redis":
                raise
            if not _redis_warned:
                logger.warning(
                    "quant_model_jobs: Redis unavailable, falling back to "
                    "process-local memory (GUNICORN_WORKERS>1 will break polling): %s",
                    exc,
                )
                _redis_warned = True
            return None


def _job_key(job_id: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{job_id}"


def _user_index_key(user_id: int) -> str:
    return f"{_REDIS_USER_INDEX_PREFIX}{int(user_id)}"


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project the internal job state to a client-safe view.

    The raw request payload (which contains strategy code) is intentionally
    withheld from status polls.
    """
    return {
        "job_id": snapshot.get("job_id"),
        "status": snapshot.get("status"),
        "phase": snapshot.get("phase"),
        "progress": snapshot.get("progress"),
        "result": snapshot.get("result"),
        "error": snapshot.get("error"),
        "created_at": snapshot.get("created_at"),
        "started_at": snapshot.get("started_at"),
        "finished_at": snapshot.get("finished_at"),
    }


def _persist_snapshot(snapshot: dict[str, Any]) -> None:
    job_id = str(snapshot.get("job_id") or "")
    if not job_id:
        return
    client = _get_redis()
    if client is None:
        with _jobs_lock:
            _jobs[job_id] = snapshot
        return
    payload = json.dumps(snapshot, ensure_ascii=False, default=str)
    pipe = client.pipeline()
    pipe.setex(_job_key(job_id), _JOB_TTL_SECONDS, payload)
    user_id = int(snapshot.get("user_id") or 0)
    if user_id:
        try:
            score = datetime.fromisoformat(
                str(snapshot.get("created_at") or _now_iso()).rstrip("Z")
            ).timestamp()
        except Exception:
            score = time.time()
        pipe.zadd(_user_index_key(user_id), {job_id: score})
        pipe.expire(_user_index_key(user_id), _JOB_TTL_SECONDS)
    pipe.execute()


def _load_snapshot(job_id: str) -> Optional[dict[str, Any]]:
    key = str(job_id or "").strip()
    if not key:
        return None
    client = _get_redis()
    if client is None:
        with _jobs_lock:
            snap = _jobs.get(key)
            return dict(snap) if snap else None
    raw = client.get(_job_key(key))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def create_job(*, payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Persist a new job and return its public snapshot."""
    job_id = _new_job_id()
    snapshot: dict[str, Any] = {
        "job_id": job_id,
        "user_id": int(user_id),
        "status": "queued",
        "phase": "queued",
        "progress": None,
        "result": None,
        "error": None,
        "created_at": _now_iso(),
        "started_at": None,
        "finished_at": None,
        "request": dict(payload),
    }
    _persist_snapshot(snapshot)
    return _public_snapshot(snapshot)


def get_job(job_id: str, *, user_id: int) -> Optional[dict[str, Any]]:
    """Tenant-scoped job lookup. Returns ``None`` if missing or not owned."""
    snapshot = _load_snapshot(job_id)
    if snapshot is None:
        return None
    if int(snapshot.get("user_id") or 0) != int(user_id):
        return None
    return _public_snapshot(snapshot)


def list_jobs(*, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    uid = int(user_id)
    client = _get_redis()
    if client is None:
        with _jobs_lock:
            rows = [
                _public_snapshot(s)
                for s in _jobs.values()
                if int(s.get("user_id") or 0) == uid
            ]
        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows[:limit]

    ids = client.zrevrange(_user_index_key(uid), 0, limit - 1) or []
    rows: list[dict[str, Any]] = []
    for job_id in ids:
        snap = _load_snapshot(str(job_id))
        if snap and int(snap.get("user_id") or 0) == uid:
            rows.append(_public_snapshot(snap))
    return rows


def _update_job(job_id: str, **changes: Any) -> None:
    snapshot = _load_snapshot(job_id)
    if snapshot is None:
        return
    snapshot.update(changes)
    _persist_snapshot(snapshot)


def set_progress(job_id: str, event: dict[str, Any]) -> None:
    """Publish a progress event into the job snapshot."""
    if not isinstance(event, dict):
        event = {"value": event}
    snapshot = _load_snapshot(job_id)
    if snapshot is None:
        return
    snapshot["progress"] = dict(event)
    if "phase" in event:
        snapshot["phase"] = event["phase"]
    _persist_snapshot(snapshot)


def mark_succeeded(job_id: str, result: Any) -> None:
    _update_job(
        job_id,
        status="succeeded",
        phase="succeeded",
        result=result,
        error=None,
        finished_at=_now_iso(),
    )


def mark_failed(job_id: str, error: str) -> None:
    _update_job(
        job_id,
        status="failed",
        phase="failed",
        error=str(error or "unknown error"),
        finished_at=_now_iso(),
    )


def start_job(job_id: str, runner: Callable[[str, dict[str, Any]], None]) -> None:
    """Mark the job running and dispatch ``runner(job_id, request)``."""
    snapshot = _load_snapshot(job_id)
    if snapshot is None:
        raise ValueError(f"unknown job: {job_id}")
    payload = dict(snapshot.get("request") or {})
    _update_job(job_id, status="running", phase="preparing", started_at=_now_iso())

    def _run() -> None:
        try:
            runner(job_id, payload)
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error("quant_model_job %s failed: %s\n%s", job_id, exc, tb)
            mark_failed(job_id, f"{exc}")

    _get_executor().submit(_run)


def submit_prepare_and_backtest(
    *, payload: dict[str, Any], user_id: int
) -> dict[str, Any]:
    """Create + start a quant-model backtest job. Returns the public snapshot.

    Injects ``__user_id`` into the stored request so the worker can re-hydrate
    the caller identity without a separate channel.
    """
    enriched = dict(payload)
    enriched["__user_id"] = int(user_id)
    snapshot = create_job(payload=enriched, user_id=user_id)
    start_job(snapshot["job_id"], run_prepare_and_backtest)
    return snapshot


def prune_old_jobs() -> int:
    """Drop finished jobs older than ``_JOB_TTL_SECONDS``. Returns removed count."""
    cutoff = time.time() - _JOB_TTL_SECONDS
    removed = 0
    client = _get_redis()
    if client is not None:
        # TTL on keys handles expiry; only clean stale user-index members.
        return 0
    with _jobs_lock:
        for job_id in list(_jobs.keys()):
            snapshot = _jobs[job_id]
            if snapshot.get("status") not in {"succeeded", "failed", "cancelled"}:
                continue
            finished = snapshot.get("finished_at")
            if not finished:
                continue
            try:
                ft = datetime.fromisoformat(finished.rstrip("Z")).timestamp()
            except Exception:
                continue
            if ft < cutoff:
                _jobs.pop(job_id, None)
                removed += 1
    return removed


def _reset_for_tests() -> None:
    """Test-only: clear all in-memory state and shut down the executor."""
    global _executor, _force_memory, _redis_client, _redis_warned
    _force_memory = True
    _redis_client = None
    _redis_warned = False
    with _executor_lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
            _executor = None
    with _jobs_lock:
        _jobs.clear()


def _to_calendar_date(value: Any) -> Any:
    """Coerce a datetime/date-like value to a calendar ``date``."""
    from datetime import date

    if isinstance(value, date):
        return value if not hasattr(value, "date") else value.date()
    return value


def run_prepare_and_backtest(job_id: str, payload: dict[str, Any]) -> None:
    """Worker: model → schedule → ensure scores → StrategyV2 backtest.

    Reports progress via ``set_progress`` and writes the final result via
    ``mark_succeeded`` / ``mark_failed``. Imports are local so unit tests can
    monkeypatch each dependency without importing the whole strategy stack.
    """
    from app.routes.backtest_center import _prepare_run
    from app.services.quant_models.ensure_scores import ensure_quant_model_scores
    from app.services.quant_models.schedule import (
        expand_rebalance_dates,
        infer_schedule_from_strategy,
        to_score_as_ofs,
    )
    from app.services.quant_models.store import get_quant_model
    from app.services.strategy_v2 import StrategyV2BacktestService

    user_id = int(payload.get("__user_id") or 0)
    if not user_id:
        mark_failed(job_id, "missing user_id")
        return

    model_key = str(payload.get("model_key") or "").strip()
    if not model_key:
        mark_failed(job_id, "model_key is required")
        return

    set_progress(job_id, {
        "phase": "preparing",
        "percent": 5,
        "message": "loading model",
    })
    model = get_quant_model(model_key)
    if not model:
        mark_failed(job_id, f"quant model not found: {model_key}")
        return

    payload = dict(payload)
    params = dict(payload.get("params") or {})
    params["source"] = str(model.get("alpha_source") or "rdagent")
    params["version"] = str(model.get("alpha_version") or "")
    payload["params"] = params

    set_progress(job_id, {
        "phase": "preparing",
        "percent": 15,
        "message": "preparing backtest",
    })
    prepared = _prepare_run(payload, user_id)
    code = str(prepared.get("code") or "")

    set_progress(job_id, {
        "phase": "preparing",
        "percent": 25,
        "message": "inferring schedule",
    })
    schedule = infer_schedule_from_strategy(code, prepared.get("params") or {})
    score_lag_days = int(params.get("score_lag_days") or 1)
    start_d = _to_calendar_date(prepared.get("start_date"))
    end_d = _to_calendar_date(prepared.get("end_date"))
    rebalance_dates = expand_rebalance_dates(schedule, start_d, end_d)
    if not rebalance_dates:
        mark_failed(job_id, "no rebalance dates in range")
        return
    as_ofs = to_score_as_ofs(rebalance_dates, score_lag_days)

    set_progress(job_id, {
        "phase": "inferring_scores",
        "percent": 35,
        "message": "ensuring score panels",
        "as_ofs": [getattr(d, "isoformat", lambda: str(d))() for d in as_ofs],
    })

    def _on_ensure_progress(event: dict[str, Any]) -> None:
        merged = {
            "phase": "inferring_scores",
            "percent": 35,
        }
        merged.update(event if isinstance(event, dict) else {})
        set_progress(job_id, merged)

    ensure_result = ensure_quant_model_scores(
        model,
        as_ofs,
        on_progress=_on_ensure_progress,
    )
    still_missing = list(ensure_result.get("still_missing") or [])
    if still_missing:
        mark_failed(
            job_id,
            f"scores still missing for {len(still_missing)} dates: {still_missing[:5]}",
        )
        return

    from app.services.external_alpha.coverage import assert_external_alpha_score_coverage

    try:
        assert_external_alpha_score_coverage(
            code=code,
            params=prepared.get("params") or {},
            start_date=start_d,
            end_date=end_d,
        )
    except ValueError as exc:
        mark_failed(job_id, str(exc))
        return

    set_progress(job_id, {
        "phase": "backtesting",
        "percent": 60,
        "message": "running backtest",
    })
    service = StrategyV2BacktestService()
    run_id, result = service.run(
        **prepared,
        persist=bool(payload.get("persist", True)),
    )

    set_progress(job_id, {
        "phase": "finalizing",
        "percent": 95,
        "message": "finalizing",
    })
    summary = {
        "run_id": run_id,
        "metrics": result.get("metrics") if isinstance(result, dict) else None,
        "result": result,
    }
    mark_succeeded(job_id, summary)
