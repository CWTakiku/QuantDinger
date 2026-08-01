"""Tushare sync for CSI300 enhanced-index factor store."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import pandas as pd

from app.data_sources.tushare_cn import _build_pro, is_tushare_configured, tencent_code_to_ts_code
from app.utils.db import get_db_connection
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


_DAILY_BASIC_COLUMNS = (
    "trade_date",
    "ts_code",
    "close",
    "pe_ttm",
    "pb",
    "ps_ttm",
    "dv_ttm",
    "total_mv",
    "circ_mv",
    "turnover_rate",
    "volume_ratio",
)


def fetch_daily_basic(
    *,
    trade_date: str,
    fields: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Pull valuation / turnover fields from Tushare `daily_basic`."""
    _ = fields
    if not is_tushare_configured():
        logger.info("Tushare not configured; skip daily_basic fetch")
        return pd.DataFrame(columns=list(_DAILY_BASIC_COLUMNS))
    pro = _build_pro()
    if pro is None:
        return pd.DataFrame(columns=list(_DAILY_BASIC_COLUMNS))
    try:
        frame = pro.daily_basic(trade_date=str(trade_date))
    except Exception as exc:
        logger.warning("Tushare daily_basic failed trade_date=%s: %s", trade_date, exc)
        return pd.DataFrame(columns=list(_DAILY_BASIC_COLUMNS))
    return normalize_daily_basic_frame(frame)


def fetch_daily_basic_stub(
    *,
    trade_date: str,
    fields: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Backward-compatible alias for fetch_daily_basic."""
    return fetch_daily_basic(trade_date=trade_date, fields=fields)


def normalize_daily_basic_frame(frame: Any) -> pd.DataFrame:
    """Normalize raw Tushare daily_basic rows into a stable schema."""
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=list(_DAILY_BASIC_COLUMNS))
    df = frame.copy()
    keep = [c for c in _DAILY_BASIC_COLUMNS if c in df.columns]
    if "trade_date" not in keep or "ts_code" not in keep:
        return pd.DataFrame(columns=list(_DAILY_BASIC_COLUMNS))
    out = df[keep].copy()
    out["trade_date"] = out["trade_date"].astype(str)
    out["ts_code"] = out["ts_code"].astype(str)
    for col in keep:
        if col in ("trade_date", "ts_code"):
            continue
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["ts_code"]).reset_index(drop=True)


def persist_index_weights(frame: pd.DataFrame) -> int:
    df = normalize_index_weight_frame(frame)
    if df.empty:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        for _, row in df.iterrows():
            cur.execute(
                """
                INSERT INTO qd_csi300_index_weights (trade_date, con_code, weight, source)
                VALUES (%s::date, %s, %s, 'tushare')
                ON CONFLICT (trade_date, con_code, source)
                DO UPDATE SET weight = EXCLUDED.weight, ingested_at = NOW()
                """,
                (str(row["trade_date"]), str(row["con_code"]), float(row["weight"])),
            )
        db.commit()
    return int(len(df))


def fetch_and_persist_index_weights(*, trade_date: str | None = None) -> int:
    return persist_index_weights(fetch_index_weights(trade_date=trade_date))


def persist_daily_basic(frame: pd.DataFrame) -> int:
    df = normalize_daily_basic_frame(frame)
    if df.empty:
        return 0
    numeric_cols = [c for c in _DAILY_BASIC_COLUMNS if c not in ("trade_date", "ts_code")]
    with get_db_connection() as db:
        cur = db.cursor()
        for _, row in df.iterrows():
            params: list[Any] = [str(row["trade_date"]), str(row["ts_code"])]
            params.extend(
                float(row[col]) if pd.notna(row.get(col)) else None for col in numeric_cols
            )
            cur.execute(
                """
                INSERT INTO qd_ashare_daily_basic (
                    trade_date, ts_code, close, pe_ttm, pb, ps_ttm, dv_ttm,
                    total_mv, circ_mv, turnover_rate, volume_ratio, source
                )
                VALUES (
                    %s::date, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'tushare'
                )
                ON CONFLICT (trade_date, ts_code, source)
                DO UPDATE SET
                    close = EXCLUDED.close,
                    pe_ttm = EXCLUDED.pe_ttm,
                    pb = EXCLUDED.pb,
                    ps_ttm = EXCLUDED.ps_ttm,
                    dv_ttm = EXCLUDED.dv_ttm,
                    total_mv = EXCLUDED.total_mv,
                    circ_mv = EXCLUDED.circ_mv,
                    turnover_rate = EXCLUDED.turnover_rate,
                    volume_ratio = EXCLUDED.volume_ratio,
                    ingested_at = NOW()
                """,
                tuple(params),
            )
        db.commit()
    return int(len(df))


def fetch_and_persist_daily_basic(*, trade_date: str) -> int:
    return persist_daily_basic(fetch_daily_basic(trade_date=trade_date))


def fetch_industry_map() -> pd.DataFrame:
    """Pull listed-stock industry tags from Tushare `stock_basic`."""
    columns = ["ts_code", "industry"]
    if not is_tushare_configured():
        logger.info("Tushare not configured; skip stock_basic fetch")
        return pd.DataFrame(columns=columns)
    pro = _build_pro()
    if pro is None:
        return pd.DataFrame(columns=columns)
    try:
        frame = pro.stock_basic(list_status="L", fields="ts_code,industry")
    except Exception as exc:
        logger.warning("Tushare stock_basic failed: %s", exc)
        return pd.DataFrame(columns=columns)
    return normalize_industry_map_frame(frame)


def normalize_industry_map_frame(frame: Any) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["ts_code", "industry"])
    df = frame.copy()
    if "ts_code" not in df.columns or "industry" not in df.columns:
        return pd.DataFrame(columns=["ts_code", "industry"])
    out = df[["ts_code", "industry"]].copy()
    out["ts_code"] = out["ts_code"].astype(str)
    out["industry"] = out["industry"].astype(str).str.strip()
    return out[(out["ts_code"] != "") & (out["industry"] != "")].reset_index(drop=True)


def persist_industry_map(frame: pd.DataFrame, *, as_of: date | None = None) -> int:
    df = normalize_industry_map_frame(frame)
    if df.empty:
        return 0
    as_of_date = as_of or date.today()
    with get_db_connection() as db:
        cur = db.cursor()
        for _, row in df.iterrows():
            cur.execute(
                """
                INSERT INTO qd_ashare_industry_map (
                    ts_code, industry, industry_src, as_of, source
                )
                VALUES (%s, %s, 'tushare_stock_basic', %s::date, 'tushare')
                ON CONFLICT (ts_code, industry_src, as_of, source)
                DO UPDATE SET industry = EXCLUDED.industry, ingested_at = NOW()
                """,
                (str(row["ts_code"]), str(row["industry"]), as_of_date.isoformat()),
            )
        db.commit()
    return int(len(df))


def fetch_and_persist_industry_map() -> int:
    return persist_industry_map(fetch_industry_map())
