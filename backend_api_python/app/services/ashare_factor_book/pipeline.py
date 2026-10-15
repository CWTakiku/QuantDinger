"""Walk-forward CSI500 book: estimate, freeze, then trade the next quarter."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.services.ashare_factor_book.book import blocked_limit, simulate
from app.services.ashare_factor_book.evaluate import (
    blend_scores,
    cross_section_chart,
    estimate_weights,
    factor_rank_correlation,
    last_trainable_date,
    preprocess_panel,
    quarter_starts,
)
from app.services.ashare_factor_book.factors import raw_factor_panels
from app.services.ashare_factor_book.metrics import summarize, year_returns

HORIZON = 5
HOLDINGS = 40
BUFFER = 60
MAX_WEIGHT = 0.04
MIN_HISTORY = 60
MIN_NAMES = 30
TRAIN_YEARS = 3
LIQUIDITY_FLOOR = 0.20


def run_book(
    frames: dict[str, pd.DataFrame],
    members: list[dict],
    benchmark: pd.Series | None = None,
    *,
    start: date,
    end: date,
    capital: float = 500_000.0,
    commission: float = 0.00025,
    slippage: float = 0.001,
) -> dict[str, object]:
    """Run one continuous walk-forward. Weights never see the quarter they trade."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    adj_close = frames["adj_close"].sort_index()
    dates = pd.DatetimeIndex(adj_close.index)
    symbols = [symbol for symbol in adj_close.columns if _tradable_board(str(symbol))]
    adj_close = adj_close.reindex(columns=symbols)
    raw_open = frames["raw_open"].reindex(index=dates, columns=symbols)
    raw_close = frames["raw_close"].reindex(index=dates, columns=symbols)
    adj_factor = frames["adj_factor"].reindex(index=dates, columns=symbols)
    turnover = frames["turnover"].reindex(index=dates, columns=symbols)
    amount = frames["amount"].reindex(index=dates, columns=symbols)
    volume = frames["volume"].reindex(index=dates, columns=symbols)
    is_st = frames["is_st"].reindex(index=dates, columns=symbols).fillna(False).astype(bool)
    up_limit = frames["up_limit"].reindex(index=dates, columns=symbols)
    down_limit = frames["down_limit"].reindex(index=dates, columns=symbols)
    log_mcap = frames.get("log_mcap")
    if log_mcap is not None:
        log_mcap = log_mcap.reindex(index=dates, columns=symbols)

    universe = _membership_mask(dates, symbols, members)
    history = adj_close.notna().cumsum() >= MIN_HISTORY
    liquid = _liquid_mask(amount, universe)
    eligible = universe & history & liquid & ~is_st
    volume_values = volume.fillna(0).astype(float)
    suspended = raw_open.isna() | (volume_values <= 0)
    limit_up = blocked_limit(raw_open, up_limit, buying=True).fillna(False)
    limit_down = blocked_limit(raw_open, down_limit, buying=False).fillna(False)

    raw_factors = raw_factor_panels(adj_close, turnover)
    processed = {}
    tradable = universe & ~is_st
    for name, panel in raw_factors.items():
        print(f"preprocess {name}", flush=True)
        processed[name] = preprocess_panel(panel.where(eligible), tradable, log_mcap, min_names=MIN_NAMES)
    forward = adj_close.shift(-HORIZON) / adj_close - 1.0
    anchors = quarter_starts(dates, start_ts, end_ts)
    schedule: list[tuple[pd.Timestamp, pd.Timestamp, dict]] = []
    diagnostics = []
    for index, anchor in enumerate(anchors):
        live_end = anchors[index + 1] if index + 1 < len(anchors) else end_ts + pd.Timedelta(days=1)
        try:
            train_end = last_trainable_date(dates, anchor, HORIZON)
        except ValueError:
            continue
        train_start = train_end - pd.DateOffset(years=TRAIN_YEARS)
        stats = estimate_weights(
            processed,
            forward,
            universe & ~is_st,
            dates,
            pd.Timestamp(train_start),
            pd.Timestamp(train_end),
            step=HORIZON,
            min_obs=26,
            min_names=MIN_NAMES,
        )
        schedule.append((anchor, pd.Timestamp(live_end), stats))
        realized = estimate_weights(
            processed,
            forward,
            universe & ~is_st,
            dates,
            anchor,
            min(pd.Timestamp(live_end) - pd.Timedelta(days=1), end_ts),
            step=HORIZON,
            min_obs=1,
            min_names=MIN_NAMES,
        )
        diagnostics.append({
            "start": anchor.date().isoformat(),
            "train_end": pd.Timestamp(train_end).date().isoformat(),
            "weights": stats,
            "realized_ic": {name: item["mean_ic"] for name, item in realized.items()},
        })

    score = blend_scores(processed, schedule)
    origin = int(dates.searchsorted(start_ts, side="left"))
    end_pos = int(dates.searchsorted(end_ts, side="right")) - 1
    slice_from = max(0, origin - 1)
    sim_dates = dates[slice_from:end_pos + 1]
    trade_positions = list(range(1, len(sim_dates), HORIZON))
    traded = simulate(
        raw_open=raw_open.reindex(sim_dates),
        raw_close=raw_close.reindex(sim_dates),
        adj_factor=adj_factor.reindex(sim_dates),
        score=score.reindex(sim_dates),
        can_hold=eligible.reindex(sim_dates).fillna(False),
        can_buy=(eligible.shift(1).fillna(False) & ~is_st & ~limit_up & ~suspended).reindex(sim_dates).fillna(False),
        can_sell=(~limit_down & ~suspended).reindex(sim_dates).fillna(False),
        trade_positions=trade_positions,
        capital=capital,
        commission=commission,
        slippage=slippage,
        holdings=HOLDINGS,
        buffer=BUFFER,
        max_weight=MAX_WEIGHT,
    )
    equity = traded["equity"]
    equity = equity[equity.index >= start_ts]
    turnover = traded["turnover"]
    turnover = turnover[turnover.index >= start_ts]
    holding_count = traded["holdings"]
    holding_count = holding_count[holding_count.index >= start_ts]
    bench = None if benchmark is None else pd.to_numeric(benchmark, errors="coerce").sort_index()
    bench_initial = _previous_value(bench, dates, start_ts)
    window_bench = None if bench is None else bench.reindex(equity.index).ffill()
    stats_all = summarize(equity, window_bench, initial=capital, benchmark_initial=bench_initial)
    chart = _score_chart(
        score,
        raw_open,
        adj_factor,
        eligible,
        dates,
        start_ts,
        end_ts,
        commission=commission,
        slippage=slippage,
    )
    chart["factorCorrelation"] = factor_rank_correlation(
        {_FACTOR_LABELS.get(name, name): panel for name, panel in processed.items()},
        eligible,
        dates,
        start_ts,
        end_ts,
        step=HORIZON,
        min_names=MIN_NAMES,
    )
    return {
        "equity": equity,
        "benchmark": window_bench,
        "summary": stats_all,
        "years": year_returns(equity, window_bench, initial=capital, benchmark_initial=bench_initial),
        "quarters": diagnostics,
        "fees": traded["fees"],
        "fills": traded["fills"],
        "turnover": turnover,
        "holdings": holding_count,
        "capital": capital,
        "slippage": slippage,
        "holdings_target": HOLDINGS,
        "chart": chart,
    }


_FACTOR_LABELS = {
    "rev_5": "5日反转",
    "mom_20_skip5": "20日动量",
    "vol_20": "20日波动",
    "turn_20": "20日换手",
}


def _score_chart(
    score: pd.DataFrame,
    raw_open: pd.DataFrame,
    adj_factor: pd.DataFrame,
    eligible: pd.DataFrame,
    dates: pd.DatetimeIndex,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    commission: float,
    slippage: float,
) -> dict:
    """Five groups and Rank IC of the blended score, entered at the next open."""
    adjusted_open = raw_open * adj_factor
    forward = adjusted_open.shift(-(HORIZON + 1)) / adjusted_open.shift(-1) - 1.0
    return cross_section_chart(
        score,
        forward,
        eligible,
        dates,
        start,
        end,
        step=HORIZON,
        groups=5,
        cost_rate=float(commission) + float(slippage),
        min_names=MIN_NAMES,
    )


def _previous_value(series: pd.Series | None, dates: pd.DatetimeIndex, start: pd.Timestamp) -> float | None:
    if series is None or series.empty:
        return None
    prior = dates[dates < start]
    if len(prior) == 0:
        return None
    value = series.reindex(prior).ffill().iloc[-1]
    if value is None or not np.isfinite(float(value)) or float(value) <= 0:
        return None
    return float(value)


def _liquid_mask(amount: pd.DataFrame, universe: pd.DataFrame) -> pd.DataFrame:
    median = amount.rolling(20, min_periods=20).median()
    liquid = pd.DataFrame(False, index=amount.index, columns=amount.columns)
    values = liquid.to_numpy(copy=True)
    med = median.to_numpy(dtype=float)
    member = universe.reindex(index=amount.index, columns=amount.columns).fillna(False).to_numpy(dtype=bool)
    for row in range(len(amount.index)):
        observed = med[row][member[row]]
        observed = observed[np.isfinite(observed)]
        if len(observed) < MIN_NAMES:
            continue
        floor = float(np.quantile(observed, LIQUIDITY_FLOOR))
        values[row] = member[row] & np.isfinite(med[row]) & (med[row] >= floor)
    return pd.DataFrame(values, index=amount.index, columns=amount.columns)


def _membership_mask(dates: pd.DatetimeIndex, symbols: list[str], members: list[dict]) -> pd.DataFrame:
    columns = {symbol: index for index, symbol in enumerate(symbols)}
    values = np.zeros((len(dates), len(symbols)), dtype=bool)
    for row in members:
        column = columns.get(str(row.get("symbol") or ""))
        if column is None or row.get("valid_from") is None:
            continue
        start = pd.Timestamp(row["valid_from"])
        left = int(dates.searchsorted(start, side="left"))
        valid_to = row.get("valid_to")
        right = len(dates) if valid_to is None or pd.isna(valid_to) else int(dates.searchsorted(pd.Timestamp(valid_to), side="left"))
        if right > left:
            values[left:right, column] = True
    return pd.DataFrame(values, index=dates, columns=symbols)


def _tradable_board(symbol: str) -> bool:
    code, _, suffix = str(symbol).upper().partition(".")
    if suffix == "BJ" or not code.isdigit():
        return False
    return code.startswith(("60", "00", "30", "68"))
