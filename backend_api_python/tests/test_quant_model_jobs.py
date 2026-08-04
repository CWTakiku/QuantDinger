"""Unit tests for the quant-model prepare+backtest worker.

The worker is exercised end-to-end with every external dependency
(``get_quant_model``, ``_prepare_run``, ``ensure_quant_model_scores``,
``StrategyV2BacktestService``) monkeypatched, so no DB / strategy runtime
is required.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.services.quant_models import jobs


@pytest.fixture(autouse=True)
def _isolated_jobs():
    jobs._reset_for_tests()
    yield
    jobs._reset_for_tests()


def _wait_done(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = jobs.get_job(job_id, user_id=1)
        if snap and snap.get("status") in {"succeeded", "failed"}:
            return snap
        time.sleep(0.01)
    return jobs.get_job(job_id, user_id=1) or {}


def _sample_model() -> dict:
    return {
        "model_key": "sess-1_loop0_model",
        "kind": "model",
        "alpha_source": "rdagent",
        "alpha_version": "qm_sess-1_loop0_model",
        "universe": "csi300",
        "provenance_json": {"session_id": "sess-1", "loop_index": 0, "mode": "model"},
    }


def _patch_prepare(monkeypatch, prepared: dict) -> None:
    """Patch the worker's late import of ``_prepare_run`` on the routes module.

    The fake prepare merges the worker's (already-overridden) ``params`` into
    the canned prepared dict so the test can assert on source/version propagation.
    """
    import app.routes.backtest_center as bc

    def fake_prepare(payload, user_id):
        out = dict(prepared)
        out["user_id"] = user_id
        out["params"] = dict(payload.get("params") or prepared.get("params") or {})
        return out

    monkeypatch.setattr(bc, "_prepare_run", fake_prepare)


def test_worker_happy_path(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "app.services.quant_models.store.get_quant_model",
        lambda key: _sample_model() if key == "sess-1_loop0_model" else None,
    )

    prepared = {
        "code": "run_weekly(rebalance, weekday=1)",
        "start_date": __import__("datetime").date(2026, 1, 5),
        "end_date": __import__("datetime").date(2026, 1, 25),
        "params": {"score_lag_days": 1},
        "initial_capital": 10_000.0,
        "leverage_enabled": False,
        "leverage": 1.0,
        "commission": 0.0005,
        "slippage": 0.0005,
        "strategy_id": None,
        "source_id": None,
        "strategy_name": "",
    }
    _patch_prepare(monkeypatch, prepared)

    ensure_calls = []

    def fake_ensure(model, as_ofs, *, on_progress=None):
        ensure_calls.append((model["model_key"], list(as_ofs)))
        if on_progress:
            on_progress({"phase": "inferring_scores", "count": 1})
        return {"missing_before": [], "inferred": 0, "still_missing": []}

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.ensure_quant_model_scores",
        fake_ensure,
    )

    run_calls = []

    class FakeService:
        def run(self, **kwargs):
            run_calls.append(kwargs)
            return 42, {"metrics": {"sharpe": 1.3}, "totalReturn": 0.12}

    monkeypatch.setattr(
        "app.services.strategy_v2.StrategyV2BacktestService",
        lambda: FakeService(),
    )

    snapshot = jobs.submit_prepare_and_backtest(
        payload={
            "model_key": "sess-1_loop0_model",
            "sourceId": 5,
            "startDate": "2026-01-05",
            "endDate": "2026-01-25",
            "params": {"score_lag_days": 1},
        },
        user_id=1,
    )
    job_id = snapshot["job_id"]
    final = _wait_done(job_id)

    assert final["status"] == "succeeded"
    assert final["result"]["run_id"] == 42
    assert final["result"]["metrics"]["sharpe"] == 1.3

    # The model's alpha_source / alpha_version must override params.
    assert ensure_calls
    model_key, as_ofs = ensure_calls[0]
    assert model_key == "sess-1_loop0_model"
    # Weekly Mondays in [2026-01-05, 2026-01-25] → 01-05, 01-12, 01-19.
    # score_lag_days=1 → as_ofs are one day earlier.
    assert [d.isoformat() for d in as_ofs] == [
        "2026-01-04", "2026-01-11", "2026-01-18",
    ]

    assert run_calls
    run_kwargs = run_calls[0]
    assert run_kwargs["params"]["source"] == "rdagent"
    assert run_kwargs["params"]["version"] == "qm_sess-1_loop0_model"
    assert run_kwargs["persist"] is True


def test_worker_missing_model_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.quant_models.store.get_quant_model",
        lambda key: None,
    )

    snapshot = jobs.submit_prepare_and_backtest(
        payload={"model_key": "missing", "code": "x", "startDate": "2026-01-05", "endDate": "2026-01-25"},
        user_id=1,
    )
    final = _wait_done(snapshot["job_id"])
    assert final["status"] == "failed"
    assert "not found" in final["error"]


def test_worker_still_missing_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.quant_models.store.get_quant_model",
        lambda key: _sample_model(),
    )

    prepared = {
        "code": "run_weekly(rebalance)",
        "start_date": __import__("datetime").date(2026, 1, 5),
        "end_date": __import__("datetime").date(2026, 1, 25),
        "params": {},
        "initial_capital": 10_000.0,
    }
    _patch_prepare(monkeypatch, prepared)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.ensure_quant_model_scores",
        lambda model, as_ofs, *, on_progress=None: {
            "missing_before": ["2026-01-04"],
            "inferred": 0,
            "still_missing": ["2026-01-04"],
        },
    )

    backtest_called = []
    monkeypatch.setattr(
        "app.services.strategy_v2.StrategyV2BacktestService",
        lambda: type("S", (), {"run": lambda self, **kw: backtest_called.append(kw) or (1, {})}),
    )

    snapshot = jobs.submit_prepare_and_backtest(
        payload={"model_key": "sess-1_loop0_model", "code": "x", "startDate": "2026-01-05", "endDate": "2026-01-25"},
        user_id=1,
    )
    final = _wait_done(snapshot["job_id"])
    assert final["status"] == "failed"
    assert "still missing" in final["error"]
    assert backtest_called == []


def test_worker_no_rebalance_dates_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.quant_models.store.get_quant_model",
        lambda key: _sample_model(),
    )

    # start after end → empty rebalance calendar.
    prepared = {
        "code": "run_weekly(rebalance)",
        "start_date": __import__("datetime").date(2026, 2, 1),
        "end_date": __import__("datetime").date(2026, 1, 1),
        "params": {},
        "initial_capital": 10_000.0,
    }
    _patch_prepare(monkeypatch, prepared)

    ensure_called = []
    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.ensure_quant_model_scores",
        lambda model, as_ofs, *, on_progress=None: ensure_called.append(as_ofs) or {
            "still_missing": [],
        },
    )

    snapshot = jobs.submit_prepare_and_backtest(
        payload={"model_key": "sess-1_loop0_model", "code": "x", "startDate": "2026-02-01", "endDate": "2026-01-01"},
        user_id=1,
    )
    final = _wait_done(snapshot["job_id"])
    assert final["status"] == "failed"
    assert "rebalance" in final["error"]
    assert ensure_called == []


def test_worker_missing_user_id_fails(monkeypatch):
    """Defensive guard: if __user_id is stripped from the stored request, the
    worker must fail fast instead of running as user 0."""
    snapshot = jobs.create_job(
        payload={"model_key": "sess-1_loop0_model"},
        user_id=1,
    )
    # Bypass submit_prepare_and_backtest to simulate a corrupted request.
    jobs._jobs[snapshot["job_id"]]["request"] = {"model_key": "sess-1_loop0_model"}
    jobs.start_job(snapshot["job_id"], jobs.run_prepare_and_backtest)
    final = _wait_done(snapshot["job_id"])
    assert final["status"] == "failed"
    assert "user_id" in final["error"]


def test_worker_records_progress_phases(monkeypatch):
    monkeypatch.setattr(
        "app.services.quant_models.store.get_quant_model",
        lambda key: _sample_model(),
    )
    prepared = {
        "code": "run_weekly(rebalance)",
        "start_date": __import__("datetime").date(2026, 1, 5),
        "end_date": __import__("datetime").date(2026, 1, 25),
        "params": {},
        "initial_capital": 10_000.0,
    }
    _patch_prepare(monkeypatch, prepared)

    monkeypatch.setattr(
        "app.services.quant_models.ensure_scores.ensure_quant_model_scores",
        lambda model, as_ofs, *, on_progress=None: {"still_missing": []},
    )

    class FakeService:
        def run(self, **kwargs):
            return 1, {"metrics": {}}

    monkeypatch.setattr(
        "app.services.strategy_v2.StrategyV2BacktestService",
        lambda: FakeService(),
    )

    snapshot = jobs.submit_prepare_and_backtest(
        payload={"model_key": "sess-1_loop0_model", "code": "x", "startDate": "2026-01-05", "endDate": "2026-01-25"},
        user_id=1,
    )
    final = _wait_done(snapshot["job_id"])
    assert final["status"] == "succeeded"
    # The final phase recorded on the snapshot should reflect the last update.
    assert final["phase"] == "succeeded"
