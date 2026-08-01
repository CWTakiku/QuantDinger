"""Point-in-time CSI300 benchmark weights with explicit fallbacks."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from app.markets.cn_stock.symbols import canonicalize_cnstock_key
from app.services.csi300_enhanced.tushare_sync import weights_to_platform_map
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

SOURCE_CSI300_PIT = "csi300_pit"
SOURCE_UNIVERSE_MEMBER_WEIGHT = "universe_member_weight"
SOURCE_EQUAL_WEIGHT = "equal_weight"


def _canonical_symbol_list(symbols: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols or []:
        key = canonicalize_cnstock_key(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _as_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _canonicalize_weight_map(weights: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (weights or {}).items():
        canon = canonicalize_cnstock_key(key)
        if not canon:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed == parsed:
            out[canon] = out.get(canon, 0.0) + parsed
    total = sum(out.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in out.items()}


def _load_index_weights(as_of: date) -> list[dict[str, Any]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT con_code, weight
            FROM qd_csi300_index_weights
            WHERE trade_date = (
              SELECT MAX(trade_date) FROM qd_csi300_index_weights WHERE trade_date <= ?
            )
            """,
            (as_of,),
        )
        return list(cur.fetchall() or [])


def _load_universe_member_weights(as_of: date) -> dict[str, float]:
    """Load csi300 member weights valid on ``as_of`` (PIT)."""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT m.market, m.symbol, m.member_weight
            FROM qd_universe_members m
            JOIN qd_universes u ON u.id = m.universe_id
            WHERE u.code = 'csi300'
              AND m.valid_from <= ?
              AND (m.valid_to IS NULL OR m.valid_to > ?)
              AND m.member_weight IS NOT NULL AND m.member_weight > 0
            """,
            (as_of, as_of),
        )
        out: dict[str, float] = {}
        for row in cur.fetchall() or []:
            key = canonicalize_cnstock_key(f"{row['market']}:{row['symbol']}")
            out[key] = float(row["member_weight"])
        return out


def _filter_and_normalize(
    weights: dict[str, float],
    symbols: list[str] | None,
) -> dict[str, float]:
    mapped = _canonicalize_weight_map(weights)
    if not mapped:
        return {}
    if symbols:
        wanted = _canonical_symbol_list(symbols)
        filtered = {k: mapped.get(k, 0.0) for k in wanted}
        total = sum(filtered.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in filtered.items() if v > 0}
    return mapped


def get_csi300_bench_weights_with_meta(
    as_of: date | str,
    *,
    symbols: list[str] | None = None,
) -> tuple[dict[str, float], str]:
    """Return (weights, source).

    ``source`` is one of ``csi300_pit``, ``universe_member_weight``, ``equal_weight``.
    Prefer PIT index weights; only fall back when no PIT row exists for ``as_of``.
    """
    as_of_d = _as_date(as_of)
    rows = _load_index_weights(as_of_d)
    if rows:
        frame = pd.DataFrame(rows)
        mapped = weights_to_platform_map(frame.assign(trade_date=as_of_d.isoformat()))
        mapped = _canonicalize_weight_map(mapped)
        if mapped:
            if symbols is None:
                return mapped, SOURCE_CSI300_PIT
            normalized = _filter_and_normalize(mapped, symbols)
            if normalized:
                return normalized, SOURCE_CSI300_PIT
            # PIT exists but none of the requested symbols are in that board —
            # do NOT fall back to current-universe weights (membership look-ahead).
            logger.warning(
                "csi300 pit weights miss requested symbols as_of=%s n_req=%s; equal_weight",
                as_of_d,
                len(symbols or []),
            )
            support = _canonical_symbol_list(symbols)
            if not support:
                return {}, SOURCE_EQUAL_WEIGHT
            w = 1.0 / len(support)
            return {s: w for s in support}, SOURCE_EQUAL_WEIGHT

    uni = _load_universe_member_weights(as_of_d)
    if uni:
        logger.warning("csi300 bench fallback=universe_member_weight as_of=%s", as_of_d)
        normalized = _filter_and_normalize(uni, symbols)
        if normalized:
            return normalized, SOURCE_UNIVERSE_MEMBER_WEIGHT

    support = _canonical_symbol_list(symbols)
    if not support:
        logger.warning("csi300 bench fallback=empty as_of=%s", as_of_d)
        return {}, SOURCE_EQUAL_WEIGHT
    logger.warning("csi300 bench fallback=equal_weight n=%s as_of=%s", len(support), as_of_d)
    w = 1.0 / len(support)
    return {s: w for s in support}, SOURCE_EQUAL_WEIGHT


def get_csi300_bench_weights(
    as_of: date | str,
    *,
    symbols: list[str] | None = None,
) -> dict[str, float]:
    weights, _source = get_csi300_bench_weights_with_meta(as_of, symbols=symbols)
    return weights


def get_csi300_bench_weight_source(
    as_of: date | str,
    *,
    symbols: list[str] | None = None,
) -> str:
    _weights, source = get_csi300_bench_weights_with_meta(as_of, symbols=symbols)
    return source
