"""Point-in-time CSI300 benchmark weights with explicit fallbacks."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from app.services.csi300_enhanced.tushare_sync import weights_to_platform_map
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

SOURCE_CSI300_PIT = "csi300_pit"
SOURCE_UNIVERSE_MEMBER_WEIGHT = "universe_member_weight"
SOURCE_EQUAL_WEIGHT = "equal_weight"


def _as_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


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


def _load_universe_member_weights() -> dict[str, float]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT m.market, m.symbol, m.member_weight
            FROM qd_universe_members m
            JOIN qd_universes u ON u.id = m.universe_id
            WHERE u.code = 'csi300' AND m.valid_to IS NULL
              AND m.member_weight IS NOT NULL AND m.member_weight > 0
            """
        )
        out: dict[str, float] = {}
        for row in cur.fetchall() or []:
            key = f"{row['market']}:{row['symbol']}"
            out[key] = float(row["member_weight"])
        return out


def _filter_and_normalize(
    weights: dict[str, float],
    symbols: list[str] | None,
) -> dict[str, float]:
    mapped = dict(weights)
    if symbols:
        mapped = {k: mapped.get(k, 0.0) for k in symbols}
    total = sum(mapped.values())
    if total > 0:
        return {k: v / total for k, v in mapped.items()}
    return {}


def get_csi300_bench_weights_with_meta(
    as_of: date | str,
    *,
    symbols: list[str] | None = None,
) -> tuple[dict[str, float], str]:
    """Return (weights, source).

    ``source`` is one of ``csi300_pit``, ``universe_member_weight``, ``equal_weight``.
    """
    as_of_d = _as_date(as_of)
    rows = _load_index_weights(as_of_d)
    if rows:
        frame = pd.DataFrame(rows)
        mapped = weights_to_platform_map(frame.assign(trade_date=as_of_d.isoformat()))
        if mapped:
            if symbols:
                normalized = _filter_and_normalize(mapped, symbols)
                if normalized:
                    return normalized, SOURCE_CSI300_PIT
            else:
                return mapped, SOURCE_CSI300_PIT
    uni = _load_universe_member_weights()
    if uni:
        logger.warning("csi300 bench fallback=universe_member_weight as_of=%s", as_of_d)
        normalized = _filter_and_normalize(uni, symbols)
        if normalized:
            return normalized, SOURCE_UNIVERSE_MEMBER_WEIGHT
    support = list(symbols or [])
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
