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


def load_valuation_panel(
    symbols: list[str],
    as_of: date | str,
) -> pd.DataFrame:
    """Load PE/PB (Tushare pe_ttm/pb) cross-section for platform symbols.

    Returns a DataFrame indexed by platform symbol with columns ``PE`` and ``PB``,
    matching the shape of ``get_fundamentals(["PE", "PB"], ...)``.
    """
    as_of_d = _as_date(as_of)
    sym_list = [str(s) for s in (symbols or []) if str(s).strip()]
    if not sym_list:
        return pd.DataFrame(columns=["PE", "PB"])

    ts_by_sym = {sym: _platform_to_ts_code(sym) for sym in sym_list}
    ts_codes = sorted(set(ts_by_sym.values()))
    placeholders = ",".join(["?"] * len(ts_codes))
    val_by_ts: dict[str, dict[str, float]] = {}

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT ts_code, pe_ttm, pb
            FROM qd_ashare_daily_basic
            WHERE ts_code IN ({placeholders})
              AND trade_date = (
                SELECT MAX(trade_date) FROM qd_ashare_daily_basic WHERE trade_date <= ?
              )
            """,
            (*ts_codes, as_of_d),
        )
        for row in cur.fetchall() or []:
            pe = row.get("pe_ttm")
            pb = row.get("pb")
            out: dict[str, float] = {}
            if pe is not None:
                try:
                    pe_f = float(pe)
                    if np.isfinite(pe_f) and pe_f != 0:
                        out["PE"] = pe_f
                except (TypeError, ValueError):
                    pass
            if pb is not None:
                try:
                    pb_f = float(pb)
                    if np.isfinite(pb_f) and pb_f != 0:
                        out["PB"] = pb_f
                except (TypeError, ValueError):
                    pass
            if out:
                val_by_ts[str(row["ts_code"])] = out

    rows = {
        sym: val_by_ts[ts_by_sym[sym]]
        for sym in sym_list
        if ts_by_sym[sym] in val_by_ts
    }
    if not rows:
        return pd.DataFrame(columns=["PE", "PB"])
    frame = pd.DataFrame.from_dict(rows, orient="index")
    for col in ("PE", "PB"):
        if col not in frame.columns:
            frame[col] = np.nan
    return frame[["PE", "PB"]]


def load_flow_panel(
    symbols: list[str],
    as_of: date | str,
) -> pd.Series:
    """Load northbound net-buy flow cross-section for platform symbols."""
    as_of_d = _as_date(as_of)
    sym_list = [str(s) for s in (symbols or []) if str(s).strip()]
    if not sym_list:
        return pd.Series(dtype=float)

    ts_by_sym = {sym: _platform_to_ts_code(sym) for sym in sym_list}
    ts_codes = sorted(set(ts_by_sym.values()))
    placeholders = ",".join(["?"] * len(ts_codes))
    flow_by_ts: dict[str, float] = {}

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT ts_code, north_net_buy, margin_balance
            FROM qd_ashare_flow_daily
            WHERE ts_code IN ({placeholders})
              AND source = 'tushare'
              AND trade_date = (
                SELECT MAX(trade_date) FROM qd_ashare_flow_daily WHERE trade_date <= ?
              )
            """,
            (*ts_codes, as_of_d),
        )
        for row in cur.fetchall() or []:
            north = row.get("north_net_buy")
            margin = row.get("margin_balance")
            value = None
            if north is not None:
                try:
                    north_f = float(north)
                    if np.isfinite(north_f):
                        value = north_f
                except (TypeError, ValueError):
                    pass
            if value is None and margin is not None:
                try:
                    margin_f = float(margin)
                    if np.isfinite(margin_f):
                        value = margin_f
                except (TypeError, ValueError):
                    pass
            if value is not None:
                flow_by_ts[str(row["ts_code"])] = value

    return pd.Series(
        {sym: flow_by_ts[ts_by_sym[sym]] for sym in sym_list if ts_by_sym[sym] in flow_by_ts},
        dtype=float,
    )


def load_consensus_panel(
    symbols: list[str],
    as_of: date | str,
) -> pd.Series:
    """Load analyst consensus composite (EPS / forward E/P / rating) for platform symbols."""
    as_of_d = _as_date(as_of)
    sym_list = [str(s) for s in (symbols or []) if str(s).strip()]
    if not sym_list:
        return pd.Series(dtype=float)

    ts_by_sym = {sym: _platform_to_ts_code(sym) for sym in sym_list}
    ts_codes = sorted(set(ts_by_sym.values()))
    placeholders = ",".join(["?"] * len(ts_codes))
    consensus_by_ts: dict[str, float] = {}

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            f"""
            SELECT ts_code, eps_fy1, pe_fy1, rating_mean
            FROM qd_ashare_consensus_daily
            WHERE ts_code IN ({placeholders})
              AND source = 'tushare'
              AND trade_date = (
                SELECT MAX(trade_date) FROM qd_ashare_consensus_daily WHERE trade_date <= ?
              )
            """,
            (*ts_codes, as_of_d),
        )
        for row in cur.fetchall() or []:
            pieces: list[float] = []
            eps = row.get("eps_fy1")
            if eps is not None:
                try:
                    eps_f = float(eps)
                    if np.isfinite(eps_f):
                        pieces.append(eps_f)
                except (TypeError, ValueError):
                    pass
            pe = row.get("pe_fy1")
            if pe is not None:
                try:
                    pe_f = float(pe)
                    if np.isfinite(pe_f) and pe_f != 0:
                        pieces.append(1.0 / pe_f)
                except (TypeError, ValueError):
                    pass
            rating = row.get("rating_mean")
            if rating is not None:
                try:
                    rating_f = float(rating)
                    if np.isfinite(rating_f):
                        pieces.append(rating_f)
                except (TypeError, ValueError):
                    pass
            if pieces:
                consensus_by_ts[str(row["ts_code"])] = float(np.mean(pieces))

    return pd.Series(
        {sym: consensus_by_ts[ts_by_sym[sym]] for sym in sym_list if ts_by_sym[sym] in consensus_by_ts},
        dtype=float,
    )


def _cross_section_ic(factor: pd.Series, forward_ret: pd.Series) -> float:
    f = pd.to_numeric(factor, errors="coerce")
    r = pd.to_numeric(forward_ret, errors="coerce")
    aligned = pd.concat([f.rename("f"), r.rename("r")], axis=1).dropna()
    if len(aligned) < 5:
        return float("nan")
    ic = aligned["f"].rank(method="average").corr(
        aligned["r"].rank(method="average"),
        method="pearson",
    )
    return float(ic) if ic is not None and np.isfinite(ic) else float("nan")


def _equal_layer_weights(layers: list[str]) -> dict[str, float]:
    if not layers:
        return {}
    weight = 1.0 / len(layers)
    return {name: weight for name in layers}


def apply_icir_weights(
    factor_panel: dict[str, pd.DataFrame],
    forward_returns: pd.DataFrame,
    *,
    window: int,
) -> dict[str, float]:
    """ICIR-based non-negative layer weights; equal-weight when samples are insufficient."""
    layers = [name for name in factor_panel if isinstance(factor_panel.get(name), pd.DataFrame)]
    if not layers:
        return {}

    window = max(1, int(window))
    min_obs = max(5, window)
    equal = _equal_layer_weights(layers)

    icir_scores: dict[str, float] = {}
    for name in layers:
        panel = factor_panel[name].sort_index()
        if panel.empty:
            return equal
        fwd = forward_returns.reindex(panel.index)
        ic_values: list[float] = []
        for dt in panel.index:
            row = panel.loc[dt]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            fwd_row = fwd.loc[dt] if dt in fwd.index else pd.Series(dtype=float)
            if isinstance(fwd_row, pd.DataFrame):
                fwd_row = fwd_row.iloc[0]
            ic = _cross_section_ic(row, fwd_row)
            if np.isfinite(ic):
                ic_values.append(ic)
        tail = ic_values[-window:]
        if len(tail) < min_obs:
            return equal
        ic_mean = float(np.mean(tail))
        ic_std = float(np.std(tail, ddof=1)) if len(tail) > 1 else 0.0
        if ic_std <= 0:
            icir_scores[name] = max(0.0, ic_mean)
        else:
            icir_scores[name] = max(0.0, ic_mean / ic_std)

    total = sum(icir_scores.values())
    if total <= 0:
        return equal
    return {name: score / total for name, score in icir_scores.items()}


def apply_regime(
    layer_weights: dict[str, float],
    *,
    bench_ret_20: float,
    threshold: float,
    mom_scale: float = 0.0,
) -> dict[str, float]:
    """Scale momentum layer down when benchmark return breaches the regime threshold."""
    if not layer_weights:
        return {}

    out = {str(k): float(v) for k, v in layer_weights.items()}
    total = sum(out.values())
    if total <= 0:
        return out

    normalized = {k: v / total for k, v in out.items()}
    if float(bench_ret_20) >= float(threshold):
        return normalized

    if "momentum" in normalized:
        normalized["momentum"] = float(mom_scale) * normalized["momentum"]

    active = {k: v for k, v in normalized.items() if v > 0}
    if not active:
        others = [k for k in normalized if k != "momentum"]
        if not others:
            return {k: 0.0 for k in normalized}
        eq = 1.0 / len(others)
        return {k: (0.0 if k == "momentum" else eq) for k in normalized}

    re_total = sum(active.values())
    return {k: (v / re_total if v > 0 else 0.0) for k, v in normalized.items()}
