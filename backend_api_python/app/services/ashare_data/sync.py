"""Tushare -> ``qd_ashare_*`` sync: per-trade-date pulls, backfill, daily job."""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from app.data_sources.tushare_cn import _build_pro
from app.services.ashare_data.rows import (
    build_bar_rows,
    build_st_intervals,
    build_suspension_rows,
    check_day_completeness,
    close_mismatch_codes,
    parse_ymd,
    st_codes_on,
)
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")
DAY_DATASETS = ("daily", "stk_limit", "suspend_d", "daily_basic", "stock_st")
# ST status primarily comes from name history; stock_st only supplements it.
REQUIRED_DATASETS = ("daily", "stk_limit", "suspend_d", "daily_basic")
BARS_DATASET = "bars"
ALERT_DATASET = "alert"
# Exchange data settles around 16:00-17:00; Tushare publishes after that.
DAILY_READY_HOUR = 17
DAILY_READY_MINUTE = 30
ALERT_HOUR = 20
MAX_CROSSED_REPAIRS = 50

_BAR_UPSERT = """
INSERT INTO qd_ashare_daily_bar
(ts_code, trade_date, open, high, low, close, pre_close, volume, amount,
 up_limit, down_limit, is_st, turnover_rate, turnover_rate_f, volume_ratio,
 total_mv, circ_mv, pe_ttm, pb)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
  open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
  close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
  volume = EXCLUDED.volume, amount = EXCLUDED.amount,
  up_limit = EXCLUDED.up_limit, down_limit = EXCLUDED.down_limit,
  is_st = EXCLUDED.is_st, turnover_rate = EXCLUDED.turnover_rate,
  turnover_rate_f = EXCLUDED.turnover_rate_f, volume_ratio = EXCLUDED.volume_ratio,
  total_mv = EXCLUDED.total_mv, circ_mv = EXCLUDED.circ_mv,
  pe_ttm = EXCLUDED.pe_ttm, pb = EXCLUDED.pb, updated_at = NOW()
"""

_SUSPENSION_UPSERT = """
INSERT INTO qd_ashare_suspension (ts_code, trade_date, suspend_type, suspend_timing)
VALUES (?, ?, ?, ?)
ON CONFLICT (ts_code, trade_date) DO UPDATE SET
  suspend_type = EXCLUDED.suspend_type, suspend_timing = EXCLUDED.suspend_timing
"""

# Tushare's pre_close is the ex-rights reference price, so a gap between the
# previous close and today's pre_close is exactly one corporate action.  Gaps
# below one tick are price rounding and must not compound into the factor.
_ADJ_TICK = 0.0051

_REBUILD_ADJ_SQL = f"""
WITH lagged AS (
    SELECT ts_code, trade_date, pre_close,
           LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close
    FROM qd_ashare_daily_bar
    {{where}}
), ratios AS (
    SELECT ts_code, trade_date,
           CASE WHEN prev_close IS NULL OR pre_close IS NULL OR pre_close <= 0 OR prev_close <= 0
                     OR ABS(prev_close - pre_close) < {_ADJ_TICK}
                THEN 0.0 ELSE LN(prev_close / pre_close) END AS log_ratio
    FROM lagged
), factors AS (
    SELECT ts_code, trade_date,
           EXP(SUM(log_ratio) OVER (PARTITION BY ts_code ORDER BY trade_date
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS factor
    FROM ratios
)
UPDATE qd_ashare_daily_bar AS b
SET adj_factor = f.factor
FROM factors AS f
WHERE b.ts_code = f.ts_code AND b.trade_date = f.trade_date
  AND b.adj_factor IS DISTINCT FROM f.factor
"""

_INCREMENTAL_ADJ_SQL = f"""
UPDATE qd_ashare_daily_bar AS b
SET adj_factor = COALESCE(prev.adj_factor, 1.0) * CASE
    WHEN prev.close IS NULL OR b.pre_close IS NULL OR b.pre_close <= 0 OR prev.close <= 0
         OR ABS(prev.close - b.pre_close) < {_ADJ_TICK}
    THEN 1.0 ELSE prev.close / b.pre_close END
FROM qd_ashare_daily_bar AS cur
LEFT JOIN LATERAL (
    SELECT p.close, p.adj_factor FROM qd_ashare_daily_bar AS p
    WHERE p.ts_code = cur.ts_code AND p.trade_date < cur.trade_date
    ORDER BY p.trade_date DESC LIMIT 1
) AS prev ON TRUE
WHERE b.ts_code = cur.ts_code AND b.trade_date = cur.trade_date AND cur.trade_date = ?
"""


def _ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


def _pro() -> Any:
    pro = _build_pro()
    if pro is None:
        raise RuntimeError("Tushare is not configured")
    return pro


def _query(pro: Any, api: str, attempts: int = 3, **params: Any) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            frame = pro.query(api, **params)
            return frame if frame is not None else pd.DataFrame()
        except Exception as exc:
            last_error = exc
            time.sleep(min(30.0, 3.0 * (attempt + 1)))
    raise RuntimeError(f"{api} {params} failed: {last_error}")


def trade_dates(start: date, end: date, *, pro: Any = None) -> list[date]:
    frame = _query(pro or _pro(), "trade_cal", exchange="SSE", start_date=_ymd(start), end_date=_ymd(end))
    if frame.empty:
        return []
    frame = frame[frame["is_open"].astype(str) == "1"]
    return sorted(d for d in (parse_ymd(v) for v in frame["cal_date"]) if d is not None)


def _fetch_dataset(pro: Any, name: str, day: date) -> pd.DataFrame:
    try:
        return _query(pro, name, trade_date=_ymd(day))
    except RuntimeError:
        if name in REQUIRED_DATASETS:
            raise
        logger.warning("ashare sync %s optional dataset %s unavailable", day, name)
        return pd.DataFrame()


def fetch_day(pro: Any, day: date) -> dict[str, pd.DataFrame]:
    # The relay is latency-bound; the client's process-wide limiter still caps the rate.
    with ThreadPoolExecutor(max_workers=len(DAY_DATASETS)) as pool:
        futures = {name: pool.submit(_fetch_dataset, pro, name, day) for name in DAY_DATASETS}
        frames = {name: future.result() for name, future in futures.items()}
    frames["daily"] = repair_crossed_daily(pro, day, frames["daily"], frames["daily_basic"])
    return frames


def repair_crossed_daily(pro: Any, day: date, daily: pd.DataFrame, basic: pd.DataFrame) -> pd.DataFrame:
    """Replace bulk ``daily`` rows that carry another stock's bar.

    The relay's by-date ``daily`` response sometimes serves a different
    stock's bar under a code (persistently, for the same codes); the
    per-code query returns the right one.
    """
    crossed = close_mismatch_codes(daily, basic)
    if not crossed or len(crossed) > MAX_CROSSED_REPAIRS:
        return daily
    replacements = []
    for code in crossed:
        row = _query(pro, "daily", ts_code=code, trade_date=_ymd(day))
        if not row.empty and not close_mismatch_codes(row, basic[basic["ts_code"] == code]):
            replacements.append(row)
    if not replacements:
        return daily
    fixed = pd.concat(replacements, ignore_index=True)
    logger.warning("ashare sync %s repaired crossed daily rows: %s", day, ",".join(fixed["ts_code"]))
    kept = daily[~daily["ts_code"].isin(fixed["ts_code"])]
    return pd.concat([kept, fixed[kept.columns.intersection(fixed.columns)]], ignore_index=True)


def _write_log(cur: Any, day: date, dataset: str, status: str, rows: int, detail: str) -> None:
    cur.execute(
        """
        INSERT INTO qd_ashare_sync_log (trade_date, dataset, status, rows, detail)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (trade_date, dataset) DO UPDATE SET
          status = EXCLUDED.status, rows = EXCLUDED.rows,
          detail = EXCLUDED.detail, updated_at = NOW()
        """,
        (day, dataset, status, int(rows), str(detail)[:2000]),
    )


def load_st_intervals() -> dict[str, list[tuple[date, date | None]]]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT ts_code, name, start_date, end_date FROM qd_ashare_name_history")
        rows = cur.fetchall() or []
        cur.close()
    return build_st_intervals(rows)


def sync_trade_date(
    day: date,
    *,
    pro: Any = None,
    st_intervals: dict[str, list[tuple[date, date | None]]] | None = None,
    max_attempts: int = 3,
    retry_sleep: float = 20.0,
    fetcher: Callable[[date], dict[str, pd.DataFrame]] | None = None,
) -> dict[str, Any]:
    """Pull one trade date, validate coverage, upsert bars and suspensions.

    Incomplete pulls are retried; if still incomplete the rows are written but
    the day is logged ``partial`` so backfill/daily sync revisit it.
    ``fetcher`` replaces the Tushare pull (e.g. an offline archive).
    """
    if fetcher is None:
        pro = pro or _pro()
        fetcher = lambda d: fetch_day(pro, d)  # noqa: E731
    intervals = load_st_intervals() if st_intervals is None else st_intervals
    frames: dict[str, pd.DataFrame] = {}
    ok, detail = False, ""
    for attempt in range(max_attempts):
        try:
            frames = fetcher(day)
            ok, detail = check_day_completeness(frames)
        except Exception as exc:
            ok, detail = False, str(exc)
        if ok:
            break
        logger.warning("ashare sync %s attempt %s incomplete: %s", day, attempt + 1, detail)
        if attempt + 1 < max_attempts:
            time.sleep(retry_sleep)

    bar_rows = build_bar_rows(day, frames, st_codes_on(intervals, day))
    suspension_rows = build_suspension_rows(day, frames.get("suspend_d"))
    status = "ok" if ok else ("partial" if bar_rows else "failed")
    with get_db_connection() as db:
        cur = db.cursor()
        if bar_rows:
            cur.executemany(_BAR_UPSERT, bar_rows)
        if suspension_rows:
            cur.executemany(_SUSPENSION_UPSERT, suspension_rows)
        for name, frame in frames.items():
            _write_log(cur, day, name, "ok", len(frame), "")
        _write_log(cur, day, BARS_DATASET, status, len(bar_rows), detail)
        db.commit()
        cur.close()
    return {"trade_date": day.isoformat(), "status": status, "bars": len(bar_rows), "detail": detail}


def sync_reference_data(*, pro: Any = None, name_history_start_year: int = 1990) -> dict[str, int]:
    """Refresh ``stock_basic`` (listed + delisted + paused) and name history."""
    pro = pro or _pro()
    basics = [
        _query(pro, "stock_basic", list_status=status,
               fields="ts_code,name,market,exchange,industry,list_status,list_date,delist_date")
        for status in ("L", "D", "P")
    ]
    basic = pd.concat([f for f in basics if not f.empty], ignore_index=True) if any(
        not f.empty for f in basics
    ) else pd.DataFrame()

    today = datetime.now(SHANGHAI).date()
    name_frames = []
    for year in range(name_history_start_year, today.year + 1):
        name_frames.append(
            _query(pro, "namechange", start_date=f"{year}0101", end_date=f"{year}1231")
        )
    names = pd.concat([f for f in name_frames if not f.empty], ignore_index=True) if any(
        not f.empty for f in name_frames
    ) else pd.DataFrame()

    basic_rows = [
        (
            str(r.get("ts_code") or "").upper(),
            str(r.get("name") or "")[:64],
            str(r.get("market") or "")[:32],
            str(r.get("exchange") or "")[:16],
            str(r.get("industry") or "")[:64],
            str(r.get("list_status") or "")[:4],
            parse_ymd(r.get("list_date")),
            parse_ymd(r.get("delist_date")),
        )
        for r in basic.to_dict("records")
        if r.get("ts_code")
    ]
    name_rows = {
        (
            str(r.get("ts_code") or "").upper(),
            parse_ymd(r.get("start_date")),
            str(r.get("name") or "")[:64],
        ): (
            parse_ymd(r.get("end_date")),
            parse_ymd(r.get("ann_date")),
            str(r.get("change_reason") or "")[:64],
        )
        for r in names.to_dict("records")
        if r.get("ts_code") and r.get("name") and parse_ymd(r.get("start_date"))
    }
    with get_db_connection() as db:
        cur = db.cursor()
        if basic_rows:
            cur.executemany(
                """
                INSERT INTO qd_ashare_stock_basic
                (ts_code, name, market, exchange, industry, list_status, list_date, delist_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ts_code) DO UPDATE SET
                  name = EXCLUDED.name, market = EXCLUDED.market, exchange = EXCLUDED.exchange,
                  industry = EXCLUDED.industry, list_status = EXCLUDED.list_status,
                  list_date = EXCLUDED.list_date, delist_date = EXCLUDED.delist_date,
                  updated_at = NOW()
                """,
                basic_rows,
            )
        if name_rows:
            cur.executemany(
                """
                INSERT INTO qd_ashare_name_history
                (ts_code, start_date, name, end_date, ann_date, change_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (ts_code, start_date, name) DO UPDATE SET
                  end_date = EXCLUDED.end_date, ann_date = EXCLUDED.ann_date,
                  change_reason = EXCLUDED.change_reason
                """,
                [key + value for key, value in name_rows.items()],
            )
        db.commit()
        cur.close()
    return {"stock_basic": len(basic_rows), "name_history": len(name_rows)}


def rebuild_adj_factors(ts_code: str | None = None) -> int:
    """Recompute the backward-adjustment factor chain from ``pre_close``."""
    where = "WHERE ts_code = ?" if ts_code else ""
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(_REBUILD_ADJ_SQL.format(where=where), (ts_code,) if ts_code else None)
        updated = int(getattr(cur, "rowcount", 0) or 0)
        db.commit()
        cur.close()
    return updated


def _apply_incremental_adj(day: date) -> None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(_INCREMENTAL_ADJ_SQL, (day,))
        db.commit()
        cur.close()


def _days_with_status(start: date, end: date, statuses: tuple[str, ...]) -> set[date]:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            "SELECT trade_date FROM qd_ashare_sync_log WHERE dataset = ? AND trade_date BETWEEN ? AND ?"
            f" AND status IN ({','.join('?' for _ in statuses)})",
            (BARS_DATASET, start, end, *statuses),
        )
        rows = cur.fetchall() or []
        cur.close()
    return {parse_ymd(r["trade_date"]) for r in rows} - {None}


def backfill(
    start: date,
    end: date,
    *,
    resume: bool = True,
    refresh_reference: bool = True,
    rebuild_adjustment: bool = True,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Idempotent, resumable history load; ``resume`` skips days already ``ok``."""
    pro = _pro()
    reference = sync_reference_data(pro=pro) if refresh_reference else {}
    intervals = load_st_intervals()
    days = trade_dates(start, end, pro=pro)
    done = _days_with_status(start, end, ("ok",)) if resume else set()
    summary: dict[str, Any] = {"reference": reference, "days": len(days), "ok": 0, "partial": [], "failed": []}
    for index, day in enumerate(d for d in days if d not in done):
        result = sync_trade_date(day, pro=pro, st_intervals=intervals)
        if result["status"] == "ok":
            summary["ok"] += 1
        else:
            summary[result["status"]].append(day.isoformat())
        if progress is not None:
            progress({**result, "index": index + 1, "remaining": len(days) - len(done) - index - 1})
    summary["skipped"] = len(done)
    if rebuild_adjustment:
        summary["adj_updated"] = rebuild_adj_factors()
    return summary


def _send_alert(subject: str, body: str) -> None:
    recipient = (os.getenv("ASHARE_DATA_ALERT_EMAIL") or os.getenv("SMTP_USER") or "").strip()
    if not recipient:
        logger.warning("ashare data alert has no recipient: %s", subject)
        return
    from app.services.email_service import get_email_service

    ok, message = get_email_service().send_email(recipient, subject, f"<pre>{body}</pre>")
    if not ok:
        logger.warning("ashare data alert email failed: %s", message)


def run_daily_sync(now: datetime | None = None, *, lookback_days: int = 10) -> dict[str, Any]:
    """Catch up recent trade dates that are not ``ok``; alert once after 20:00.

    Safe to call on a short interval: it does nothing before 17:30 Shanghai on
    the current trade date and only re-pulls days that are missing or partial.
    """
    now = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    pro = _pro()
    today = now.date()
    days = trade_dates(today - timedelta(days=lookback_days), today, pro=pro)
    ready = (now.hour, now.minute) >= (DAILY_READY_HOUR, DAILY_READY_MINUTE)
    if days and days[-1] == today and not ready:
        days = days[:-1]
    if not days:
        return {"synced": [], "pending": []}

    done = _days_with_status(days[0], days[-1], ("ok",))
    todo = [d for d in days if d not in done]
    if not todo:
        return {"synced": [], "pending": []}

    sync_reference_data(pro=pro, name_history_start_year=today.year - 1)
    intervals = load_st_intervals()
    results = [sync_trade_date(d, pro=pro, st_intervals=intervals) for d in todo]
    # Re-pulling a day that precedes stored bars shifts every later factor.
    if done and todo[0] < max(done):
        rebuild_adj_factors()
    else:
        for day in todo:
            _apply_incremental_adj(day)
    ok_days = [r["trade_date"] for r in results if r["status"] == "ok"]
    if ok_days:
        from app.services.ashare_data.qlib_export import (
            export_configured_trade_dates,
            refresh_configured_instrument_end,
        )

        export_configured_trade_dates(ok_days)
        refresh_configured_instrument_end(max(ok_days))
    pending = [r for r in results if r["status"] != "ok"]

    if pending and now.hour >= ALERT_HOUR:
        alerted = _days_with_status(today, today, ("sent",))
        if today not in alerted:
            _send_alert(
                f"[QuantDinger] A股数据同步未完成 {today.isoformat()}",
                "\n".join(f"{r['trade_date']} {r['status']}: {r['detail']}" for r in pending),
            )
            with get_db_connection() as db:
                cur = db.cursor()
                _write_log(cur, today, ALERT_DATASET, "sent", len(pending), "")
                db.commit()
                cur.close()
    return {"synced": [r["trade_date"] for r in results], "pending": pending}
