"""In-process async job registry for quant-model-driven backtests.

MVP backend: thread-pool + in-memory dict. We deliberately avoid reusing
``app.utils.agent_jobs.submit_job`` here because that path is built around an
Agent token (``agent_token_id``, per-token concurrency caps, idempotency
keys read from request headers). The human Vue backtest page does not carry
an Agent token, so wiring Celery+agent_jobs for an admin backtest would force
us to either mint a synthetic token or refactor the tenant model. Both are
out of scope for the MVP.

Trade-offs:
  * Pro: zero new infra, no DB migration, no Agent token required, survives
    single-process restarts within the request lifecycle.
  * Con: jobs are lost on process restart (no durability) and bounded by a
    single process. Acceptable for MVP — the Vue admin backtest is a
    single-user, low-frequency flow.

When this stops being enough, swap ``start_job`` to dispatch a Celery task
and persist snapshots in ``qd_agent_jobs`` (or a new table). The public
snapshot shape returned to clients is intentionally compatible with that
future migration.
"""
from __future__ import annotations

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

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


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


def create_job(*, payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Persist a new job row in memory and return its public snapshot."""
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
    with _jobs_lock:
        _jobs[job_id] = snapshot
    return _public_snapshot(snapshot)


def get_job(job_id: str, *, user_id: int) -> Optional[dict[str, Any]]:
    """Tenant-scoped job lookup. Returns ``None`` if missing or not owned."""
    with _jobs_lock:
        snapshot = _jobs.get(job_id)
    if snapshot is None:
        return None
    if int(snapshot.get("user_id") or 0) != int(user_id):
        return None
    return _public_snapshot(snapshot)


def list_jobs(*, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    with _jobs_lock:
        rows = [
            _public_snapshot(s)
            for s in _jobs.values()
            if int(s.get("user_id") or 0) == int(user_id)
        ]
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[:limit]


def _update_job(job_id: str, **changes: Any) -> None:
    with _jobs_lock:
        snapshot = _jobs.get(job_id)
        if snapshot is None:
            return
        snapshot.update(changes)


def set_progress(job_id: str, event: dict[str, Any]) -> None:
    """Publish a progress event into the job snapshot (in-memory only)."""
    if not isinstance(event, dict):
        event = {"value": event}
    with _jobs_lock:
        snapshot = _jobs.get(job_id)
        if snapshot is None:
            return
        snapshot["progress"] = dict(event)
        if "phase" in event:
            snapshot["phase"] = event["phase"]


def mark_succeeded(job_id: str, result: Any) -> None:
    _update_job(
        job_id,
        status="succeeded",
        phase="succeeded",
        result=result,
        finished_at=_now_iso(),
    )


def mark_failed(job_id: str, error: str) -> None:
    _update_job(
        job_id,
        status="failed",
        phase="failed",
        error=str(error)[:6000],
        finished_at=_now_iso(),
    )


def start_job(job_id: str, runner: Callable[[str, dict[str, Any]], None]) -> None:
    """Dispatch ``runner(job_id, payload)`` on the thread pool.

    The runner is responsible for calling ``set_progress`` / ``mark_succeeded``
    / ``mark_failed``. If the runner raises, the job is marked failed here.
    """
    with _jobs_lock:
        snapshot = _jobs.get(job_id)
        if snapshot is None:
            logger.warning("start_job: unknown job_id=%s", job_id)
            return
        payload = dict(snapshot.get("request") or {})

    def _run() -> None:
        _update_job(
            job_id,
            status="running",
            phase="preparing",
            started_at=_now_iso(),
        )
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
    global _executor
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
