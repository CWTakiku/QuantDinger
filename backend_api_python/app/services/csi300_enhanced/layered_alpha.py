"""Layered neutralized alpha assembly for CSI300 enhanced indexing."""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from app.data_sources.tushare_cn import tencent_code_to_ts_code
from app.services.csi300_enhanced.preprocess import (
    cross_section_zscore,
    neutralize_industry_size,
    winsorize_mad,
)
from app.utils.db import get_db_connection


def _as_date(value: date | str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _platform_to_ts_code(symbol: str) -> str:
    s = str(symbol).strip()
    if s.upper().startswith("CNSTOCK:"):
        s = s.split(":", 1)[1]
    return tencent_code_to_ts_code(s)


def _layer_active(series: pd.Series | None) -> bool:
    if series is None:
        return False
    values = pd.to_numeric(series, errors="coerce").dropna()
    return not values.empty


def combine_layers(
    layer_scores: dict[str, pd.Series],
    weights: dict[str, float],
) -> pd.Series:
    """Weighted combine layer z-scores; reallocate weights from missing layers."""
    active: dict[str, float] = {}
    for name, weight in weights.items():
        if weight <= 0:
            continue
        if not _layer_active(layer_scores.get(name)):
            continue
        active[name] = float(weight)
    if not active:
        return pd.Series(dtype=float)

    total = sum(active.values())
    if total <= 0:
        return pd.Series(dtype=float)

    norm = {name: w / total for name, w in active.items()}
    pieces: list[pd.Series] = []
    for name, weight in norm.items():
        s = pd.to_numeric(layer_scores[name], errors="coerce")
        pieces.append(s * weight)
    combined = pd.concat(pieces, axis=1).sum(axis=1, min_count=1)
    return cross_section_zscore(combined.dropna())


def build_neutralized_factor(
    raw: pd.Series,
    industry: dict[str, str] | pd.Series,
    log_mcap: pd.Series,
) -> pd.Series:
    s = cross_section_zscore(winsorize_mad(raw))
    return neutralize_industry_size(s, industry, log_mcap)


def load_industry_and_size(
    symbols: list[str],
    as_of: date | str,
) -> tuple[dict[str, str], pd.Series]:
    """Load latest industry map and log circ market cap for platform symbols."""
    as_of_d = _as_date(as_of)
    sym_list = [str(s) for s in (symbols or []) if str(s).strip()]
    if not sym_list:
        return {}, pd.Series(dtype=float)

    ts_by_sym = {sym: _platform_to_ts_code(sym) for sym in sym_list}
    ts_codes = sorted(set(ts_by_sym.values()))
    placeholders = ",".join(["?"] * len(ts_codes))

    industry_by_ts: dict[str, str] = {}
    size_by_ts: dict[str, float] = {}

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT ts_code, industry
            FROM qd_ashare_industry_map
            WHERE ts_code IN ({placeholders})
              AND as_of = (
                SELECT MAX(as_of) FROM qd_ashare_industry_map WHERE as_of <= ?
              )
            """,
            (*ts_codes, as_of_d),
        )
        for row in cur.fetchall() or []:
            industry_by_ts[str(row["ts_code"])] = str(row["industry"])

        cur.execute(
            f"""
            SELECT ts_code, circ_mv
            FROM qd_ashare_daily_basic
            WHERE ts_code IN ({placeholders})
              AND trade_date = (
                SELECT MAX(trade_date) FROM qd_ashare_daily_basic WHERE trade_date <= ?
              )
            """,
            (*ts_codes, as_of_d),
        )
        for row in cur.fetchall() or []:
            circ_mv = row.get("circ_mv")
            if circ_mv is None:
                continue
            try:
                mv = float(circ_mv)
            except (TypeError, ValueError):
                continue
            if np.isfinite(mv) and mv > 0:
                size_by_ts[str(row["ts_code"])] = float(np.log1p(mv))

    industry = {
        sym: industry_by_ts[ts_by_sym[sym]]
        for sym in sym_list
        if ts_by_sym[sym] in industry_by_ts
    }
    log_mcap = pd.Series(
        {sym: size_by_ts[ts_by_sym[sym]] for sym in sym_list if ts_by_sym[sym] in size_by_ts},
        dtype=float,
    )
    return industry, log_mcap
