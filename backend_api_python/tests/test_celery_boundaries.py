"""Celery routing and durable task boundary tests."""

from __future__ import annotations


def test_celery_queues_keep_trading_outside_task_system():
    from app.celery_app import celery_app

    routes = celery_app.conf.task_routes
    assert routes["quantdinger.tasks.agent_job"]["queue"] == "jobs"
    assert routes["quantdinger.tasks.fast_analysis"]["queue"] == "ai"
    assert "strategy" not in " ".join(routes).lower()


def test_celery_beat_owns_periodic_maintenance():
    from app.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule
    assert schedule["reflection-cycle"]["task"] == "quantdinger.tasks.reflection"
    assert schedule["ai-calibration-cycle"]["task"] == "quantdinger.tasks.ai_calibration"
    assert schedule["market-catalog-sync"]["task"] == "quantdinger.tasks.market_catalog_sync"
    assert schedule["market-catalog-sync"]["schedule"] == 86400
    assert schedule["csi300-enhanced-daily-sync"]["task"] == "quantdinger.tasks.csi300_enhanced_daily_sync"
    assert schedule["csi300-enhanced-daily-sync"]["schedule"] == 86400


def test_fast_analysis_dispatches_to_celery(monkeypatch):
    from app.services import fast_analysis_tasks
    from app.tasks.fast_analysis import execute_fast_analysis

    calls = []
    monkeypatch.setenv("CELERY_TASKS_ENABLED", "true")
    monkeypatch.setattr(execute_fast_analysis, "delay", lambda *args, **kwargs: calls.append((args, kwargs)))

    fast_analysis_tasks.start_async_analysis_task(1, "Crypto", "BTC/USDT")

    assert calls == [((1, "Crypto", "BTC/USDT"), {})]


def test_csi300_enhanced_daily_sync_task_returns_zero_without_tushare(monkeypatch):
    from app.tasks.csi300_enhanced import run_csi300_enhanced_daily_sync_task

    monkeypatch.setenv("ENABLE_CSI300_ENHANCED_DAILY_SYNC", "true")
    monkeypatch.setattr(
        "app.services.csi300_enhanced.tushare_sync.is_tushare_configured",
        lambda: False,
    )
    result = run_csi300_enhanced_daily_sync_task.run(trade_date="20260731")
    assert result["trade_date"] == "20260731"
    assert result["index_weights"] == 0
    assert result["daily_basic"] == 0
