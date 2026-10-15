"""Cross-sectional preprocessing and walk-forward factor weights.

Industry neutralization is intentionally absent: the industry map on
hand starts in 2026, and applying today's industry to earlier years
would leak. Size is neutralized only on dates where circulating market
cap covers the cross-section.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.services.csi300_enhanced.preprocess import cross_section_zscore, winsorize_mad


def last_trainable_date(dates: pd.DatetimeIndex, live_start: pd.Timestamp, embargo: int) -> pd.Timestamp:
    """Last session whose ``embargo``-day forward return finishes before ``live_start``."""
    live_pos = int(dates.searchsorted(pd.Timestamp(live_start), side="left"))
    pos = live_pos - int(embargo) - 1
    if pos < 0:
        raise ValueError("not enough history before the live window")
    return pd.Timestamp(dates[pos])


def preprocess_cross_section(
    values: pd.Series,
    log_mcap: pd.Series | None = None,
    *,
    min_names: int = 30,
    min_size_coverage: float = 0.8,
) -> pd.Series:
    """MAD-winsorize, z-score, then residualize on log market cap when coverage allows."""
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < min_names:
        return pd.Series(dtype=float)
    scored = cross_section_zscore(winsorize_mad(clean))
    if log_mcap is None:
        return scored
    size = pd.to_numeric(log_mcap, errors="coerce").reindex(scored.index)
    if float(size.notna().mean()) < min_size_coverage or int(size.notna().sum()) < min_names:
        return scored
    return _size_residual_zscore(scored, size.dropna())


def preprocess_panel(
    raw: pd.DataFrame,
    universe: pd.DataFrame,
    log_mcap: pd.DataFrame | None = None,
    *,
    min_names: int = 30,
) -> pd.DataFrame:
    """Standardize each date inside the point-in-time universe."""
    out = pd.DataFrame(np.nan, index=raw.index, columns=raw.columns, dtype=float)
    size_frame = log_mcap.reindex(index=raw.index, columns=raw.columns) if log_mcap is not None else None
    members = universe.reindex(index=raw.index, columns=raw.columns).fillna(False)
    for stamp in raw.index:
        row = raw.loc[stamp].where(members.loc[stamp])
        size = None if size_frame is None else size_frame.loc[stamp]
        scored = preprocess_cross_section(row, size, min_names=min_names)
        if scored.empty:
            continue
        out.loc[stamp, scored.index] = scored.to_numpy(dtype=float)
    return out


def estimate_weights(
    factors: dict[str, pd.DataFrame],
    forward: pd.DataFrame,
    universe: pd.DataFrame,
    dates: pd.DatetimeIndex,
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    *,
    step: int = 5,
    min_obs: int = 26,
    min_names: int = 30,
) -> dict[str, dict[str, float]]:
    """Freeze a sign and an ICIR weight for each factor.

    Rank IC is computed on non-overlapping sessions so overlapping
    five-day returns do not shrink the standard deviation.
    """
    sample = _sample_dates(dates, train_start, train_end, step)
    stats: dict[str, dict[str, float]] = {}
    for name, panel in factors.items():
        ic_values = [
            _rank_ic(panel.loc[stamp], forward.loc[stamp], universe.loc[stamp], min_names=min_names)
            for stamp in sample
            if stamp in panel.index and stamp in forward.index
        ]
        ic_values = [value for value in ic_values if np.isfinite(value)]
        mean = float(np.mean(ic_values)) if ic_values else float("nan")
        std = float(np.std(ic_values, ddof=1)) if len(ic_values) > 1 else float("nan")
        sign = -1.0 if np.isfinite(mean) and mean < 0 else 1.0
        oriented = sign * mean if np.isfinite(mean) else float("nan")
        if len(ic_values) < min_obs or not np.isfinite(oriented) or oriented <= 0:
            ir = 0.0
        elif not np.isfinite(std) or std <= 1e-12:
            ir = float(oriented)
        else:
            ir = float(oriented / std)
        stats[name] = {
            "sign": float(sign),
            "ir": float(max(ir, 0.0)),
            "mean_ic": float(mean) if np.isfinite(mean) else 0.0,
            "obs": float(len(ic_values)),
            "weight": 0.0,
        }
    total = sum(item["ir"] for item in stats.values())
    if total > 0:
        for item in stats.values():
            item["weight"] = item["ir"] / total
    return stats


def blend_scores(
    factors: dict[str, pd.DataFrame],
    schedule: list[tuple[pd.Timestamp, pd.Timestamp, dict[str, dict[str, float]]]],
) -> pd.DataFrame:
    """Apply each frozen sign and weight on ``[start, end)``."""
    first = next(iter(factors.values()))
    score = pd.DataFrame(np.nan, index=first.index, columns=first.columns, dtype=float)
    for start, end, stats in schedule:
        if sum(item["weight"] for item in stats.values()) <= 0:
            continue
        window = (score.index >= start) & (score.index < end)
        if not bool(window.any()):
            continue
        combined = None
        for name, panel in factors.items():
            weight = float(stats[name]["weight"])
            if weight <= 0:
                continue
            signed = float(stats[name]["sign"]) * panel.loc[window]
            combined = signed * weight if combined is None else combined.add(signed * weight, fill_value=0.0)
        if combined is not None:
            score.loc[window] = combined
    return score


def quarter_starts(dates: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """First trading session on or after each calendar quarter inside the window."""
    stamps: list[pd.Timestamp] = []
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    quarter = _quarter_open(start)
    while quarter <= end:
        anchor_from = max(quarter, start)
        pos = int(dates.searchsorted(anchor_from, side="left"))
        if pos >= len(dates):
            break
        anchor = pd.Timestamp(dates[pos])
        if anchor > end:
            break
        if _quarter_open(anchor) == quarter and anchor not in stamps:
            stamps.append(anchor)
        month = int(quarter.month) + 3
        year = int(quarter.year) + (1 if month > 12 else 0)
        month = month - 12 if month > 12 else month
        quarter = pd.Timestamp(date(year, month, 1))
    return stamps


def _quarter_open(stamp: pd.Timestamp) -> pd.Timestamp:
    month = ((int(stamp.month) - 1) // 3) * 3 + 1
    return pd.Timestamp(date(int(stamp.year), month, 1))


def _sample_dates(dates: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp, step: int) -> list[pd.Timestamp]:
    eligible = [pd.Timestamp(stamp) for stamp in dates if start <= stamp <= end]
    if not eligible:
        return []
    origin = int(dates.searchsorted(eligible[0], side="left"))
    out = []
    for stamp in eligible:
        pos = int(dates.searchsorted(stamp, side="left"))
        if (pos - origin) % int(step) == 0:
            out.append(stamp)
    return out


def _rank_ic(factor: pd.Series, forward: pd.Series, mask: pd.Series, *, min_names: int) -> float:
    frame = pd.concat(
        [
            pd.to_numeric(factor, errors="coerce").where(mask).rename("factor"),
            pd.to_numeric(forward, errors="coerce").where(mask).rename("forward"),
        ],
        axis=1,
    ).dropna()
    if len(frame) < min_names:
        return float("nan")
    value = frame["factor"].rank(method="average").corr(frame["forward"].rank(method="average"), method="pearson")
    return float(value) if value is not None and np.isfinite(value) else float("nan")


def cross_section_chart(
    score: pd.DataFrame,
    forward: pd.DataFrame,
    mask: pd.DataFrame,
    dates: pd.DatetimeIndex,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    step: int = 5,
    groups: int = 5,
    cost_rate: float = 0.00125,
    min_names: int = 30,
) -> dict:
    """Quantile curves and Rank IC in the shape the research page already draws."""
    stamps = _sample_dates(dates, pd.Timestamp(start), pd.Timestamp(end), step)
    group_count = int(groups)
    ic_rows: list[dict] = []
    group_returns: dict[int, list[dict]] = {index: [] for index in range(1, group_count + 1)}
    previous: dict[int, dict[str, float]] = {index: {} for index in range(1, group_count + 1)}
    autocorrelations: list[float] = []
    monotonicities: list[float] = []
    previous_ranks: pd.Series | None = None
    covered = 0
    considered = 0
    for stamp in stamps:
        if stamp not in score.index or stamp not in forward.index or stamp not in mask.index:
            continue
        active = mask.loc[stamp].fillna(False)
        if int(active.sum()) < min_names:
            continue
        frame = pd.concat(
            [
                pd.to_numeric(score.loc[stamp], errors="coerce").where(active).rename("factor"),
                pd.to_numeric(forward.loc[stamp], errors="coerce").where(active).rename("forward"),
            ],
            axis=1,
        ).replace([np.inf, -np.inf], np.nan).dropna()
        considered += int(active.sum())
        covered += int(len(frame))
        if len(frame) < max(min_names, group_count):
            continue
        ranks = frame["factor"].rank(method="average")
        ic = ranks.corr(frame["forward"].rank(method="average"), method="pearson")
        if ic is None or not np.isfinite(float(ic)):
            continue
        ic_rows.append({"time": pd.Timestamp(stamp).strftime("%Y-%m-%d"), "value": float(ic)})
        if previous_ranks is not None:
            aligned = pd.concat([previous_ranks, ranks], axis=1).dropna()
            if len(aligned) >= min_names:
                auto = aligned.iloc[:, 0].rank().corr(aligned.iloc[:, 1].rank(), method="pearson")
                if auto is not None and np.isfinite(float(auto)):
                    autocorrelations.append(float(auto))
        previous_ranks = ranks
        labels = pd.qcut(frame["factor"].rank(method="first"), q=group_count, labels=False) + 1
        period_returns = []
        for group in range(1, group_count + 1):
            names = list(frame.index[labels == group])
            if not names:
                continue
            weights = {symbol: 1.0 / len(names) for symbol in names}
            turnover = _weight_turnover(previous[group], weights)
            cost = turnover * (1.0 if not previous[group] else 2.0) * float(cost_rate)
            gross = float(frame.loc[names, "forward"].mean())
            previous[group] = weights
            group_returns[group].append({
                "time": pd.Timestamp(stamp).strftime("%Y-%m-%d"),
                "group": group,
                "grossReturn": gross,
                "netReturn": gross - cost,
                "turnover": turnover,
                "cost": cost,
                "members": [str(symbol) for symbol in names],
                "memberCount": len(names),
            })
            period_returns.append((group, gross))
        if len(period_returns) >= 3:
            order = pd.Series([item[0] for item in period_returns], dtype="float64")
            outcome = pd.Series([item[1] for item in period_returns], dtype="float64")
            mono = order.rank().corr(outcome.rank(), method="pearson")
            if mono is not None and np.isfinite(float(mono)):
                monotonicities.append(float(mono))
    ic_series = pd.Series([row["value"] for row in ic_rows], dtype="float64")
    rolling = ic_series.rolling(20, min_periods=5).mean() if len(ic_series) else ic_series
    for index, row in enumerate(ic_rows):
        value = rolling.iloc[index] if len(rolling) else np.nan
        row["rolling"] = None if pd.isna(value) else float(value)
    curves = []
    for group, rows in group_returns.items():
        gross_nav = 1.0
        net_nav = 1.0
        points = []
        for row in rows:
            gross_nav *= 1.0 + float(row["grossReturn"])
            net_nav *= 1.0 + float(row["netReturn"])
            points.append({"time": row["time"], "gross": gross_nav, "net": net_nav})
        curves.append({"group": group, "points": points, "finalGross": gross_nav, "finalNet": net_nav})
    long_short = _long_short_curve(group_returns.get(group_count, []), group_returns.get(1, []))
    mean_ic = float(ic_series.mean()) if len(ic_series) else 0.0
    std_ic = float(ic_series.std(ddof=1)) if len(ic_series) > 1 else 0.0
    periods = 252.0 / max(int(step), 1)
    icir = mean_ic / std_ic * (periods ** 0.5) if std_ic > 0 else 0.0
    half = max(1, len(ic_series) // 2)
    rebalance = [row for rows in group_returns.values() for row in rows[1:]]
    average_turnover = (
        sum(float(row["turnover"]) for row in rebalance) / len(rebalance) if rebalance else 0.0
    )
    return {
        "rankIc": mean_ic,
        "icir": float(icir),
        "icPositiveRate": float((ic_series > 0).mean()) if len(ic_series) else 0.0,
        "icSeries": ic_rows,
        "groupCurves": curves,
        "longShortCurve": long_short,
        "groupObservations": [row for rows in group_returns.values() for row in rows],
        "monotonicity": float(sum(monotonicities) / len(monotonicities)) if monotonicities else 0.0,
        "coverage": covered / considered if considered else 0.0,
        "missingRate": 1.0 - (covered / considered if considered else 0.0),
        "averageTurnover": average_turnover,
        "grossLongShortReturn": float(long_short[-1]["gross"] - 1.0) if long_short else 0.0,
        "netLongShortReturn": float(long_short[-1]["net"] - 1.0) if long_short else 0.0,
        "stability": {
            "firstHalfIc": float(ic_series.iloc[:half].mean()) if len(ic_series) else 0.0,
            "secondHalfIc": float(ic_series.iloc[half:].mean()) if len(ic_series) > half else 0.0,
            "rankAutocorrelation": float(sum(autocorrelations) / len(autocorrelations)) if autocorrelations else 0.0,
        },
    }


def factor_rank_correlation(
    factors: dict[str, pd.DataFrame],
    mask: pd.DataFrame,
    dates: pd.DatetimeIndex,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    step: int = 5,
    min_names: int = 30,
) -> dict:
    """Average cross-sectional rank correlation of the frozen factors."""
    names = list(factors)
    matrices = []
    for stamp in _sample_dates(dates, pd.Timestamp(start), pd.Timestamp(end), step):
        if stamp not in mask.index:
            continue
        active = mask.loc[stamp].fillna(False)
        frame = pd.DataFrame({name: factors[name].loc[stamp].where(active) for name in names}).dropna()
        if len(frame) < min_names:
            continue
        matrices.append(frame.rank().corr(method="pearson"))
    if not matrices:
        return {"factors": names, "matrix": []}
    average = sum(matrices) / len(matrices)
    values = average.loc[names, names].to_numpy(dtype=float)
    return {
        "factors": names,
        "matrix": [[None if not np.isfinite(item) else round(float(item), 4) for item in row] for row in values],
    }


def _weight_turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    if not previous:
        return 1.0
    symbols = set(previous) | set(current)
    return 0.5 * sum(abs(float(current.get(symbol, 0.0)) - float(previous.get(symbol, 0.0))) for symbol in symbols)


def _long_short_curve(long_rows: list[dict], short_rows: list[dict]) -> list[dict]:
    short_map = {row["time"]: row for row in short_rows}
    gross_nav = 1.0
    net_nav = 1.0
    points = []
    for long_row in long_rows:
        short_row = short_map.get(long_row["time"])
        if not short_row:
            continue
        gross_return = float(long_row["grossReturn"]) - float(short_row["grossReturn"])
        cost = float(long_row["cost"]) + float(short_row["cost"])
        gross_nav *= 1.0 + gross_return
        net_nav *= 1.0 + gross_return - cost
        points.append({"time": long_row["time"], "gross": gross_nav, "net": net_nav})
    return points


def _size_residual_zscore(score: pd.Series, log_mcap: pd.Series) -> pd.Series:
    joined = pd.concat([score.rename("y"), log_mcap.rename("x")], axis=1).dropna()
    design = np.column_stack([np.ones(len(joined)), joined["x"].to_numpy(dtype=float)])
    beta, *_ = np.linalg.lstsq(design, joined["y"].to_numpy(dtype=float), rcond=None)
    residual = pd.Series(joined["y"].to_numpy(dtype=float) - design @ beta, index=joined.index)
    return cross_section_zscore(residual)
