"""Price-volume factors used by A-share equity books.

Definitions follow the usual sell-side convention: momentum skips the
most recent week so it does not fight short-term reversal. Every value
at date t uses rows at or before t.
"""

from __future__ import annotations

import pandas as pd


def raw_factor_panels(
    adj_close: pd.DataFrame,
    turnover: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Return raw factor panels keyed by name. Higher is not yet 'better'."""
    daily = adj_close / adj_close.shift(1) - 1.0
    return {
        "rev_5": adj_close / adj_close.shift(5) - 1.0,
        "mom_20_skip5": adj_close.shift(5) / adj_close.shift(25) - 1.0,
        "vol_20": daily.rolling(20, min_periods=20).std(ddof=0),
        "turn_20": turnover.rolling(20, min_periods=20).median(),
    }
