"""Tushare sync stubs for CSI300 enhanced-index factor store."""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from app.data_sources.tushare_cn import _build_pro, is_tushare_configured, tencent_code_to_ts_code
from app.utils.logger import get_logger

logger = get_logger(__name__)

CSI300_INDEX_TS_CODE = "000300.SH"


def fetch_index_weights(
    *,
    index_code: str = CSI300_INDEX_TS_CODE,
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    Pull CSI300 constituent weights via Tushare `index_weight`.

    Returns columns: trade_date, con_code, weight (percent as provided by Tushare).
    Empty DataFrame when Tushare is not configured or the call fails.
    """
    if not is_tushare_configured():
        logger.info("Tushare not configured; skip index_weight fetch")
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])

    pro = _build_pro()
    if pro is None:
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])

    ts_code = tencent_code_to_ts_code(index_code)
    kwargs: dict[str, Any] = {"index_code": ts_code}
    if trade_date:
        kwargs["trade_date"] = str(trade_date)
    if start_date:
        kwargs["start_date"] = str(start_date)
    if end_date:
        kwargs["end_date"] = str(end_date)

    try:
        frame = pro.index_weight(**kwargs)
    except Exception as exc:
        logger.warning("Tushare index_weight failed index=%s: %s", ts_code, exc)
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])

    return normalize_index_weight_frame(frame)


def normalize_index_weight_frame(frame: Any) -> pd.DataFrame:
    """Normalize raw Tushare index_weight rows into a stable schema."""
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])

    df = frame.copy()
    rename = {}
    if "con_code" not in df.columns and "con_ts_code" in df.columns:
        rename["con_ts_code"] = "con_code"
    if rename:
        df = df.rename(columns=rename)
    keep = [c for c in ("trade_date", "con_code", "weight") if c in df.columns]
    if len(keep) < 3:
        return pd.DataFrame(columns=["trade_date", "con_code", "weight"])
    out = df[keep].copy()
    out["trade_date"] = out["trade_date"].astype(str)
    out["con_code"] = out["con_code"].astype(str)
    out["weight"] = pd.to_numeric(out["weight"], errors="coerce")
    return out.dropna(subset=["weight"]).reset_index(drop=True)


def weights_to_platform_map(frame: pd.DataFrame) -> dict[str, float]:
    """Map Tushare weight percent to platform CNStock symbols summing to 1."""
    if frame is None or frame.empty:
        return {}
    rows: dict[str, float] = {}
    for _, row in frame.iterrows():
        code = str(row.get("con_code") or "").strip().upper()
        if not code:
            continue
        if code.endswith(".SH") or code.endswith(".SZ"):
            symbol = f"CNStock:{code}"
        else:
            ts = tencent_code_to_ts_code(code)
            symbol = f"CNStock:{ts}"
        try:
            w = float(row.get("weight"))
        except (TypeError, ValueError):
            continue
        if w > 0:
            rows[symbol] = rows.get(symbol, 0.0) + w
    total = sum(rows.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in rows.items()}


def fetch_daily_basic_stub(
    *,
    trade_date: str,
    fields: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """
    Skeleton for valuation / turnover fields from Tushare `daily_basic`.

    Not persisted yet; returns empty when unconfigured.
    """
    _ = fields
    if not is_tushare_configured():
        return pd.DataFrame()
    pro = _build_pro()
    if pro is None:
        return pd.DataFrame()
    try:
        return pro.daily_basic(trade_date=str(trade_date))
    except Exception as exc:
        logger.warning("Tushare daily_basic failed trade_date=%s: %s", trade_date, exc)
        return pd.DataFrame()
