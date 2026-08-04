"""Live/paper rebalance pre-hook: ensure quant model scores exist before orders.

Before a scheduled rebalance callback runs, if the strategy is bound to a
quant model (``params.model_key`` or resolvable ``source``+``version``),
call ``ensure_quant_model_scores`` for ``as_of = trade_date - score_lag_days``.

Return value contract for ``ensure_before_rebalance``:

* ``True``  — rebalance may proceed. Either no model is bound (existing
  strategies unaffected), or the bound model's score panel for the
  computed ``as_of`` is present (or was just inferred).
* ``False`` — caller must **skip the rebalance** (place no orders) and
  log/alert. Returned when a model is bound but scores are still missing
  after inference, when inference raised, or when a ``model_key`` is
  configured but the model can no longer be resolved (broken binding —
  safe-skip per "no stale score fallback" default).

This module is intentionally side-effect free apart from logging and the
``ensure_quant_model_scores`` call; it does not touch orders, positions, or
the strategy runtime state.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from app.services.quant_models.ensure_scores import ensure_quant_model_scores
from app.services.quant_models.store import get_quant_model, list_quant_models
from app.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SCORE_LAG_DAYS = 1


def _resolve_params(strategy_row: Any) -> dict[str, Any]:
    """Extract the ``params`` dict from a strategy row or dict-like input.

    Accepts either:
    * a raw DB row from ``qd_strategies_trading`` where ``trading_config`` may
      be a JSON string or an already-parsed dict (the executor pre-parses it),
    * or a plain dict carrying ``params`` / ``model_key`` directly (used by
      unit tests and callers that pass a curated config).
    """
    if not isinstance(strategy_row, dict):
        return {}
    # Direct params on the row (test/convenience path).
    direct = strategy_row.get("params")
    if isinstance(direct, dict):
        return dict(direct)
    # Strategy row path: params live inside trading_config.
    trading_config = strategy_row.get("trading_config")
    if isinstance(trading_config, str) and trading_config.strip():
        try:
            trading_config = json.loads(trading_config)
        except Exception:
            trading_config = {}
    if not isinstance(trading_config, dict):
        trading_config = {}
    nested = trading_config.get("params")
    if isinstance(nested, dict):
        return dict(nested)
    return {}


def _resolve_model(strategy_row: Any, params: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve the bound quant model.

    Priority:
    1. ``params.model_key`` → ``get_quant_model``
    2. ``params.source`` + ``params.version`` → match a published model
    3. give up → ``None`` (treated as "no binding")
    """
    model_key = str(params.get("model_key") or "").strip()
    if model_key:
        return get_quant_model(model_key)

    source = str(params.get("source") or "").strip()
    version = str(params.get("version") or "").strip()
    if source and version:
        for model in list_quant_models(status="published"):
            if (
                str(model.get("alpha_source") or "") == source
                and str(model.get("alpha_version") or "") == version
            ):
                return model
    return None


def ensure_before_rebalance(strategy_row: Any, trade_date: date) -> bool:
    """Ensure the bound quant model has scores for ``trade_date - score_lag_days``.

    Returns ``True`` if the rebalance may proceed; ``False`` if the caller
    must skip the rebalance and alert.
    """
    if not isinstance(trade_date, date):
        try:
            trade_date = date.fromisoformat(str(trade_date)[:10])
        except Exception:
            logger.warning(
                "ensure_before_rebalance: invalid trade_date=%r; allowing rebalance",
                trade_date,
            )
            return True

    params = _resolve_params(strategy_row)
    if not params:
        return True

    model_key_configured = bool(str(params.get("model_key") or "").strip())
    model = _resolve_model(strategy_row, params)
    if not model:
        if model_key_configured:
            # Broken binding: model_key was set but the model is gone/archived.
            # Safe-skip rather than silently trading without fresh scores.
            logger.error(
                "quant_model bound but not found; skipping rebalance: "
                "model_key=%s trade_date=%s",
                params.get("model_key"), trade_date,
            )
            return False
        # No binding at all → existing strategies unaffected.
        return True

    raw_lag = params.get("score_lag_days")
    if raw_lag is None or raw_lag == "":
        score_lag_days = DEFAULT_SCORE_LAG_DAYS
    else:
        try:
            score_lag_days = int(raw_lag)
        except (TypeError, ValueError):
            score_lag_days = DEFAULT_SCORE_LAG_DAYS
    score_lag_days = max(0, score_lag_days)
    as_of = trade_date - timedelta(days=score_lag_days)

    try:
        result = ensure_quant_model_scores(model, [as_of])
    except Exception as exc:
        logger.error(
            "quant_model ensure raised; skipping rebalance: "
            "model_key=%s as_of=%s err=%s",
            model.get("model_key"), as_of, exc,
        )
        return False

    still_missing = list(result.get("still_missing") or [])
    if still_missing:
        logger.error(
            "quant_model scores still missing; skipping rebalance: "
            "model_key=%s as_of=%s missing=%s",
            model.get("model_key"), as_of, still_missing[:5],
        )
        return False

    return True
