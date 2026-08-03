"""Tushare sync for CSI300 enhanced-index factor store."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
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
    from app.markets.cn_stock.symbols import canonicalize_cnstock_key

    if frame is None or frame.empty:
        return {}
    rows: dict[str, float] = {}
    for _, row in frame.iterrows():
        code = str(row.get("con_code") or "").strip().upper()
        if not code:
            continue
        symbol = canonicalize_cnstock_key(code)
        if not symbol:
            continue
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


def rebuild_csi300_membership_from_index_weights(
    *,
    universe_code: str = "csi300",
) -> dict[str, Any]:
    """Replace ``qd_universe_members`` with PIT intervals from index weights.

    Each distinct ``trade_date`` in ``qd_csi300_index_weights`` becomes an
    interval ``[trade_date, next_trade_date)`` (last board stays open-ended).
    Symbols are stored as ``600519.SH`` / ``000001.SZ``.
    """
    from app.markets.cn_stock.symbols import canonicalize_cn_symbol

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT id FROM qd_universes WHERE code = ?", (universe_code,))
        uni = cur.fetchone()
        if not uni:
            raise ValueError(f"universe not found: {universe_code}")
        universe_id = int(uni["id"])

        cur.execute(
            """
            SELECT trade_date, con_code, weight
            FROM qd_csi300_index_weights
            ORDER BY trade_date ASC, con_code ASC
            """
        )
        raw_rows = list(cur.fetchall() or [])
        if not raw_rows:
            return {
                "universe_id": universe_id,
                "universe_code": universe_code,
                "boards": 0,
                "members_inserted": 0,
            }

        by_date: dict[date, list[tuple[str, float]]] = {}
        for row in raw_rows:
            td = row["trade_date"]
            if isinstance(td, datetime):
                td = td.date()
            elif not isinstance(td, date):
                td = date.fromisoformat(str(td)[:10])
            code = canonicalize_cn_symbol(str(row["con_code"] or ""))
            if not code:
                continue
            try:
                weight_pct = float(row["weight"])
            except (TypeError, ValueError):
                continue
            if weight_pct <= 0 or weight_pct != weight_pct:
                continue
            by_date.setdefault(td, []).append((code, weight_pct))

        dates = sorted(by_date)
        cur.execute("DELETE FROM qd_universe_members WHERE universe_id = ?", (universe_id,))
        inserted = 0
        version = f"index_weight_pit:{dates[0].isoformat()}:{dates[-1].isoformat()}"
        for idx, td in enumerate(dates):
            next_td = dates[idx + 1] if idx + 1 < len(dates) else None
            board = sorted(by_date[td], key=lambda item: (-item[1], item[0]))
            for rank, (code, weight_pct) in enumerate(board, start=1):
                cur.execute(
                    """
                    INSERT INTO qd_universe_members (
                        universe_id, market, symbol, name, market_type,
                        valid_from, valid_to, member_weight, member_rank,
                        source_version, metadata_json
                    )
                    VALUES (
                        ?, 'CNStock', ?, '', 'spot',
                        ?, ?, ?, ?,
                        ?, '{}'::jsonb
                    )
                    """,
                    (
                        universe_id,
                        code,
                        td,
                        next_td,
                        float(weight_pct) / 100.0,
                        int(rank),
                        version,
                    ),
                )
                inserted += 1
        db.commit()

    logger.info(
        "rebuilt csi300 membership universe=%s boards=%s members=%s span=%s..%s",
        universe_code,
        len(dates),
        inserted,
        dates[0],
        dates[-1],
    )
    return {
        "universe_id": universe_id,
        "universe_code": universe_code,
        "boards": len(dates),
        "members_inserted": inserted,
        "first_trade_date": dates[0].isoformat(),
        "last_trade_date": dates[-1].isoformat(),
    }


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


_FLOW_COLUMNS = (
    "trade_date",
    "ts_code",
    "north_net_buy",
    "margin_balance",
    "metadata_json",
)

_CONSENSUS_COLUMNS = (
    "trade_date",
    "ts_code",
    "eps_fy1",
    "pe_fy1",
    "rating_mean",
    "metadata_json",
)

_RATING_MAP: dict[str, float] = {
    "买入": 5.0,
    "增持": 4.0,
    "中性": 3.0,
    "减持": 2.0,
    "卖出": 1.0,
    "强烈推荐": 5.0,
    "推荐": 4.0,
    "观望": 3.0,
}


def _safe_tushare_api(pro: Any, api_name: str, **kwargs: Any) -> pd.DataFrame:
    """Call a Tushare pro API; return empty DataFrame on any failure."""
    try:
        fn = getattr(pro, api_name)
        frame = fn(**kwargs)
    except Exception as exc:
        logger.warning("Tushare %s failed kwargs=%s: %s", api_name, kwargs, exc)
        return pd.DataFrame()
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame()
    return frame.copy()


def _shift_trade_date(trade_date: str, days: int) -> str:
    dt = datetime.strptime(str(trade_date), "%Y%m%d")
    return (dt + timedelta(days=days)).strftime("%Y%m%d")


def _rating_to_numeric(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    mapped = _RATING_MAP.get(text)
    if mapped is not None:
        return mapped
    try:
        num = float(text)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _normalize_hk_hold_frame(frame: Any, *, trade_date: str) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["trade_date", "ts_code", "vol", "ratio"])
    df = frame.copy()
    if "ts_code" not in df.columns and "code" in df.columns:
        df = df.rename(columns={"code": "ts_code"})
    if "ts_code" not in df.columns:
        return pd.DataFrame(columns=["trade_date", "ts_code", "vol", "ratio"])
    out = df.copy()
    out["trade_date"] = str(trade_date)
    out["ts_code"] = out["ts_code"].astype(str)
    if "vol" in out.columns:
        out["vol"] = pd.to_numeric(out["vol"], errors="coerce")
    else:
        out["vol"] = pd.NA
    if "ratio" in out.columns:
        out["ratio"] = pd.to_numeric(out["ratio"], errors="coerce")
    else:
        out["ratio"] = pd.NA
    return out.dropna(subset=["ts_code"]).reset_index(drop=True)


def _normalize_margin_detail_frame(frame: Any, *, trade_date: str) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=["trade_date", "ts_code", "margin_balance"])
    df = frame.copy()
    if "ts_code" not in df.columns:
        return pd.DataFrame(columns=["trade_date", "ts_code", "margin_balance"])
    balance_col = "rzye" if "rzye" in df.columns else None
    if balance_col is None:
        for candidate in ("margin_balance", "fin_balance"):
            if candidate in df.columns:
                balance_col = candidate
                break
    if balance_col is None:
        return pd.DataFrame(columns=["trade_date", "ts_code", "margin_balance"])
    out = df[["ts_code"]].copy()
    out["trade_date"] = str(trade_date)
    out["ts_code"] = out["ts_code"].astype(str)
    out["margin_balance"] = pd.to_numeric(df[balance_col], errors="coerce")
    return out.dropna(subset=["ts_code"]).reset_index(drop=True)


def _merge_flow_frames(
    *,
    trade_date: str,
    hk_today: pd.DataFrame,
    hk_prev: pd.DataFrame,
    margin: pd.DataFrame,
) -> pd.DataFrame:
    hk_today = _normalize_hk_hold_frame(hk_today, trade_date=trade_date)
    hk_prev = _normalize_hk_hold_frame(hk_prev, trade_date=_shift_trade_date(trade_date, -1))
    margin = _normalize_margin_detail_frame(margin, trade_date=trade_date)

    prev_vol = {}
    if not hk_prev.empty and "vol" in hk_prev.columns:
        for _, row in hk_prev.iterrows():
            code = str(row["ts_code"])
            if pd.notna(row.get("vol")):
                prev_vol[code] = float(row["vol"])

    rows: dict[str, dict[str, Any]] = {}

    if not hk_today.empty:
        for _, row in hk_today.iterrows():
            code = str(row["ts_code"])
            vol = float(row["vol"]) if pd.notna(row.get("vol")) else None
            ratio = float(row["ratio"]) if pd.notna(row.get("ratio")) else None
            north = None
            if vol is not None and code in prev_vol:
                north = vol - prev_vol[code]
            meta: dict[str, Any] = {"hk_vol": vol, "hk_ratio": ratio}
            if north is not None:
                meta["north_vol_delta"] = north
            rows[code] = {
                "trade_date": str(trade_date),
                "ts_code": code,
                "north_net_buy": north,
                "margin_balance": None,
                "metadata_json": meta,
            }

    if not margin.empty:
        for _, row in margin.iterrows():
            code = str(row["ts_code"])
            bal = float(row["margin_balance"]) if pd.notna(row.get("margin_balance")) else None
            if code not in rows:
                rows[code] = {
                    "trade_date": str(trade_date),
                    "ts_code": code,
                    "north_net_buy": None,
                    "margin_balance": bal,
                    "metadata_json": {"margin_src": "margin_detail"},
                }
            else:
                rows[code]["margin_balance"] = bal
                rows[code]["metadata_json"]["margin_src"] = "margin_detail"

    if not rows:
        return pd.DataFrame(columns=list(_FLOW_COLUMNS))
    return pd.DataFrame(list(rows.values()))


def normalize_flow_daily_frame(frame: Any) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=list(_FLOW_COLUMNS))
    df = frame.copy()
    if "trade_date" not in df.columns or "ts_code" not in df.columns:
        return pd.DataFrame(columns=list(_FLOW_COLUMNS))
    out = df.copy()
    out["trade_date"] = out["trade_date"].astype(str)
    out["ts_code"] = out["ts_code"].astype(str)
    for col in ("north_net_buy", "margin_balance"):
        if col not in out.columns:
            out[col] = pd.NA
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "metadata_json" not in out.columns:
        out["metadata_json"] = [{} for _ in range(len(out))]
    return out[(out["ts_code"] != "")].reset_index(drop=True)


def fetch_flow_daily(*, trade_date: str) -> pd.DataFrame:
    """Pull northbound holdings + margin detail for one trade date."""
    if not is_tushare_configured():
        logger.info("Tushare not configured; skip flow_daily fetch")
        return pd.DataFrame(columns=list(_FLOW_COLUMNS))
    pro = _build_pro()
    if pro is None:
        return pd.DataFrame(columns=list(_FLOW_COLUMNS))

    hk_today = _safe_tushare_api(pro, "hk_hold", trade_date=str(trade_date))
    hk_prev = pd.DataFrame()
    for offset in (-1, -2, -3, -4, -5):
        prev_date = _shift_trade_date(trade_date, offset)
        candidate = _safe_tushare_api(pro, "hk_hold", trade_date=prev_date)
        if not candidate.empty:
            hk_prev = candidate
            break
    margin = _safe_tushare_api(pro, "margin_detail", trade_date=str(trade_date))
    return normalize_flow_daily_frame(
        _merge_flow_frames(
            trade_date=str(trade_date),
            hk_today=hk_today,
            hk_prev=hk_prev,
            margin=margin,
        )
    )


def persist_flow_daily(frame: pd.DataFrame) -> int:
    df = normalize_flow_daily_frame(frame)
    if df.empty:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        for _, row in df.iterrows():
            meta = row.get("metadata_json")
            if isinstance(meta, str):
                meta_json = meta
            else:
                meta_json = json.dumps(meta or {}, ensure_ascii=False)
            north = float(row["north_net_buy"]) if pd.notna(row.get("north_net_buy")) else None
            margin = float(row["margin_balance"]) if pd.notna(row.get("margin_balance")) else None
            cur.execute(
                """
                INSERT INTO qd_ashare_flow_daily (
                    trade_date, ts_code, north_net_buy, margin_balance, source, metadata_json
                )
                VALUES (%s::date, %s, %s, %s, 'tushare', %s::jsonb)
                ON CONFLICT (trade_date, ts_code, source)
                DO UPDATE SET
                    north_net_buy = EXCLUDED.north_net_buy,
                    margin_balance = EXCLUDED.margin_balance,
                    metadata_json = EXCLUDED.metadata_json,
                    ingested_at = NOW()
                """,
                (str(row["trade_date"]), str(row["ts_code"]), north, margin, meta_json),
            )
        db.commit()
    return int(len(df))


def fetch_and_persist_flow_daily(*, trade_date: str) -> int:
    return persist_flow_daily(fetch_flow_daily(trade_date=trade_date))


def _normalize_report_rc_frame(frame: Any, *, trade_date: str) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    df = frame.copy()
    if "ts_code" not in df.columns:
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    df["ts_code"] = df["ts_code"].astype(str)
    if "report_date" in df.columns:
        df["report_date"] = df["report_date"].astype(str)
        df = df[df["report_date"] <= str(trade_date)]
    if df.empty:
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))

    eps_col = "eps" if "eps" in df.columns else None
    pe_col = "pe" if "pe" in df.columns else None
    rating_col = "rating" if "rating" in df.columns else None

    rows: list[dict[str, Any]] = []
    for ts_code, group in df.groupby("ts_code"):
        eps_vals = pd.to_numeric(group[eps_col], errors="coerce") if eps_col else pd.Series(dtype=float)
        pe_vals = pd.to_numeric(group[pe_col], errors="coerce") if pe_col else pd.Series(dtype=float)
        rating_vals = (
            group[rating_col].map(_rating_to_numeric)
            if rating_col
            else pd.Series(dtype=float)
        )
        eps_mean = float(eps_vals.mean()) if eps_col and eps_vals.notna().any() else None
        pe_mean = float(pe_vals.mean()) if pe_col and pe_vals.notna().any() else None
        rating_mean = float(rating_vals.mean()) if rating_col and rating_vals.notna().any() else None
        if eps_mean is None and pe_mean is None and rating_mean is None:
            continue
        rows.append(
            {
                "trade_date": str(trade_date),
                "ts_code": str(ts_code),
                "eps_fy1": eps_mean,
                "pe_fy1": pe_mean,
                "rating_mean": rating_mean,
                "metadata_json": {
                    "consensus_src": "report_rc",
                    "report_count": int(len(group)),
                },
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    return pd.DataFrame(rows)


def _normalize_forecast_vip_frame(frame: Any, *, trade_date: str) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    df = frame.copy()
    if "ts_code" not in df.columns:
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    df["ts_code"] = df["ts_code"].astype(str)
    eps_col = None
    for candidate in ("eps", "p_eps", "net_profit_max"):
        if candidate in df.columns:
            eps_col = candidate
            break
    rows: list[dict[str, Any]] = []
    for ts_code, group in df.groupby("ts_code"):
        eps_vals = pd.to_numeric(group[eps_col], errors="coerce") if eps_col else pd.Series(dtype=float)
        eps_mean = float(eps_vals.mean()) if eps_col and eps_vals.notna().any() else None
        if eps_mean is None:
            continue
        rows.append(
            {
                "trade_date": str(trade_date),
                "ts_code": str(ts_code),
                "eps_fy1": eps_mean,
                "pe_fy1": None,
                "rating_mean": None,
                "metadata_json": {"consensus_src": "forecast_vip", "forecast_count": int(len(group))},
            }
        )
    if not rows:
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    return pd.DataFrame(rows)


def normalize_consensus_daily_frame(frame: Any) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    df = frame.copy()
    if "trade_date" not in df.columns or "ts_code" not in df.columns:
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    out = df.copy()
    out["trade_date"] = out["trade_date"].astype(str)
    out["ts_code"] = out["ts_code"].astype(str)
    for col in ("eps_fy1", "pe_fy1", "rating_mean"):
        if col not in out.columns:
            out[col] = pd.NA
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "metadata_json" not in out.columns:
        out["metadata_json"] = [{} for _ in range(len(out))]
    return out[(out["ts_code"] != "")].reset_index(drop=True)


def fetch_consensus_daily(*, trade_date: str) -> pd.DataFrame:
    """Pull analyst consensus cross-section; tries report_rc then forecast_vip."""
    if not is_tushare_configured():
        logger.info("Tushare not configured; skip consensus_daily fetch")
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))
    pro = _build_pro()
    if pro is None:
        return pd.DataFrame(columns=list(_CONSENSUS_COLUMNS))

    start_date = _shift_trade_date(trade_date, -30)
    report_rc = _safe_tushare_api(
        pro,
        "report_rc",
        start_date=start_date,
        end_date=str(trade_date),
    )
    normalized = normalize_consensus_daily_frame(
        _normalize_report_rc_frame(report_rc, trade_date=str(trade_date))
    )
    if not normalized.empty:
        return normalized

    forecast_vip = _safe_tushare_api(pro, "forecast_vip", trade_date=str(trade_date))
    return normalize_consensus_daily_frame(
        _normalize_forecast_vip_frame(forecast_vip, trade_date=str(trade_date))
    )


def persist_consensus_daily(frame: pd.DataFrame) -> int:
    df = normalize_consensus_daily_frame(frame)
    if df.empty:
        return 0
    with get_db_connection() as db:
        cur = db.cursor()
        for _, row in df.iterrows():
            meta = row.get("metadata_json")
            if isinstance(meta, str):
                meta_json = meta
            else:
                meta_json = json.dumps(meta or {}, ensure_ascii=False)
            eps = float(row["eps_fy1"]) if pd.notna(row.get("eps_fy1")) else None
            pe = float(row["pe_fy1"]) if pd.notna(row.get("pe_fy1")) else None
            rating = float(row["rating_mean"]) if pd.notna(row.get("rating_mean")) else None
            cur.execute(
                """
                INSERT INTO qd_ashare_consensus_daily (
                    trade_date, ts_code, eps_fy1, pe_fy1, rating_mean, source, metadata_json
                )
                VALUES (%s::date, %s, %s, %s, %s, 'tushare', %s::jsonb)
                ON CONFLICT (trade_date, ts_code, source)
                DO UPDATE SET
                    eps_fy1 = EXCLUDED.eps_fy1,
                    pe_fy1 = EXCLUDED.pe_fy1,
                    rating_mean = EXCLUDED.rating_mean,
                    metadata_json = EXCLUDED.metadata_json,
                    ingested_at = NOW()
                """,
                (str(row["trade_date"]), str(row["ts_code"]), eps, pe, rating, meta_json),
            )
        db.commit()
    return int(len(df))


def fetch_and_persist_consensus_daily(*, trade_date: str) -> int:
    return persist_consensus_daily(fetch_consensus_daily(trade_date=trade_date))


def run_csi300_enhanced_daily_sync(
    *,
    trade_date: str | None = None,
    skip_industry: bool = False,
    skip_flow: bool = False,
    skip_consensus: bool = False,
) -> dict[str, Any]:
    """Orchestrate day-end CSI300 enhanced panel sync.

    Missing Tushare / API failures return 0 for the affected panel and do not raise.
    """
    day = str(trade_date or date.today().strftime("%Y%m%d"))
    index_n = fetch_and_persist_index_weights(trade_date=day)
    membership: dict[str, Any] = {}
    if index_n > 0:
        try:
            membership = rebuild_csi300_membership_from_index_weights()
        except Exception as exc:
            logger.warning("csi300 membership rebuild skipped: %s", exc)
            membership = {"error": str(exc)[:240]}
    counts: dict[str, Any] = {
        "trade_date": day,
        "index_weights": index_n,
        "daily_basic": fetch_and_persist_daily_basic(trade_date=day),
        "industry_map": 0 if skip_industry else fetch_and_persist_industry_map(),
        "flow_daily": 0 if skip_flow else fetch_and_persist_flow_daily(trade_date=day),
        "consensus_daily": 0 if skip_consensus else fetch_and_persist_consensus_daily(trade_date=day),
        "membership": membership,
    }
    return counts
