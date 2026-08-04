"""Unit tests for ``app.services.quant_models.live_hook``.

The hook is exercised with ``ensure_quant_model_scores`` and the store
accessors monkeypatched, so no DB / bridge inference is required.

Coverage:
* success (model bound, ensure ok) → True
* failure (still_missing) → False
* failure (ensure raises) → False
* broken binding (model_key set but model gone) → False
* no model binding (no model_key, no source/version) → True
* params under trading_config (strategy row shape) and direct params
* score_lag_days honored; invalid trade_date is tolerated
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from app.services.quant_models import live_hook


def _model() -> dict:
    return {
        "model_key": "sess-1_loop0_model",
        "kind": "model",
        "alpha_source": "rdagent",
        "alpha_version": "qm_sess-1_loop0_model",
        "universe": "csi300",
        "provenance_json": {"session_id": "sess-1", "loop_index": 0, "mode": "model"},
    }


def _patch_get_model(monkeypatch, model_or_none):
    monkeypatch.setattr(
        "app.services.quant_models.live_hook.get_quant_model",
        lambda key: model_or_none,
    )


def _patch_list_models(monkeypatch, models):
    monkeypatch.setattr(
        "app.services.quant_models.live_hook.list_quant_models",
        lambda *, status="published": list(models),
    )


def _patch_ensure(monkeypatch, result_or_exc):
    calls = []

    def fake(model, as_ofs, *, on_progress=None):
        calls.append((model["model_key"], list(as_ofs)))
        if isinstance(result_or_exc, Exception):
            raise result_or_exc
        return result_or_exc

    monkeypatch.setattr(
        "app.services.quant_models.live_hook.ensure_quant_model_scores",
        fake,
    )
    return calls


def test_success_returns_true(monkeypatch):
    _patch_get_model(monkeypatch, _model())
    calls = _patch_ensure(monkeypatch, {"still_missing": [], "inferred": 0, "missing_before": []})

    strategy = {"trading_config": {"params": {"model_key": "sess-1_loop0_model", "score_lag_days": 1}}}
    ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is True
    assert calls == [("sess-1_loop0_model", [date(2026, 1, 11)])]


def test_still_missing_returns_false(monkeypatch, caplog):
    _patch_get_model(monkeypatch, _model())
    _patch_ensure(monkeypatch, {"still_missing": ["2026-01-11"], "inferred": 0, "missing_before": ["2026-01-11"]})

    strategy = {"trading_config": {"params": {"model_key": "sess-1_loop0_model"}}}
    with caplog.at_level(logging.ERROR, logger="app.services.quant_models.live_hook"):
        ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is False
    assert any("still missing" in rec.message for rec in caplog.records)


def test_ensure_raises_returns_false(monkeypatch, caplog):
    _patch_get_model(monkeypatch, _model())
    _patch_ensure(monkeypatch, RuntimeError("bridge offline"))

    strategy = {"trading_config": {"params": {"model_key": "sess-1_loop0_model"}}}
    with caplog.at_level(logging.ERROR, logger="app.services.quant_models.live_hook"):
        ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is False
    assert any("bridge offline" in rec.message for rec in caplog.records)


def test_broken_binding_returns_false(monkeypatch, caplog):
    # model_key configured but model not found → safe-skip.
    _patch_get_model(monkeypatch, None)
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": {"params": {"model_key": "ghost"}}}
    with caplog.at_level(logging.ERROR, logger="app.services.quant_models.live_hook"):
        ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is False
    assert any("bound but not found" in rec.message for rec in caplog.records)
    # Ensure must not be called when the model can't be resolved.
    assert calls == []


def test_no_model_binding_returns_true(monkeypatch):
    _patch_get_model(monkeypatch, None)
    _patch_list_models(monkeypatch, [])
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": {"params": {"top_n": 30}}}
    ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is True
    assert calls == []


def test_source_version_fallback_resolves_model(monkeypatch):
    _patch_get_model(monkeypatch, None)
    _patch_list_models(monkeypatch, [_model()])
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": {"params": {"source": "rdagent", "version": "qm_sess-1_loop0_model"}}}
    ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is True
    assert calls == [("sess-1_loop0_model", [date(2026, 1, 11)])]


def test_source_version_fallback_no_match_returns_true(monkeypatch):
    _patch_get_model(monkeypatch, None)
    _patch_list_models(monkeypatch, [])
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": {"params": {"source": "rdagent", "version": "qm_other"}}}
    ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is True
    assert calls == []


def test_direct_params_dict_supported(monkeypatch):
    """Callers may pass a curated dict with ``params`` at the top level."""
    _patch_get_model(monkeypatch, _model())
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"params": {"model_key": "sess-1_loop0_model", "score_lag_days": 0}}
    ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is True
    # score_lag_days=0 → as_of == trade_date
    assert calls == [("sess-1_loop0_model", [date(2026, 1, 12)])]


def test_score_lag_days_default_is_one(monkeypatch):
    _patch_get_model(monkeypatch, _model())
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": {"params": {"model_key": "sess-1_loop0_model"}}}
    live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert calls[0][1] == [date(2026, 1, 11)]


def test_score_lag_days_invalid_falls_back_to_default(monkeypatch):
    _patch_get_model(monkeypatch, _model())
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": {"params": {"model_key": "sess-1_loop0_model", "score_lag_days": "oops"}}}
    live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    # Invalid → default 1 day lag.
    assert calls[0][1] == [date(2026, 1, 11)]


def test_trading_config_as_json_string(monkeypatch):
    """The executor pre-parses trading_config, but the hook must tolerate the
    raw JSON-string shape too (defensive)."""
    import json as _json

    _patch_get_model(monkeypatch, _model())
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": _json.dumps({"params": {"model_key": "sess-1_loop0_model", "score_lag_days": 2}})}
    ok = live_hook.ensure_before_rebalance(strategy, date(2026, 1, 12))

    assert ok is True
    assert calls == [("sess-1_loop0_model", [date(2026, 1, 10)])]


def test_invalid_trade_date_returns_true(monkeypatch):
    """A garbage trade_date must not crash the hook; we allow the rebalance
    rather than block on a caller bug."""
    _patch_get_model(monkeypatch, _model())
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    strategy = {"trading_config": {"params": {"model_key": "sess-1_loop0_model"}}}
    ok = live_hook.ensure_before_rebalance(strategy, "not-a-date")

    assert ok is True
    assert calls == []


def test_empty_strategy_row_returns_true(monkeypatch):
    _patch_get_model(monkeypatch, None)
    _patch_list_models(monkeypatch, [])
    calls = _patch_ensure(monkeypatch, {"still_missing": []})

    assert live_hook.ensure_before_rebalance({}, date(2026, 1, 12)) is True
    assert live_hook.ensure_before_rebalance(None, date(2026, 1, 12)) is True
    assert calls == []
